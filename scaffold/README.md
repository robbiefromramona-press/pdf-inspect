# Press app scaffold

The shared shell and upload flow for the upload-based tools (PDF-Inspect today,
CoordXY and later apps next). Plain ES modules and one stylesheet, no build step.

| File | What it is |
| --- | --- |
| `scaffold.css` | Shell layout, buttons, tools menu, dropzone. Colors are CSS tokens in `:root`, so each app sets its own brand. |
| `scaffold.js` | `mountShell()` builds the top bar, step pills, sidebar, and stage. `createUploader()` builds the drag-and-drop + file-picker area and checks the file really is a PDF. Also exports the `h()` DOM helper and `formatBytes()`. |
| `apps.js` | The list of family apps shown in every app's **Tools** menu. |

## Using it

```html
<link rel="stylesheet" href="scaffold/scaffold.css">
<script type="module">
  import { mountShell, createUploader } from './scaffold/scaffold.js';

  const shell = mountShell(document.getElementById('app'), {
    app: { id: 'coordxy', name: 'COORD', accent: 'XY', tagline: 'PDF → field-ready points', logo: 'assets/logo-64.png' },
    steps: [{ id: 'upload', label: 'Upload' }, { id: 'coordsys', label: 'Coord. System' }, { id: 'place', label: 'Place Points' }],
  });
  shell.stage.append(createUploader({ onFile: (file) => loadPdf(file) }));
</script>
```

`shell.sidebar` and `shell.stage` are plain elements the app fills per step.
`shell.setStep(id)` moves the pills; `shell.setActions([...buttons])` sets the top-bar buttons.

## Where this lives (plan agreed 2026-09-16)

1. **Now:** vendored inside the PDF-Inspect repo. This copy is the reference.
2. **After PDF-Inspect is proven live:** migrate CoordXY onto it as its own
   deliberate change. CoordXY is currently one 750-line `index.html`, so this
   means replacing its top bar, sidebar shell, and file input with these modules
   while leaving its calibration, canvas, and point-placement code alone.
3. **Later:** if several apps share it, promote it to its own home and decide
   how apps pick up updates.

Nothing here touches CoordXY until step 2.
