// Builds the report object. This JSON-shaped object is the single source for
// everything the user sees or downloads: the on-screen table, CSV, TXT, print,
// the (hidden) JSON export, and the saved-inspection store.

import { APP, EMPTY_TEXT } from './config.js';

let schemaPromise = null;

export function loadSchema() {
  if (!schemaPromise) {
    schemaPromise = fetch(new URL('../engine/fields.json', import.meta.url)).then((r) => {
      if (!r.ok) throw new Error(`Couldn't load the field list (HTTP ${r.status})`);
      return r.json();
    });
  }
  return schemaPromise;
}

/**
 * @param schema     engine/fields.json
 * @param engineOut  report from engine/extract.py
 * @param file       { name, sizeBytes, sha256, lastModified }
 */
export function buildReport(schema, engineOut, file) {
  const rows = schema.fields.map((field) => {
    const r = engineOut.fields[field.key] || {
      status: 'error', summary: null, detail: [], notes: ['The engine returned nothing for this field.'], flags: [], data: null,
    };
    return {
      key: field.key,
      section: field.section,
      name: field.name,
      description: field.description,
      status: r.status,
      summary: r.summary,
      detail: r.detail,
      notes: r.notes,
      flags: r.flags,
      data: r.data,
    };
  });

  return {
    schemaVersion: 1,
    app: { id: APP.id, name: 'PDF-Inspect', version: APP.version },
    generatedAt: new Date().toISOString(),
    file: {
      name: file.name,
      sizeBytes: file.sizeBytes,
      sha256: file.sha256,
      lastModified: file.lastModified ? new Date(file.lastModified).toISOString() : null,
      pageCount: engineOut.pageCount,
    },
    engine: engineOut.engine,
    encrypted: engineOut.encrypted,
    locked: engineOut.locked,
    calibration: engineOut.calibration,
    sections: schema.sections,
    rows,
  };
}

/** The value column as plain text (CSV/TXT). */
export function valueText(row, { includeNotes = false } = {}) {
  if (row.status !== 'found') {
    const lines = [EMPTY_TEXT[row.status] || EMPTY_TEXT.absent];
    if (includeNotes) lines.push(...row.notes);
    return lines.join('\n');
  }
  const lines = [row.summary, ...row.detail];
  lines.push(...row.flags.map((f) => `Warning: ${f}`));
  if (includeNotes) lines.push(...row.notes.map((n) => `Note: ${n}`));
  return lines.join('\n');
}

export function counts(report) {
  const found = report.rows.filter((r) => r.status === 'found').length;
  const flags = report.rows.reduce((n, r) => n + r.flags.length, 0);
  return { found, total: report.rows.length, flags };
}
