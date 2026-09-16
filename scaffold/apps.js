// The family of tools shown in every app's Tools menu.
// Add an entry here when a new app launches; every app that uses the
// scaffold picks it up. Only list apps that are actually live.

export const APPS = [
  // URL is the planned Netlify site name — confirm once the site is created.
  { id: 'pdf-inspect', name: 'PDF-Inspect', blurb: 'See everything stored inside a PDF', url: 'https://pdf-inspect.netlify.app/' },
  { id: 'coordxy', name: 'CoordXY', blurb: 'PDF drawing to field-ready layout points', url: 'https://coordx-press.netlify.app/' },
  { id: 'qr-point', name: 'QR Point', blurb: 'Scan a QR code to pull up point data', url: 'https://qr-point.netlify.app/' },
  { id: 'csv-converter', name: 'CSV Converter', blurb: 'Reformat point files', url: 'https://press-csv-converter.netlify.app/' },
  { id: 'calculator', name: 'Calculator', blurb: 'Field math for layout', url: 'https://tst-calc.netlify.app/' },
];

export const SITES = [
  { id: 'bim-press', name: 'BIM-Press.com', url: 'https://bim-press.com/' },
  { id: 'totalstationtech', name: 'TotalStationTech.com', url: 'https://totalstationtech.com/' },
];
