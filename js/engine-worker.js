// Runs the Python extraction engine in a background thread so large drawing
// sets don't freeze the page. Loaded as a module worker (see engine.js).
//
// Boot sequence (first visit downloads ~13 MB; the browser caches it after):
//   1. load Pyodide (Python compiled to WebAssembly) from jsDelivr
//   2. unpack the vendored pypdf wheel from engine/
//   3. load engine/extract.py
//
// Messages in:  { type: 'boot' }
//               { type: 'inspect', id, bytes: ArrayBuffer }
// Messages out: { type: 'status', stage: 'booting'|'ready', label }
//               { type: 'progress', id, label, fraction }
//               { type: 'result', id, payload }   payload = JSON string from run_json
//               { type: 'error', id, message }

// Keep the version in these two lines in step.
import { loadPyodide } from 'https://cdn.jsdelivr.net/npm/pyodide@314.0.6/pyodide.mjs';
const PYODIDE_BASE = 'https://cdn.jsdelivr.net/npm/pyodide@314.0.6/';

const PYPDF_WHEEL = 'pypdf-6.18.1-py3-none-any.whl';
const ENGINE_DIR = new URL('../engine/', self.location.href);
const WORK_DIR = '/home/pyodide/pdfinspect';

let enginePromise = null;
let cryptographyLoaded = false;

function boot() {
  if (!enginePromise) {
    enginePromise = (async () => {
      self.postMessage({ type: 'status', stage: 'booting', label: 'Loading the inspection engine' });
      const [pyodide, wheel, source] = await Promise.all([
        loadPyodide({ indexURL: PYODIDE_BASE }),
        fetchOk(new URL(PYPDF_WHEEL, ENGINE_DIR)).then((r) => r.arrayBuffer()),
        fetchOk(new URL('extract.py', ENGINE_DIR)).then((r) => r.text()),
      ]);
      pyodide.FS.mkdirTree(WORK_DIR);
      pyodide.unpackArchive(wheel, 'zip', { extractDir: WORK_DIR });  // a wheel is a zip
      pyodide.FS.writeFile(`${WORK_DIR}/pdfinspect_extract.py`, source);
      pyodide.runPython(`import sys\nif ${JSON.stringify(WORK_DIR)} not in sys.path: sys.path.insert(0, ${JSON.stringify(WORK_DIR)})`);
      const engine = pyodide.pyimport('pdfinspect_extract');
      const versions = {
        pyodide: pyodide.version,
        pypdf: pyodide.runPython('import pypdf; pypdf.__version__'),
      };
      self.postMessage({ type: 'status', stage: 'ready', label: 'Engine ready', versions });
      return { pyodide, engine, versions };
    })();
    enginePromise.catch(() => { enginePromise = null; });  // allow a retry after a network failure
  }
  return enginePromise;
}

async function fetchOk(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Couldn't load ${url.pathname || url} (HTTP ${response.status})`);
  return response;
}

async function inspect(id, bytes) {
  const { pyodide, engine, versions } = await boot();
  const data = new Uint8Array(bytes);
  const progress = (label, fraction) => self.postMessage({ type: 'progress', id, label, fraction });

  let payload = engine.run_json(data, progress);
  let parsed = JSON.parse(payload);

  // AES-encrypted files need the cryptography package, which is only fetched when needed.
  if (!parsed.ok && parsed.needs === 'cryptography' && !cryptographyLoaded) {
    progress('Loading decryption support', 0.05);
    await pyodide.loadPackage('cryptography');
    cryptographyLoaded = true;
    payload = engine.run_json(data, progress);
    parsed = JSON.parse(payload);
  }
  if (parsed.ok) parsed.report.engine.runtime = `Pyodide ${versions.pyodide}`;
  self.postMessage({ type: 'result', id, payload: JSON.stringify(parsed) });
}

self.onmessage = (event) => {
  const msg = event.data || {};
  if (msg.type === 'boot') {
    boot().catch((err) => self.postMessage({ type: 'status', stage: 'failed', label: String(err && err.message || err) }));
  } else if (msg.type === 'inspect') {
    inspect(msg.id, msg.bytes).catch((err) => {
      self.postMessage({ type: 'error', id: msg.id, message: String(err && err.message || err) });
    });
  }
};
