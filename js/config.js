// App-wide settings.

export const APP = {
  id: 'pdf-inspect',
  name: 'PDF-',
  accent: 'Inspect',
  tagline: 'Look closer. Know more.',
  version: '0.1.0',
  logo: 'assets/logo-64.png',
};

export const FEATURES = {
  // The JSON download is fully built (see exporters.js) but kept out of the UI
  // for now. Flip to true to show the button.
  jsonExport: false,
};

// Browsers handle very large files, but Pyodide holds a full copy in memory.
export const MAX_FILE_BYTES = 500 * 1024 * 1024;

// Empty-state strings for the "Value Found in This PDF" column.
export const EMPTY_TEXT = {
  absent: '— not present —',
  error: '— could not be read —',
  locked: '— locked: password required —',
};
