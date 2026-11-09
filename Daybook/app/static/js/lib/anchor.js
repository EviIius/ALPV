// Text anchoring for highlights: find a quoted passage again inside a rendered page
// (a slide, a PDF page's text layer, or a document) and draw marks over it.
// Highlights are stored as quote + a little text before and after, so they survive
// zooming, re-rendering and small layout changes.

const SKIP = '.rd-ann, .ps-bu, .rd-skip, script, style';
const BLOCKS = new Set(['P', 'DIV', 'TD', 'TH', 'LI', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'BLOCKQUOTE', 'PRE', 'TR', 'SECTION', 'BR']);

export const norm = (s) => (s || '').replace(/[\s​ ]+/g, ' ').trim();

function blockOf(node) {
  let el = node.parentElement;
  while (el && !BLOCKS.has(el.tagName)) el = el.parentElement;
  return el;
}

// Walk the text under `root` into one whitespace-collapsed string, remembering
// which text node and offset each character came from.
export function textIndex(root) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode: (n) => (n.parentElement && n.parentElement.closest(SKIP) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
  });
  let s = '';
  const map = [];
  let space = true;
  let lastBlock = null;
  let lastParent = null;
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const block = blockOf(n);
    // Separate words that sit in different blocks (or different PDF text spans).
    if (s && !space && (block !== lastBlock || (n.parentElement !== lastParent && n.parentElement?.parentElement?.classList.contains('textLayer')))) {
      s += ' '; map.push([n, 0]); space = true;
    }
    const t = n.nodeValue;
    for (let i = 0; i < t.length; i++) {
      const ws = /[\s​ ]/.test(t[i]);
      if (ws) {
        if (space) continue;
        s += ' '; map.push([n, i]); space = true;
      } else {
        s += t[i]; map.push([n, i]); space = false;
      }
    }
    lastBlock = block;
    lastParent = n.parentElement;
  }
  return { s, map };
}

const commonPrefix = (a, b) => { let i = 0; while (i < a.length && i < b.length && a[i] === b[i]) i++; return i; };
const commonSuffix = (a, b) => { let i = 0; while (i < a.length && i < b.length && a[a.length - 1 - i] === b[b.length - 1 - i]) i++; return i; };

function rangeAt(idx, start, len) {
  const a = idx.map[start];
  const b = idx.map[start + len - 1];
  if (!a || !b) return null;
  const r = document.createRange();
  try {
    r.setStart(a[0], a[1]);
    r.setEnd(b[0], b[1] + 1);
  } catch { return null; }
  return r;
}

// The best place for an annotation's quote, using the text around it to pick between repeats.
export function anchorRange(idx, ann) {
  const q = norm(ann.quote);
  if (!q) return null;
  let hay = idx.s;
  let needle = q;
  let at = hay.indexOf(needle);
  if (at < 0) {
    hay = idx.s.toLowerCase();
    needle = q.toLowerCase();
    at = hay.indexOf(needle);
    if (at < 0) return null;
  }
  let best = at;
  let bestScore = -1;
  const pre = norm(ann.prefix);
  const suf = norm(ann.suffix);
  for (let i = at; i >= 0; i = hay.indexOf(needle, i + 1)) {
    const score = commonSuffix(idx.s.slice(Math.max(0, i - 60), i).trimEnd(), pre) + commonPrefix(idx.s.slice(i + q.length).trimStart(), suf);
    if (score > bestScore) { best = i; bestScore = score; }
  }
  return rangeAt(idx, best, q.length);
}

export function findRanges(idx, query, limit = 200) {
  const q = norm(query).toLowerCase();
  if (!q) return [];
  const hay = idx.s.toLowerCase();
  const out = [];
  for (let i = hay.indexOf(q); i >= 0 && out.length < limit; i = hay.indexOf(q, i + q.length)) {
    const r = rangeAt(idx, i, q.length);
    if (r) out.push(r);
  }
  return out;
}

// What to store for the current selection inside `root`.
export function capture(range, root) {
  const quote = norm(range.toString());
  const before = document.createRange();
  const after = document.createRange();
  try {
    before.setStart(root, 0);
    before.setEnd(range.startContainer, range.startOffset);
    after.setStart(range.endContainer, range.endOffset);
    after.setEnd(root, root.childNodes.length);
  } catch {
    return { quote, prefix: '', suffix: '' };
  }
  return { quote, prefix: norm(before.toString()).slice(-48), suffix: norm(after.toString()).slice(0, 48) };
}

// Draw annotation marks (and search matches) for one page into its overlay layer.
export function drawMarks(pageEl, anns, findQuery, activeFind) {
  const root = pageEl.querySelector('.rd-text') || pageEl;
  let layer = pageEl.querySelector(':scope > .rd-ann');
  if (!layer) {
    layer = document.createElement('div');
    layer.className = 'rd-ann';
    layer.setAttribute('aria-hidden', 'true');
    pageEl.appendChild(layer);
  }
  layer.textContent = '';
  if (!anns.length && !findQuery) return { found: 0, marks: [] };
  const base = pageEl.getBoundingClientRect();
  const idx = textIndex(root);
  const marks = [];
  const put = (rect, cls, annId) => {
    if (rect.width < 1 || rect.height < 1) return;
    const d = document.createElement('div');
    d.className = cls;
    d.style.left = `${rect.left - base.left}px`;
    d.style.top = `${rect.top - base.top}px`;
    d.style.width = `${rect.width}px`;
    d.style.height = `${rect.height}px`;
    if (annId) d.dataset.ann = annId;
    layer.appendChild(d);
    marks.push(d);
  };
  for (const a of anns) {
    const r = anchorRange(idx, a);
    if (!r) continue;
    for (const rect of r.getClientRects()) put(rect, `rd-hl c-${a.color}${a.comment ? ' has-c' : ''}`, a.id);
  }
  let found = 0;
  if (findQuery) {
    for (const r of findRanges(idx, findQuery)) {
      const cls = `rd-find${activeFind === found ? ' on' : ''}`;
      for (const rect of r.getClientRects()) put(rect, cls, null);
      found++;
    }
  }
  return { found, marks };
}

// The annotation (if any) under a click.
export function hitTest(pageEl, x, y) {
  const layer = pageEl.querySelector(':scope > .rd-ann');
  if (!layer) return null;
  for (const d of layer.querySelectorAll('[data-ann]')) {
    const r = d.getBoundingClientRect();
    if (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom) return d.dataset.ann;
  }
  return null;
}
