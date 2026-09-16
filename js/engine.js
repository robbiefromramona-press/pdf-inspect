// Main-thread client for js/engine-worker.js.

let worker = null;
let nextId = 1;
const pending = new Map();
const statusListeners = new Set();
let lastStatus = { stage: 'idle', label: 'Engine not loaded yet' };

function ensureWorker() {
  if (worker) return worker;
  worker = new Worker(new URL('./engine-worker.js', import.meta.url), { type: 'module' });
  worker.onmessage = ({ data: msg }) => {
    if (msg.type === 'status') {
      lastStatus = msg;
      statusListeners.forEach((fn) => fn(msg));
      return;
    }
    const job = pending.get(msg.id);
    if (!job) return;
    if (msg.type === 'progress') {
      job.onProgress?.(msg.label, msg.fraction);
    } else if (msg.type === 'result') {
      pending.delete(msg.id);
      job.resolve(JSON.parse(msg.payload));
    } else if (msg.type === 'error') {
      pending.delete(msg.id);
      job.reject(new Error(msg.message));
    }
  };
  worker.onerror = (event) => {
    const error = new Error(event.message || 'The inspection engine stopped unexpectedly.');
    pending.forEach((job) => job.reject(error));
    pending.clear();
    worker = null;  // a fresh worker is created on the next request
  };
  return worker;
}

/** Start downloading the engine in the background so it's ready when a file arrives. */
export function preloadEngine() {
  ensureWorker().postMessage({ type: 'boot' });
}

export function onEngineStatus(fn) {
  statusListeners.add(fn);
  fn(lastStatus);
  return () => statusListeners.delete(fn);
}

export function engineStatus() {
  return lastStatus;
}

/**
 * Inspect PDF bytes. The ArrayBuffer is transferred to the worker (it becomes
 * unusable here), so hash it first if you need to.
 * Resolves to { ok: true, report } or { ok: false, message }.
 */
export function inspectBytes(buffer, onProgress) {
  const id = nextId++;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject, onProgress });
    ensureWorker().postMessage({ type: 'inspect', id, bytes: buffer }, [buffer]);
  });
}
