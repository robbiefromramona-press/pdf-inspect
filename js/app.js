// PDF-Inspect: upload -> analyze -> report.

import { mountShell, createUploader, h, icon, formatBytes } from '../scaffold/scaffold.js';
import { APP, FEATURES, MAX_FILE_BYTES, EMPTY_TEXT } from './config.js';
import { preloadEngine, onEngineStatus, inspectBytes } from './engine.js';
import { loadSchema, buildReport, counts } from './report.js';
import { exportCsv, exportTxt, exportJson, printReport } from './exporters.js';
import { LocalInspectionStore, sha256Hex, LOCAL_RETENTION_DAYS } from './store.js';

const STEPS = [
  { id: 'upload', label: 'Upload' },
  { id: 'analyze', label: 'Analyze' },
  { id: 'report', label: 'Report' },
];

const shell = mountShell(document.getElementById('app'), { app: { ...APP, homeUrl: './' }, steps: STEPS });
const store = new LocalInspectionStore();
let runToken = 0;  // ignores results from a file the user has since replaced

// Same-site tools can ask whether a file (by SHA-256) was already inspected.
window.PDFInspect = { lookupCalibration: (sha256) => store.lookupCalibration(sha256) };

showUpload();
(window.requestIdleCallback || ((fn) => setTimeout(fn, 300)))(() => preloadEngine());

// ---------------------------------------------------------------------------
// Step 1: upload
// ---------------------------------------------------------------------------
async function showUpload(errorMessage) {
  runToken++;
  shell.setStep('upload');
  shell.setActions([]);

  const engineLine = h('div', { class: 'engine-line' });

  const sectionList = h('ul', { class: 'check-list' });
  loadSchema().then((schema) => {
    sectionList.replaceChildren(...schema.sections.map((sec) => {
      const n = schema.fields.filter((f) => f.section === sec.key).length;
      return h('li', {}, sec.name, h('span', {}, String(n)));
    }));
  }).catch(() => {});

  shell.sidebar.replaceChildren(
    h('h3', {}, '01 · Upload'),
    h('p', { class: 'sc-hint' }, 'Drop in any PDF: drawings, specs, submittals. PDF-Inspect reads the file’s internal structure and lists every piece of metadata, layer, and object data it can find.'),
    h('h4', { class: 'side-label' }, 'What gets checked'),
    sectionList,
    engineLine,
  );
  const unsubscribe = onEngineStatus((s) => {
    if (!engineLine.isConnected) return unsubscribe();  // the user moved on
    engineLine.dataset.stage = s.stage;
    engineLine.textContent = {
      idle: 'Engine: waiting to load',
      booting: 'Engine: loading in the background…',
      ready: 'Engine: ready',
      failed: `Engine failed to load: ${s.label}`,
    }[s.stage] || s.label;
  });

  const uploader = createUploader({
    maxBytes: MAX_FILE_BYTES,
    note: 'Your file is read inside this browser. It is never uploaded to a server.',
    onFile: inspectFile,
  });

  setChildren(shell.stage,
    h('section', { class: 'hero' },
      h('div', { class: 'hero-copy' },
        h('h1', {}, 'Look closer.', h('br'), h('span', {}, 'Know more.')),
        h('p', {}, 'Uncover the full story inside your PDFs: layers, scale, CAD and BIM object data, markups, fonts, attachments, and security settings.'),
      ),
      // Hero illustration goes here once the corrected brand asset is in assets/:
      // h('img', { class: 'hero-art', src: 'assets/hero.jpg', alt: '', width: 620, height: 366 }),
    ),
    errorMessage ? h('div', { class: 'banner bad', role: 'alert' }, errorMessage) : null,
    uploader,
  );
}

// ---------------------------------------------------------------------------
// Step 2: analyze
// ---------------------------------------------------------------------------
async function inspectFile(file) {
  const token = ++runToken;
  const bar = h('div', { class: 'progress-fill' });
  const percent = h('div', { class: 'progress-percent' }, '');
  const label = h('div', { class: 'progress-label' }, 'Reading file');
  const track = h('div', { class: 'progress-track is-indeterminate', role: 'progressbar', 'aria-valuemin': 0, 'aria-valuemax': 100 }, bar);

  shell.setStep('analyze');
  shell.setActions([h('button', { class: 'sc-btn secondary', type: 'button', onclick: () => showUpload() }, 'Cancel')]);
  shell.sidebar.replaceChildren(
    h('h3', {}, '02 · Analyze'),
    fileCard({ name: file.name, sizeBytes: file.size }),
    h('p', { class: 'sc-hint' }, 'Large drawing sets can take a minute. The page stays usable while this runs.'),
  );
  shell.stage.replaceChildren(h('section', { class: 'analyze-panel' },
    h('h2', {}, 'Analyzing PDF…'), percent, track, label));

  const setProgress = (text, fraction) => {
    if (token !== runToken) return;
    label.textContent = text;
    if (fraction == null) return;
    const pct = Math.max(0, Math.min(100, Math.round(fraction * 100)));
    track.classList.remove('is-indeterminate');
    track.setAttribute('aria-valuenow', pct);
    bar.style.width = `${pct}%`;
    percent.textContent = `${pct}%`;
  };

  const stopWatching = onEngineStatus((s) => {
    if (s.stage === 'booting') setProgress('Loading the inspection engine (about 13 MB, first visit only)…');
  });

  try {
    const buffer = await file.arrayBuffer();
    const sha256 = await sha256Hex(buffer);  // before the buffer is handed to the worker
    const schema = await loadSchema();

    // PAID TIER: an entitlement check belongs here once accounts and billing
    // exist. The app is intentionally ungated until then.

    const out = await inspectBytes(buffer, setProgress);
    if (token !== runToken) return;
    if (!out.ok) {
      showUpload(out.message || 'This file could not be inspected.');
      return;
    }
    const report = buildReport(schema, out.report, {
      name: file.name, sizeBytes: file.size, sha256, lastModified: file.lastModified,
    });
    store.save(report).catch((err) => console.warn('Saving the inspection in this browser failed:', err));
    showReport(report);
  } catch (err) {
    if (token !== runToken) return;
    console.error(err);
    showUpload(`Something went wrong while inspecting “${file.name}”: ${err.message}`);
  } finally {
    stopWatching();
  }
}

// ---------------------------------------------------------------------------
// Step 3: report
// ---------------------------------------------------------------------------
function showReport(report) {
  shell.setStep('report');
  shell.setActions([
    h('button', { class: 'sc-btn secondary', type: 'button', onclick: () => showUpload() }, icon('refresh'), 'Inspect another PDF'),
  ]);

  const { found, total, flags } = counts(report);
  const table = reportTable(report);
  const hideEmpty = h('input', { type: 'checkbox', id: 'hide-empty' });
  hideEmpty.addEventListener('change', () => table.classList.toggle('hide-empty', hideEmpty.checked));

  shell.sidebar.replaceChildren(
    h('h3', {}, '03 · Report'),
    fileCard(report.file),
    h('div', { class: 'stats' },
      stat(`${found}`, `of ${total} fields found`),
      stat(`${flags}`, flags === 1 ? 'warning' : 'warnings', flags ? 'warn' : ''),
    ),
    h('h4', { class: 'side-label' }, 'Download'),
    h('div', { class: 'download-buttons' },
      h('button', { class: 'sc-btn block', type: 'button', onclick: () => exportCsv(report) }, icon('download'), 'CSV'),
      h('button', { class: 'sc-btn secondary block', type: 'button', onclick: () => exportTxt(report) }, icon('download'), 'TXT'),
      h('button', { class: 'sc-btn secondary block', type: 'button', onclick: printReport }, icon('print'), 'Print / Save as PDF'),
      FEATURES.jsonExport
        ? h('button', { class: 'sc-btn secondary block', type: 'button', onclick: () => exportJson(report) }, icon('download'), 'JSON')
        : null,
    ),
    h('label', { class: 'toggle', for: 'hide-empty' }, hideEmpty, 'Show only fields with data'),
    h('p', { class: 'fineprint' }, `This report is kept in this browser for ${LOCAL_RETENTION_DAYS} days.`),
  );

  setChildren(shell.stage,
    h('header', { class: 'print-header' },
      h('div', { class: 'print-brand' }, 'PDF-Inspect report'),
      h('div', {}, report.file.name),
      h('div', {}, `${formatBytes(report.file.sizeBytes)}${report.file.pageCount != null ? ` · ${report.file.pageCount} pages` : ''} · generated ${new Date(report.generatedAt).toLocaleString()}`),
      h('div', { class: 'mono' }, `SHA-256 ${report.file.sha256}`),
    ),
    report.locked
      ? h('div', { class: 'banner bad', role: 'alert' }, 'This file needs a password to open, so only its security settings could be read.')
      : null,
    highlights(report),
    table,
  );
}

function highlights(report) {
  const items = [];
  const byKey = Object.fromEntries(report.rows.map((r) => [r.key, r]));
  if (report.calibration) {
    const scale = byKey.embedded_scale;
    const geo = byKey.geopdf;
    if (scale.status === 'found') items.push(h('li', { class: 'good' }, h('strong', {}, 'Scale is already calibrated: '), scale.summary));
    if (geo.status === 'found') items.push(h('li', { class: 'good' }, h('strong', {}, 'Georeferenced: '), geo.summary));
  }
  for (const row of report.rows) {
    for (const flag of row.flags) items.push(h('li', { class: 'warn' }, h('strong', {}, `${row.name}: `), flag));
  }
  if (!items.length) return null;
  return h('section', { class: 'highlights', 'aria-label': 'Highlights' }, h('h2', {}, 'Worth a look'), h('ul', {}, ...items));
}

function reportTable(report) {
  const table = h('table', { class: 'report' },
    h('caption', { class: 'visually-hidden' }, `Inspection report for ${report.file.name}`),
    h('colgroup', {}, h('col', { class: 'c-name' }), h('col', { class: 'c-desc' }), h('col', { class: 'c-value' })),
    h('thead', {}, h('tr', {},
      h('th', { scope: 'col' }, 'Data Name'),
      h('th', { scope: 'col' }, 'Data Description'),
      h('th', { scope: 'col' }, 'Value Found in This PDF'),
    )),
  );
  for (const section of report.sections) {
    const rows = report.rows.filter((r) => r.section === section.key);
    table.append(h('tbody', {},
      h('tr', { class: 'section-row' }, h('th', { colspan: 3, scope: 'colgroup' }, section.name)),
      ...rows.map(reportRow),
    ));
  }
  return h('div', { class: 'table-wrap' }, table);
}

function reportRow(row) {
  const value = h('td', { class: 'value', 'data-label': 'Value Found in This PDF' });
  if (row.status === 'found') {
    value.append(h('div', { class: 'summary' }, row.summary));
    for (const flag of row.flags) value.append(h('div', { class: 'flag' }, flag));
    if (row.detail.length) {
      value.append(h('details', {},
        h('summary', {}, `Show ${row.detail.length.toLocaleString()} ${row.detail.length === 1 ? 'item' : 'items'}`),
        h('div', { class: 'detail' }, ...row.detail.map((line) => h('div', {}, line))),
      ));
    }
  } else {
    value.append(h('div', { class: `empty ${row.status}` }, EMPTY_TEXT[row.status] || EMPTY_TEXT.absent));
  }
  for (const note of row.notes) value.append(h('div', { class: 'note' }, note));

  return h('tr', { 'data-status': row.status, id: `field-${row.key}` },
    h('th', { scope: 'row', class: 'name', 'data-label': 'Data Name' }, h('span', { class: `dot ${row.status}`, 'aria-hidden': 'true' }), row.name),
    h('td', { class: 'desc', 'data-label': 'Data Description' }, row.description),
    value,
  );
}

// replaceChildren() would print "null" for skipped optional pieces.
function setChildren(parent, ...children) {
  parent.replaceChildren(...children.filter(Boolean));
}

function fileCard(file) {
  return h('div', { class: 'file-card' },
    h('div', { class: 'file-name', title: file.name }, file.name),
    h('div', { class: 'file-meta' },
      formatBytes(file.sizeBytes),
      file.pageCount != null ? ` · ${file.pageCount.toLocaleString()} ${file.pageCount === 1 ? 'page' : 'pages'}` : ''),
  );
}

function stat(value, label, tone = '') {
  return h('div', { class: `stat ${tone}` }, h('div', { class: 'stat-value' }, value), h('div', { class: 'stat-label' }, label));
}
