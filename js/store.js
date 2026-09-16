// Saved inspections, and the calibration lookup other apps (CoordXY) use.
//
// THE CONTRACT
//   Key:    SHA-256 of the PDF's bytes (lowercase hex). Any app that has the
//           same file computes the same key, so no upload IDs need sharing.
//   Rows:   one per report field, shaped exactly like the Supabase table in
//           store/schema.sql, so moving to the account store later changes
//           only the adapter, not the callers.
//   Lookup: lookupCalibration(sha256) -> report.calibration or null.
//
// WHAT RUNS TODAY
//   LocalInspectionStore keeps inspections in this browser (IndexedDB). It is
//   per-browser and per-site: CoordXY on a different domain cannot read it.
//   Cross-app sharing needs the account-scoped store (store/schema.sql), which
//   needs user accounts, which don't exist yet.

// Retention (decided 2026-09-16): 30 days for now. Once memberships are
// active, account-store rows are kept for the life of the account instead
// (see store/schema.sql). The browser-only store always uses 30 days.
export const LOCAL_RETENTION_DAYS = 30;

const DB_NAME = 'pdf-inspect';
const DB_VERSION = 1;
const STORE = 'inspections';

export async function sha256Hex(buffer) {
  const digest = await crypto.subtle.digest('SHA-256', buffer);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

/** Report -> rows matching public.pdf_extractions. */
export function toRows(report, { userId = null, sourceApp = 'pdf-inspect', expiresAt = null } = {}) {
  const base = {
    user_id: userId,
    file_sha256: report.file.sha256,
    file_name: report.file.name,
    source_app: sourceApp,
    extracted_at: report.generatedAt,
    expires_at: expiresAt,
    engine_version: `pypdf ${report.engine.version}`,
  };
  const rows = report.rows.map((r) => ({
    ...base,
    field_key: r.key,
    status: r.status,
    value: { summary: r.summary, detail: r.detail, notes: r.notes, flags: r.flags, data: r.data },
  }));
  rows.push({
    ...base,
    field_key: 'calibration',
    status: report.calibration ? 'found' : 'absent',
    value: { data: report.calibration },
  });
  return rows;
}

function openDb() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(STORE)) {
        db.createObjectStore(STORE, { keyPath: 'sha256' }).createIndex('expiresAt', 'expiresAt');
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

function tx(db, mode, fn) {
  return new Promise((resolve, reject) => {
    const t = db.transaction(STORE, mode);
    const result = fn(t.objectStore(STORE));
    t.oncomplete = () => resolve(result?.result ?? result);
    t.onerror = () => reject(t.error);
    t.onabort = () => reject(t.error);
  });
}

export class LocalInspectionStore {
  async save(report) {
    const now = Date.now();
    const expiresAt = new Date(now + LOCAL_RETENTION_DAYS * 86400000).toISOString();
    const record = {
      sha256: report.file.sha256,
      fileName: report.file.name,
      savedAt: new Date(now).toISOString(),
      expiresAt,
      calibration: report.calibration,
      rows: toRows(report, { expiresAt }),
    };
    const db = await openDb();
    try {
      await tx(db, 'readwrite', (s) => s.put(record));
      await this.purgeExpired(db);
    } finally {
      db.close();
    }
    return record;
  }

  async get(sha256) {
    const db = await openDb();
    try {
      const record = await tx(db, 'readonly', (s) => s.get(sha256));
      if (!record || record.expiresAt < new Date().toISOString()) return null;
      return record;
    } finally {
      db.close();
    }
  }

  async lookupCalibration(sha256) {
    return (await this.get(sha256))?.calibration ?? null;
  }

  async purgeExpired(openDbHandle) {
    const db = openDbHandle || await openDb();
    try {
      await tx(db, 'readwrite', (s) => {
        const range = IDBKeyRange.upperBound(new Date().toISOString());
        s.index('expiresAt').openCursor(range).onsuccess = (e) => {
          const cursor = e.target.result;
          if (cursor) { cursor.delete(); cursor.continue(); }
        };
      });
    } finally {
      if (!openDbHandle) db.close();
    }
  }
}

/**
 * Convert a calibration scale entry into CoordXY's number.
 * CoordXY renders page 1 at a fixed CSS width and works in feet per canvas
 * pixel, with renderScale = canvas px per PDF point (1400 / page width in pt).
 * Returns null when the unit isn't a length CoordXY can use.
 */
export function feetPerCanvasPx(scaleEntry, renderScale) {
  const FEET_PER_UNIT = { ft: 1, feet: 1, "'": 1, in: 1 / 12, inch: 1 / 12, '"': 1 / 12, yd: 3, mi: 5280,
    m: 3.280839895, meter: 3.280839895, cm: 0.03280839895, mm: 0.003280839895, km: 3280.839895,
    'us-ft': 1200 / 3937 / 0.3048, usft: 1200 / 3937 / 0.3048 };
  const unit = String(scaleEntry?.unit || '').trim().toLowerCase();
  const factor = FEET_PER_UNIT[unit];
  if (!factor || !scaleEntry.unitsPerPoint || !renderScale) return null;
  // unitsPerPoint converts page coordinate units, the same units pdf.js
  // viewports (and so CoordXY's renderScale) are measured in.
  return (scaleEntry.unitsPerPoint * factor) / renderScale;
}
