# PDF-Inspect

Look closer. Know more. Upload a PDF and get a report of every piece of
metadata, layer data, and embedded CAD/BIM object data stored inside it.

Part of the BIM-Press / TotalStationTech tool family. Vanilla HTML/CSS/JS, no
build step, deployed GitHub → Netlify like the other tools.

## How it works

```
upload (scaffold/)  →  SHA-256 hash  →  Web Worker  →  report object  →  table / CSV / TXT / print / JSON
                                          │
                                          └─ Pyodide (Python in WebAssembly, from jsDelivr)
                                             + pypdf (vendored wheel in engine/)
                                             + engine/extract.py
```

- **The PDF never leaves the browser.** Python runs client-side in a background
  worker, so there's no server, no upload size limit, and no cold starts.
- **Why Python in the browser:** Netlify Functions only run JavaScript,
  TypeScript, and Go. Running pypdf through Pyodide keeps full access to the
  low-level structures (`/Measure`, GeoPDF, OCG layers, structure-tree object
  data, Bluebeam keys) without adding a second hosting provider.
- **First visit** downloads about 13 MB of engine files, which the browser then
  caches. The engine starts loading in the background as soon as the page opens.

## Files

| Path | What it is |
| --- | --- |
| `index.html`, `app.css` | The page |
| `js/app.js` | Upload → analyze → report flow |
| `js/engine.js`, `js/engine-worker.js` | Runs the Python engine off the main thread |
| `js/report.js` | Builds the report object everything else renders from |
| `js/exporters.js` | CSV, TXT, print, JSON downloads |
| `js/store.js` | Saved inspections + the calibration lookup contract for CoordXY |
| `js/config.js` | Feature flags (`FEATURES.jsonExport`), limits, empty-state text |
| `engine/extract.py` | The extraction engine (28 fields) |
| `engine/fields.json` | Report rows: names and descriptions shown in the table |
| `scaffold/` | Shared shell + upload UI (see its README) |
| `store/schema.sql` | Per-user Supabase table for when accounts exist. **Not deployed.** |
| `tests/` | Synthetic fixture PDFs and the engine test suite |

## Report columns

Exactly three: **Data Name**, **Data Description**, **Value Found in This PDF**.
Fields that don't apply show `— not present —`.

## JSON export

Fully built, hidden from the UI. Set `FEATURES.jsonExport = true` in
`js/config.js` to show the button.

## Tests

```bash
pip install pypdf cryptography
python tests/make_fixtures.py
python -m unittest discover tests
```

The fixtures are hand-built from the PDF specification. They prove the engine
reads those structures, not that every real Revit, AutoCAD, or Bluebeam export
lays them out the same way. Real sample files are the next thing to test against.

## Run locally

Any static server works, as long as it serves `.js` as JavaScript. For example:

```bash
python -m http.server 8765
```

## Not built yet (by decision)

- **Paid-tier gate.** No auth or billing exists on any site. The app runs
  ungated; the entitlement check goes where `js/app.js` says `PAID TIER`.
- **Account-scoped data store.** `store/schema.sql` is designed but not
  deployed; it needs user accounts. Today inspections are saved in the
  browser only.
- **Retention:** 30 days for now; life of the account once memberships are
  active. Built into `js/store.js` and `store/schema.sql`.
