// Press app scaffold: the shared shell (top bar, step pills, tools menu,
// sidebar, stage) and the PDF upload dropzone.
//
// Plain ES module, no build step. Everything user-supplied is inserted with
// textContent, never innerHTML, so file names can't inject markup.
//
//   import { mountShell, createUploader } from './scaffold/scaffold.js';

import { APPS, SITES } from './apps.js';

// Tiny DOM builder: h('div', { class: 'x', onclick: fn }, 'text', childNode)
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key.startsWith('on') && typeof value === 'function') el.addEventListener(key.slice(2), value);
    else if (key === 'class') el.className = value;
    else if (key === 'dataset') Object.assign(el.dataset, value);
    else el.setAttribute(key, value === true ? '' : String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

// Inline SVG icons (static markup only).
const ICONS = {
  upload: '<path d="M7 18a4.5 4.5 0 0 1-.6-8.96A6 6 0 0 1 18 8.5a4 4 0 0 1-.5 7.97" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/><path d="M12 12v9M8.5 15.5 12 12l3.5 3.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>',
  chevron: '<path d="m6 9 6 6 6-6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
  grid: '<path d="M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>',
  download: '<path d="M12 4v11m-4.5-4.5L12 15l4.5-4.5M5 20h14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
  print: '<path d="M7 9V4h10v5M7 17H5a1 1 0 0 1-1-1v-6a1 1 0 0 1 1-1h14a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1h-2M7 14h10v6H7z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>',
  refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7M20 5v6h-6" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
};

export function icon(name, className = '') {
  const span = document.createElement('span');
  span.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true" class="${className}">${ICONS[name] || ''}</svg>`;
  return span.firstElementChild;
}

/**
 * Build the app shell inside `root`.
 *
 * options.app      { id, name, accent, tagline, logo, homeUrl }
 *                  name + accent render as the two-tone wordmark ("PDF-" + "Inspect")
 * options.steps    [{ id, label }]  shown as numbered pills
 * options.actions  extra nodes for the right side of the top bar
 *
 * Returns { sidebar, stage, setStep(id), setActions(nodes) }.
 */
export function mountShell(root, { app, steps = [], actions = [] }) {
  const stepsEl = h('nav', { class: 'sc-steps', 'aria-label': 'Progress' });
  const actionsEl = h('div', { class: 'sc-actions' });
  const sidebar = h('aside', { class: 'sc-sidebar' });
  const stage = h('main', { class: 'sc-stage', id: 'main' });

  const brand = h('a', { class: 'sc-brand', href: app.homeUrl || './' },
    app.logo ? h('img', { src: app.logo, alt: '', width: 40, height: 40 }) : null,
    h('div', {},
      h('div', { class: 'sc-wordmark' }, app.name, h('span', {}, app.accent || '')),
      app.tagline ? h('div', { class: 'sc-tagline' }, app.tagline) : null,
    ),
  );

  root.replaceChildren(h('div', { class: 'sc-app' },
    h('header', { class: 'sc-topbar' }, brand, stepsEl, actionsEl),
    h('div', { class: 'sc-main' }, sidebar, stage),
  ));

  const toolsMenu = createToolsMenu(app.id);

  function setStep(currentId) {
    const current = steps.findIndex((s) => s.id === currentId);
    stepsEl.replaceChildren(...steps.map((s, i) => h('div', {
      class: 'sc-step' + (i < current ? ' is-done' : '') + (i === current ? ' is-active' : ''),
      'aria-current': i === current ? 'step' : null,
    }, `${i + 1}. ${s.label}`)));
  }

  function setActions(nodes) {
    actionsEl.replaceChildren(...nodes.filter(Boolean), toolsMenu);
  }

  setActions(actions);
  if (steps.length) setStep(steps[0].id);
  return { sidebar, stage, setStep, setActions };
}

function createToolsMenu(currentAppId) {
  const button = h('button', { class: 'sc-btn secondary', type: 'button', 'aria-haspopup': 'true', 'aria-expanded': 'false' },
    icon('grid'), 'Tools', icon('chevron'));
  const list = h('ul', { class: 'sc-menu-list', hidden: true },
    ...APPS.map((a) => h('li', {},
      h('a', { href: a.url, 'aria-current': a.id === currentAppId ? 'page' : null }, a.name, h('small', {}, a.blurb)))),
    h('li', { 'aria-hidden': 'true' }, h('div', { class: 'sc-menu-sep' })),
    ...SITES.map((s) => h('li', {}, h('a', { href: s.url }, s.name))),
  );
  const wrap = h('div', { class: 'sc-menu' }, button, list);

  const setOpen = (open) => {
    list.hidden = !open;
    button.setAttribute('aria-expanded', String(open));
  };
  button.addEventListener('click', (e) => { e.stopPropagation(); setOpen(list.hidden); });
  document.addEventListener('click', (e) => { if (!wrap.contains(e.target)) setOpen(false); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !list.hidden) { setOpen(false); button.focus(); } });
  return wrap;
}

/**
 * A drag-and-drop + file-picker upload area.
 *
 * options.accept     MIME type to accept (default application/pdf)
 * options.maxBytes   reject files larger than this (optional)
 * options.title      big line of text
 * options.note       small print under the button
 * options.onFile     called with the chosen File once it passes checks
 */
export function createUploader({ accept = 'application/pdf', maxBytes, title = 'Drag & drop a PDF file here', buttonLabel = 'Choose PDF File', note, onFile }) {
  const input = h('input', { type: 'file', accept, tabindex: '-1', 'aria-hidden': 'true' });
  const error = h('div', { class: 'sc-drop-error', role: 'alert' });
  const button = h('button', { class: 'sc-btn', type: 'button', onclick: () => input.click() }, buttonLabel);
  const zone = h('section', { class: 'sc-dropzone', 'aria-label': 'Upload a PDF' },
    icon('upload', 'sc-drop-icon'),
    h('div', { class: 'sc-drop-title' }, title),
    h('div', { class: 'sc-drop-or' }, 'or'),
    button,
    error,
    note ? h('div', { class: 'sc-drop-note' }, note) : null,
    input,
  );

  async function accept_(file) {
    error.textContent = '';
    if (!file) return;
    if (!(await looksLikePdf(file))) {
      error.textContent = `“${file.name}” isn't a PDF. Choose a .pdf file.`;
      return;
    }
    if (maxBytes && file.size > maxBytes) {
      error.textContent = `“${file.name}” is ${formatBytes(file.size)}. The limit is ${formatBytes(maxBytes)}.`;
      return;
    }
    onFile(file);
  }

  input.addEventListener('change', () => { accept_(input.files[0]); input.value = ''; });

  // Count enter/leave so moving over child elements doesn't flicker the highlight.
  let depth = 0;
  zone.addEventListener('dragenter', (e) => { e.preventDefault(); depth++; zone.classList.add('is-dragging'); });
  zone.addEventListener('dragover', (e) => { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; });
  zone.addEventListener('dragleave', () => { depth = Math.max(0, depth - 1); if (!depth) zone.classList.remove('is-dragging'); });
  zone.addEventListener('drop', (e) => {
    e.preventDefault();
    depth = 0;
    zone.classList.remove('is-dragging');
    accept_(e.dataTransfer?.files?.[0]);  // one PDF at a time
  });

  return zone;
}

// Check the file's first bytes for "%PDF-" rather than trusting the extension.
// The spec allows a little junk before the header, so look in the first 1024 bytes.
async function looksLikePdf(file) {
  const head = new Uint8Array(await file.slice(0, 1024).arrayBuffer());
  const text = String.fromCharCode(...head);
  return text.includes('%PDF-');
}

export function formatBytes(n) {
  if (n == null) return 'unknown size';
  if (n < 1024) return `${n.toLocaleString()} bytes`;
  const units = ['KB', 'MB', 'GB'];
  let i = -1;
  do { n /= 1024; i++; } while (n >= 1024 && i < units.length - 1);
  return `${n.toFixed(1)} ${units[i]}`;
}
