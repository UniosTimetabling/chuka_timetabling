// Works out what kind of thing a file/link is, so the UI can show the right
// icon (PDF, Word, link, ...) instead of a generic one.
//
// Detection order — the END of the URL/filename wins, because a link can
// itself be a PDF (https://.../timetable.pdf) and must get the PDF icon, not
// the link icon:
//   1. extension at the end of the URL path (query string / #hash ignored)
//   2. an extension inside a query value (…/download?file=timetable.pdf)
//   3. extension of the file name, when one is supplied
//   4. mime type, when one is supplied
//   5. the caller's fallback — 'link' for bare URLs, 'file' for attachments

const EXTENSION_KIND = {
  pdf: 'pdf',
  doc: 'word', docx: 'word', dot: 'word', dotx: 'word', rtf: 'word', odt: 'word',
  xls: 'excel', xlsx: 'excel', xlsm: 'excel', csv: 'excel', ods: 'excel',
  ppt: 'powerpoint', pptx: 'powerpoint', odp: 'powerpoint',
  jpg: 'image', jpeg: 'image', png: 'image', gif: 'image', webp: 'image', heic: 'image', bmp: 'image',
  zip: 'archive', rar: 'archive', '7z': 'archive',
  txt: 'text',
};

function kindFromExtension(segment) {
  if (!segment) return null;
  const clean = segment.trim().replace(/\/+$/, '');
  const dot = clean.lastIndexOf('.');
  if (dot === -1 || dot === clean.length - 1) return null;
  return EXTENSION_KIND[clean.slice(dot + 1).toLowerCase()] || null;
}

function safeDecode(value) {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

function kindFromUrl(url) {
  if (!url || typeof url !== 'string') return null;
  const [beforeHash] = url.split('#');
  const [path, query = ''] = beforeHash.split('?');

  const lastSegment = safeDecode(path.replace(/\/+$/, '').split('/').pop() || '');
  const fromPath = kindFromExtension(lastSegment);
  if (fromPath) return fromPath;

  for (const pair of query.split('&')) {
    const value = safeDecode(pair.split('=').slice(1).join('='));
    const fromQuery = kindFromExtension(value.split('/').pop());
    if (fromQuery) return fromQuery;
  }
  return null;
}

function kindFromMime(mimeType) {
  const mime = (mimeType || '').toLowerCase();
  if (!mime) return null;
  if (mime.includes('pdf')) return 'pdf';
  if (mime.includes('wordprocessingml') || mime.includes('msword') || mime.includes('rtf')) return 'word';
  if (mime.includes('spreadsheetml') || mime.includes('ms-excel') || mime.includes('csv')) return 'excel';
  if (mime.includes('presentationml') || mime.includes('ms-powerpoint')) return 'powerpoint';
  if (mime.startsWith('image/')) return 'image';
  if (mime.includes('zip')) return 'archive';
  if (mime.startsWith('text/')) return 'text';
  return null;
}

export function detectFileKind({ url, name, mimeType, fallback = 'file' } = {}) {
  return (
    kindFromUrl(url) ||
    kindFromExtension(name) ||
    kindFromMime(mimeType) ||
    fallback
  );
}

// Human label — used for accessibility text and small captions.
export const FILE_KIND_LABEL = {
  pdf: 'PDF',
  word: 'Word document',
  excel: 'Excel spreadsheet',
  powerpoint: 'PowerPoint',
  image: 'Image',
  archive: 'Archive',
  text: 'Text file',
  file: 'File',
  link: 'Link',
};
