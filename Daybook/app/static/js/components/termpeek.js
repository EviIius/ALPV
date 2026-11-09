// Select a term anywhere in Daybook (a note, a slide, a PDF page) to see what it
// means. Works off the glossary built from your own notes; nothing leaves the app.
import { html, useEffect, useState } from '/vendor/preact.js';
import { get } from '../lib/api.js';
import { onChange } from '../lib/store.js';

let map = null;
let loading = null;

export function resetGlossaryMap() { map = null; }

async function glossaryMap() {
  if (map) return map;
  if (!loading) {
    loading = get('/api/glossary/map').then((r) => {
      map = new Map();
      for (const t of r.terms || []) map.set(t.term.toLowerCase(), t);
      return map;
    }).finally(() => { loading = null; });
  }
  return loading;
}

function lookup(m, text) {
  const t = text.trim().replace(/^[("'“‘[]+|[)"'”’\].,;:!?]+$/g, '');
  if (!t || t.length > 60) return null;
  let hit = m.get(t.toLowerCase());
  let base = t;
  if (!hit && /[a-z]?s$/.test(t)) { base = t.slice(0, -1); hit = m.get(base.toLowerCase()); } // plurals: KPIs
  if (!hit) return null;
  // Acronyms only in capitals ("IT", not "it").
  if (/^[A-Z0-9&]{2,}$/.test(hit.term) && base !== hit.term) return null;
  return hit;
}

export function TermPeek() {
  const [peek, setPeek] = useState(null);
  useEffect(() => {
    let timer = null;
    let seq = 0;
    const onSel = () => {
      clearTimeout(timer);
      timer = setTimeout(async () => {
        const mine = ++seq;
        const sel = window.getSelection();
        const text = sel && sel.rangeCount && !sel.isCollapsed ? sel.toString() : '';
        if (!text || text.length > 60 || /\n/.test(text.trim())) { setPeek(null); return; }
        let m;
        try { m = await glossaryMap(); } catch { return; }
        if (mine !== seq) return;
        const hit = lookup(m, text);
        if (!hit) { setPeek(null); return; }
        const r = sel.getRangeAt(0).getBoundingClientRect();
        if (!r.width && !r.height) { setPeek(null); return; }
        setPeek({ ...hit, x: r.left + r.width / 2, bottom: r.bottom, top: r.top });
      }, 280);
    };
    const onKey = (e) => { if (e.key === 'Escape') setPeek(null); };
    const hide = () => { seq++; setPeek(null); };
    const onDown = (e) => { if (!e.target.closest || !e.target.closest('.term-peek')) hide(); };
    document.addEventListener('selectionchange', onSel);
    document.addEventListener('keydown', onKey);
    document.addEventListener('pointerdown', onDown, true);
    window.addEventListener('scroll', hide, true);
    window.addEventListener('hashchange', hide);
    window.addEventListener('resize', hide);
    const off = onChange((evt) => { if (evt.entity === 'glossary') resetGlossaryMap(); });
    return () => {
      clearTimeout(timer);
      document.removeEventListener('selectionchange', onSel);
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('pointerdown', onDown, true);
      window.removeEventListener('scroll', hide, true);
      window.removeEventListener('hashchange', hide);
      window.removeEventListener('resize', hide);
      off();
    };
  }, []);
  if (!peek) return null;
  const w = Math.min(320, window.innerWidth - 16);
  const left = Math.max(8, Math.min(window.innerWidth - w - 8, peek.x - w / 2));
  // Below the selection (the reader's highlight bar sits above it); above when there's no room.
  const below = peek.bottom + 130 < window.innerHeight;
  const style = `left:${left}px;width:${w}px;` + (below ? `top:${peek.bottom + 10}px` : `top:${peek.top - 10}px;transform:translateY(-100%)`);
  return html`<div class="term-peek" style=${style} role="tooltip" onMouseDown=${(e) => e.preventDefault()}>
    <div class="tp-head"><span class="tp-term">${peek.term}</span>
      <a class="tp-link" href=${`#/glossary?q=${encodeURIComponent(peek.term)}`} onClick=${() => setPeek(null)}>Glossary</a></div>
    <div class="tp-def">${peek.definition}</div>
  </div>`;
}
