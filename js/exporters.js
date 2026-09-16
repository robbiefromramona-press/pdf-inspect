// Downloads built from the report object.

import { valueText } from './report.js';
import { formatBytes } from '../scaffold/scaffold.js';

export function exportCsv(report) {
  const header = ['Data Name', 'Data Description', 'Value Found in This PDF'];
  const lines = [header, ...report.rows.map((r) => [r.name, r.description, valueText(r)])]
    .map((cells) => cells.map(csvCell).join(','));
  // BOM so Excel opens the UTF-8 correctly; CRLF between records per RFC 4180.
  download('﻿' + lines.join('\r\n') + '\r\n', `${baseName(report)}-inspect.csv`, 'text/csv;charset=utf-8');
}

export function exportTxt(report) {
  const out = [];
  const rule = '='.repeat(72);
  out.push('PDF-Inspect report', rule);
  out.push(`File:       ${report.file.name}`);
  out.push(`Size:       ${formatBytes(report.file.sizeBytes)}`);
  if (report.file.pageCount != null) out.push(`Pages:      ${report.file.pageCount}`);
  out.push(`SHA-256:    ${report.file.sha256}`);
  out.push(`Generated:  ${new Date(report.generatedAt).toLocaleString()}`);
  out.push(`Engine:     pypdf ${report.engine.version}${report.engine.runtime ? ` (${report.engine.runtime})` : ''}`);
  for (const section of report.sections) {
    out.push('', '', section.name.toUpperCase(), rule);
    for (const row of report.rows.filter((r) => r.section === section.key)) {
      out.push('', row.name, `  ${row.description}`);
      for (const line of valueText(row, { includeNotes: true }).split('\n')) out.push(`    ${line}`);
    }
  }
  download(out.join('\r\n') + '\r\n', `${baseName(report)}-inspect.txt`, 'text/plain;charset=utf-8');
}

export function exportJson(report) {
  download(JSON.stringify(report, null, 2), `${baseName(report)}-inspect.json`, 'application/json');
}

/** Opens every collapsed list so the printout is complete, then prints. */
export function printReport() {
  const closed = [...document.querySelectorAll('details:not([open])')];
  closed.forEach((d) => { d.open = true; });
  const restore = () => { closed.forEach((d) => { d.open = false; }); window.removeEventListener('afterprint', restore); };
  window.addEventListener('afterprint', restore);
  window.print();
}

// PDFs are untrusted input: a title like =HYPERLINK(...) would run as a
// formula in Excel. Prefix risky leading characters so they stay text.
function csvCell(value) {
  let s = String(value ?? '');
  if (/^[=+\-@\t\r]/.test(s)) s = `'${s}`;
  return `"${s.replace(/"/g, '""')}"`;
}

function baseName(report) {
  const name = (report.file.name || 'document').replace(/\.pdf$/i, '');
  return name.replace(/[\\/:*?"<>|\x00-\x1f]+/g, '_').slice(0, 120) || 'document';
}

function download(text, filename, type) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
