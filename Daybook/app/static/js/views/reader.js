// Reader: read a document in its original form — slides (with speaker notes and a
// presenter view), PDF pages, spreadsheet tabs, pictures, or a document's text —
// with search inside it, highlights and comments. Route: #/read/<note id>?p=<page>&q=<find>
import { html, useEffect, useMemo, useRef, useState } from '/vendor/preact.js';
import { del, get, post, put, qs } from '../lib/api.js';
import { emitChange, toast, toastError, useChanges } from '../lib/store.js';
import { navigate } from '../lib/router.js';
import { Empty, Icon, Markdown, Spinner, confirmDialog, openMenu, useMedia } from '../lib/ui.js';
import { capture, drawMarks, hitTest } from '../lib/anchor.js';

const COLORS = ['yellow', 'green', 'blue', 'pink', 'purple'];
const WORD = { slides: 'Slide', pdf: 'Page', sheet: 'Sheet', doc: 'Page', image: 'Picture' };
const touch = window.matchMedia('(hover: none) and (pointer: coarse)').matches;

let pdfjsPromise = null;
function pdfjs() {
  if (!pdfjsPromise) {
    pdfjsPromise = import('/vendor/pdfjs/pdf.min.mjs').then((lib) => {
      lib.GlobalWorkerOptions.workerSrc = '/vendor/pdfjs/pdf.worker.min.mjs';
      return lib;
    });
  }
  return pdfjsPromise;
}

function setPageInUrl(id, n, q) {
  const params = new URLSearchParams();
  if (n > 1) params.set('p', String(n));
  if (q) params.set('q', q);
  const s = params.toString();
  history.replaceState(null, '', `#/read/${encodeURIComponent(id)}${s ? `?${s}` : ''}`);
}

export function ReaderView({ route }) {
  const id = route.parts[1];
  const params = route.params;
  return id ? html`<${Reader} key=${id} id=${id} startPage=${Number(params.get('p')) || 1} startQuery=${params.get('q') || ''} />`
    : html`<div class="view"><${Empty} title="Nothing to read" icon="book-open">Open a deck, PDF or document from Notes or the Library.<//></div>`;
}

function Reader({ id, startPage, startQuery }) {
  const wide = useMedia('(min-width: 900px)');
  const roomy = useMedia('(min-width: 1180px)');
  const [m, setM] = useState(null);
  const [err, setErr] = useState(null);
  const [anns, setAnns] = useState([]);
  const [cur, setCur] = useState(startPage);
  const [count, setCount] = useState(0);
  const [panel, setPanel] = useState(null);
  const [zoom, setZoom] = useState(1);
  const [width, setWidth] = useState(0);
  const [present, setPresent] = useState(false);
  const [findOpen, setFindOpen] = useState(!!startQuery);
  const [findQ, setFindQ] = useState(startQuery);
  const [findHits, setFindHits] = useState(null);
  const [findAt, setFindAt] = useState(0);
  const [sel, setSel] = useState(null);
  const [pop, setPop] = useState(null);
  const [deck, setDeck] = useState(null);
  const scroller = useRef(null);
  const pages = useRef(new Map());
  const state = useRef({});
  state.current = { anns, findQ, cur, mode: m?.mode, findAt };

  const loadAnns = () => get(`/api/reader/${id}/annotations`).then((r) => setAnns(r.items)).catch(() => {});
  useEffect(() => {
    get(`/api/reader/${id}`).then((mm) => {
      setM(mm);
      if (mm.mode === 'slides' && roomy) setPanel('notes');
      document.title = `${mm.title} — Daybook`;
    }).catch((e) => setErr(e));
    loadAnns();
  }, [id]);
  useChanges(['annotations'], loadAnns, [id]);

  // Track the reading width (pages fit it).
  useEffect(() => {
    const el = scroller.current;
    if (!el) return undefined;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    setWidth(el.clientWidth);
    return () => ro.disconnect();
  }, [m?.mode]);

  // Marks: highlights and find matches, per page.
  const redrawTimer = useRef(null);
  const redraw = (only) => {
    const { anns: list, findQ: q, mode } = state.current;
    const entries = only ? [[only, pages.current.get(only)]] : [...pages.current.entries()];
    for (const [n, el] of entries) {
      if (!el || !el.isConnected || el.dataset.ready !== '1') continue;
      const mine = list.filter((a) => (mode === 'doc' ? true : a.page === n));
      drawMarks(el, mine, findOpen ? q : '', -1);
    }
  };
  const scheduleRedraw = () => { clearTimeout(redrawTimer.current); redrawTimer.current = setTimeout(() => redraw(), 60); };
  useEffect(() => { scheduleRedraw(); }, [anns, findQ, findOpen, zoom, width]);

  const onPageReady = (n, el) => {
    pages.current.set(n, el);
    el.dataset.ready = '1';
    redraw(n);
  };
  const registerPage = (n, el) => { if (el) pages.current.set(n, el); else pages.current.delete(n); };

  // Which page is in view.
  useEffect(() => {
    const el = scroller.current;
    if (!el) return undefined;
    let raf = 0;
    const onScroll = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        const top = el.getBoundingClientRect().top + el.clientHeight * 0.3;
        let best = null;
        for (const [n, p] of pages.current) {
          const r = p.getBoundingClientRect();
          if (r.top <= top && r.bottom > el.getBoundingClientRect().top) best = n;
        }
        if (best && best !== state.current.cur) {
          setCur(best);
          setPageInUrl(id, best, state.current.findQ && findOpen ? state.current.findQ : '');
        }
      });
    };
    el.addEventListener('scroll', onScroll, { passive: true });
    return () => { el.removeEventListener('scroll', onScroll); cancelAnimationFrame(raf); };
  }, [m?.mode, findOpen]);

  const goTo = (n, { flash } = {}) => {
    const total = count || 1;
    const k = Math.max(1, Math.min(total, n));
    const el = pages.current.get(k);
    if (el) el.scrollIntoView({ block: 'start', behavior: Math.abs(k - state.current.cur) > 3 ? 'auto' : 'smooth' });
    setCur(k);
    setPageInUrl(id, k, findOpen ? findQ : '');
    if (flash) setTimeout(() => {
      const mark = pages.current.get(k)?.querySelector(`[data-ann="${flash}"]`);
      if (mark) { mark.scrollIntoView({ block: 'center', behavior: 'smooth' }); mark.classList.add('flash'); setTimeout(() => mark.classList.remove('flash'), 1400); }
    }, 350);
  };

  // Jump to the starting page once pages exist.
  const jumped = useRef(false);
  useEffect(() => {
    if (jumped.current || !count) return;
    if (startPage > 1 && pages.current.get(startPage)) {
      jumped.current = true;
      setTimeout(() => goTo(startPage), 30);
    } else if (startPage <= 1) jumped.current = true;
  }, [count, width]);

  // Selection → highlight toolbar.
  useEffect(() => {
    let t = null;
    const check = () => {
      const s = window.getSelection();
      if (!s || s.isCollapsed || !s.rangeCount) { setSel(null); return; }
      const r = s.getRangeAt(0);
      const elOf = (n) => (n.nodeType === 1 ? n : n.parentElement);
      const a = elOf(r.startContainer)?.closest('.rd-page');
      const b = elOf(r.endContainer)?.closest('.rd-page');
      if (!a || a !== b || !scroller.current?.contains(a) || !r.toString().trim()) { setSel(null); return; }
      const rect = r.getBoundingClientRect();
      setSel({ page: Number(a.dataset.page) || null, el: a, range: r.cloneRange(), x: rect.left + rect.width / 2, top: rect.top, bottom: rect.bottom });
    };
    const onSel = () => { clearTimeout(t); t = setTimeout(check, touch ? 350 : 120); };
    document.addEventListener('selectionchange', onSel);
    return () => { document.removeEventListener('selectionchange', onSel); clearTimeout(t); };
  }, []);

  const addMark = async (color, withComment = false) => {
    if (!sel) return;
    const root = sel.el.querySelector('.rd-text') || sel.el;
    const c = capture(sel.range, root);
    if (!c.quote) return;
    try {
      const a = await post(`/api/reader/${id}/annotations`, { page: m.mode === 'doc' ? 1 : sel.page, ...c, color });
      window.getSelection().removeAllRanges();
      setSel(null);
      setAnns((list) => [...list, a]);
      if (withComment) setTimeout(() => openPopFor(a.id), 120);
    } catch (e) { toastError(e); }
  };

  const openPopFor = (aid, at) => {
    const a = state.current.anns.find((x) => x.id === aid);
    if (!a) return;
    let x = at?.x;
    let y = at?.y;
    if (x == null) {
      const mark = scroller.current?.querySelector(`[data-ann="${aid}"]`);
      const r = mark?.getBoundingClientRect();
      x = r ? r.left + r.width / 2 : window.innerWidth / 2;
      y = r ? r.bottom : window.innerHeight / 2;
    }
    setPop({ id: aid, x, y });
  };

  const onPagesClick = (e) => {
    if (e.target.closest('a[href]')) return;
    const s = window.getSelection();
    if (s && !s.isCollapsed) return;
    const pageEl = e.target.closest('.rd-page');
    if (!pageEl) return;
    const aid = hitTest(pageEl, e.clientX, e.clientY);
    if (aid) openPopFor(aid, { x: e.clientX, y: e.clientY + 10 });
  };

  // Find inside the document.
  useEffect(() => {
    if (!findOpen || !findQ.trim() || !m) { setFindHits(null); return undefined; }
    const t = setTimeout(async () => {
      if (m.mode === 'doc' || m.mode === 'image') {
        setFindHits(null);
        return;
      }
      try { setFindHits((await get(`/api/reader/${id}/find${qs({ q: findQ.trim() })}`)).hits); setFindAt(0); } catch { setFindHits([]); }
    }, 250);
    return () => clearTimeout(t);
  }, [findQ, findOpen, m?.mode]);
  const findStep = (dir) => {
    if (findHits && findHits.length) {
      const pagesWithHits = findHits.map((h) => h.page);
      const nextIdx = dir > 0 ? pagesWithHits.findIndex((p) => p > cur) : pagesWithHits.map((p) => p < cur).lastIndexOf(true);
      const k = nextIdx >= 0 ? nextIdx : (dir > 0 ? 0 : pagesWithHits.length - 1);
      setFindAt(k);
      if (m.mode === 'sheet') return;
      goTo(pagesWithHits[k]);
      setTimeout(() => pages.current.get(pagesWithHits[k])?.querySelector('.rd-find')?.scrollIntoView({ block: 'center' }), 400);
      return;
    }
    const marks = [...(scroller.current?.querySelectorAll('.rd-find') || [])];
    if (!marks.length) return;
    const k = (findAt + dir + marks.length) % marks.length;
    setFindAt(k);
    marks.forEach((x, i) => x.classList.toggle('on', i === k));
    marks[k].scrollIntoView({ block: 'center', behavior: 'smooth' });
  };

  // Keyboard: arrows/page keys move between pages, ⌘F opens find, Esc closes things.
  useEffect(() => {
    const onKey = (e) => {
      if (present) return;
      const typing = /INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName || '') || document.activeElement?.isContentEditable;
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'f') { e.preventDefault(); setFindOpen(true); setTimeout(() => document.getElementById('rd-find')?.focus(), 30); return; }
      if (typing) { if (e.key === 'Escape') { setFindOpen(false); document.activeElement.blur(); } return; }
      if (e.key === 'Escape') { if (pop) setPop(null); else if (findOpen) setFindOpen(false); return; }
      if (e.key === 'p' && state.current.mode === 'slides' && !e.metaKey && !e.ctrlKey) { e.preventDefault(); setPresent(true); return; }
      if (state.current.mode !== 'slides' && state.current.mode !== 'pdf') return;
      if (['PageDown', 'ArrowRight'].includes(e.key) || (e.key === ' ' && state.current.mode === 'slides')) { e.preventDefault(); goTo(state.current.cur + 1); }
      if (['PageUp', 'ArrowLeft'].includes(e.key)) { e.preventDefault(); goTo(state.current.cur - 1); }
      if (e.key === 'Home') goTo(1);
      if (e.key === 'End') goTo(count);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [present, pop, findOpen, count]);

  if (err) {
    return html`<div class="view"><${Empty} title=${err.status === 423 ? 'This note is locked' : "This can't be shown in the reader"} icon=${err.status === 423 ? 'lock' : 'book-open'}>
      ${err.message}<div style="margin-top:12px"><a class="btn btn-sm" href=${`#/notes/${id}`}>Open the note</a></div><//></div>`;
  }
  if (!m) return html`<div class="view"><${Spinner} label="Opening…" /></div>`;
  if (m.mode === 'mockup') { navigate(`#/notes/${id}`, { replace: true }); return null; }

  const word = WORD[m.mode] || 'Page';
  const paged = m.mode === 'slides' || m.mode === 'pdf';
  const showRail = paged && roomy && m.mode === 'slides';
  const panelOpen = !!panel;
  const back = () => { if (history.length > 1) history.back(); else navigate(`#/notes/${id}`); };
  const more = (e) => openMenu(e.currentTarget, [
    { label: 'Open as a note', icon: 'file-text', onSelect: () => navigate(`#/notes/${id}`) },
    m.lib_path ? { label: 'Open the original', icon: 'square-arrow-out-up-right', onSelect: () => post(`/api/library/open/${id}`).catch(toastError) } : null,
    m.lib_path ? { label: 'Show in Finder', icon: 'folder-open', onSelect: () => post(`/api/library/reveal/${id}`).catch(toastError) } : null,
    m.raw ? { label: 'Download the original', icon: 'download', onSelect: () => { location.href = `${m.raw}?download=true`; } } : null,
    { label: 'Save highlights as a note', icon: 'notebook-pen', onSelect: () => saveHighlightsNote(id, setM), disabled: !anns.length },
  ].filter(Boolean), { align: 'end' });

  return html`<div class=${`reader mode-${m.mode}${panelOpen ? ' with-panel' : ''}${showRail ? ' with-rail' : ''}`}>
    <header class="rd-bar">
      <button class="icon-btn" type="button" title="Back" onClick=${back}><${Icon} name="chevron-left" /></button>
      <div class="rd-title">
        <div class="t" title=${m.title}>${m.title}</div>
        <div class="s">${m.filename || 'Note'}${count && paged ? ` · ${count} ${word.toLowerCase()}s` : ''}</div>
      </div>
      ${paged && count ? html`<div class="rd-pager">
        <button class="icon-btn" type="button" title=${`Previous ${word.toLowerCase()}`} disabled=${cur <= 1} onClick=${() => goTo(cur - 1)}><${Icon} name="chevron-up" /></button>
        <span class="num"><input aria-label=${word} type="text" inputmode="numeric" value=${cur} onChange=${(e) => goTo(Number(e.target.value) || 1)} /> / ${count}</span>
        <button class="icon-btn" type="button" title=${`Next ${word.toLowerCase()}`} disabled=${cur >= count} onClick=${() => goTo(cur + 1)}><${Icon} name="chevron-down" /></button>
      </div>` : null}
      <div class="rd-tools">
        <button class="icon-btn" type="button" title="Find in this document" aria-pressed=${findOpen ? 'true' : 'false'}
          onClick=${() => { setFindOpen(!findOpen); setTimeout(() => document.getElementById('rd-find')?.focus(), 30); }}><${Icon} name="search" /></button>
        ${wide && m.mode !== 'sheet' ? html`<button class="icon-btn" type="button" title="Smaller" onClick=${() => setZoom((z) => Math.max(0.5, +(z - 0.15).toFixed(2)))}><${Icon} name="minus" /></button>
          <button class="btn btn-sm btn-quiet zoom-label" type="button" title="Fit to width" onClick=${() => setZoom(1)}>${Math.round(zoom * 100)}%</button>
          <button class="icon-btn" type="button" title="Larger" onClick=${() => setZoom((z) => Math.min(2.5, +(z + 0.15).toFixed(2)))}><${Icon} name="plus" /></button>` : null}
        ${m.mode === 'slides' ? html`<button class="icon-btn" type="button" title="Present (P)" onClick=${() => setPresent(true)}><${Icon} name="presentation" /></button>` : null}
        ${m.mode === 'slides' ? html`<button class="icon-btn" type="button" title="Speaker notes" aria-pressed=${panel === 'notes' ? 'true' : 'false'}
          onClick=${() => setPanel(panel === 'notes' ? null : 'notes')}><${Icon} name="message-square-text" /></button>` : null}
        ${m.mode !== 'sheet' && m.mode !== 'image' ? html`<button class="icon-btn hl-btn" type="button" title="Highlights and comments" aria-pressed=${panel === 'marks' ? 'true' : 'false'}
          onClick=${() => setPanel(panel === 'marks' ? null : 'marks')}><${Icon} name="highlighter" />${anns.length ? html`<span class="badge">${anns.length}</span>` : null}</button>` : null}
        <button class="icon-btn" type="button" title="More" onClick=${more}><${Icon} name="ellipsis" /></button>
      </div>
    </header>
    ${findOpen ? html`<div class="rd-findbar">
      <${Icon} name="search" />
      <input id="rd-find" type="search" placeholder=${m.mode === 'sheet' ? 'Filter rows' : `Find in this ${m.mode === 'slides' ? 'deck' : 'document'}`} value=${findQ}
        onInput=${(e) => setFindQ(e.target.value)} onKeyDown=${(e) => { if (e.key === 'Enter') findStep(e.shiftKey ? -1 : 1); }} />
      ${findHits ? html`<span class="muted rd-findcount">${findHits.length ? `${findHits.reduce((n, h) => n + h.count, 0)} on ${findHits.length} ${findHits.length === 1 ? word.toLowerCase() : `${word.toLowerCase()}s`}` : 'No matches'}</span>` : null}
      ${m.mode !== 'sheet' ? html`<button class="icon-btn" type="button" title="Previous match (Shift+Enter)" onClick=${() => findStep(-1)}><${Icon} name="chevron-up" /></button>
        <button class="icon-btn" type="button" title="Next match (Enter)" onClick=${() => findStep(1)}><${Icon} name="chevron-down" /></button>` : null}
      <button class="icon-btn" type="button" title="Close" onClick=${() => { setFindOpen(false); setFindQ(''); }}><${Icon} name="x" /></button>
    </div>` : null}
    <div class="rd-body">
      ${showRail && deck ? html`<${SlideRail} deck=${deck} cur=${cur} onPick=${goTo} anns=${anns} />` : null}
      <div class="rd-scroll" ref=${scroller} onClick=${onPagesClick}>
        ${m.mode === 'slides' ? html`<${SlidesBody} id=${id} width=${width} zoom=${zoom} register=${registerPage} onReady=${onPageReady} onLoaded=${(d) => { setDeck(d); setCount(d.slides.length); }} />` : null}
        ${m.mode === 'pdf' ? html`<${PdfBody} m=${m} width=${width} zoom=${zoom} register=${registerPage} onReady=${onPageReady} onCount=${setCount} scroller=${scroller} />` : null}
        ${m.mode === 'sheet' ? html`<${SheetBody} id=${id} filter=${findOpen ? findQ : ''} hits=${findHits} />` : null}
        ${m.mode === 'image' ? html`<div class="rd-image" style=${`width:${Math.round(Math.max(200, (width - 48) * zoom))}px`}><img src=${m.raw} alt=${m.title} /></div>` : null}
        ${m.mode === 'doc' ? html`<${DocBody} id=${id} width=${width} zoom=${zoom} register=${registerPage} onReady=${onPageReady} onCount=${setCount} />` : null}
      </div>
      ${panelOpen ? html`<${SidePanel} which=${panel} onClose=${() => setPanel(null)} m=${m} deck=${deck} cur=${cur} anns=${anns} word=${word}
        findHits=${findHits} onGo=${goTo} onOpen=${(a) => { goTo(a.page || 1, { flash: a.id }); }} onSaveNote=${() => saveHighlightsNote(id, setM)} wide=${wide} />` : null}
    </div>
    ${sel ? html`<${SelectionBar} sel=${sel} onMark=${addMark} />` : null}
    ${pop ? html`<${AnnotationPop} pop=${pop} ann=${anns.find((a) => a.id === pop.id)} onClose=${() => setPop(null)}
      onChange=${(a) => setAnns((list) => list.map((x) => (x.id === a.id ? a : x)))}
      onDelete=${(aid) => { setAnns((list) => list.filter((x) => x.id !== aid)); setPop(null); }} />` : null}
    ${present && deck ? html`<${PresentMode} deck=${deck} start=${cur} onClose=${(n) => { setPresent(false); if (n) goTo(n); }} />` : null}
  </div>`;
}

async function saveHighlightsNote(id, setM) {
  try {
    const n = await post(`/api/reader/${id}/highlights-note`);
    emitChange({ entity: 'notes' });
    setM((m) => ({ ...m, highlights_note: n.id }));
    toast(`Saved “${n.title}”${n.lib_path ? ` to ${n.lib_path}` : ''}`, { action: { label: 'Open', run: () => navigate(`#/notes/${n.id}`) } });
  } catch (e) { toastError(e); }
}

// ---------------------------------------------------------------------------
// Slides
// ---------------------------------------------------------------------------
function SlidesBody({ id, width, zoom, register, onReady, onLoaded }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    get(`/api/reader/${id}/slides`).then((d) => { setData(d); onLoaded(d); }).catch(setErr);
  }, [id]);
  if (err) return html`<div class="rd-msg"><${Empty} title="This deck couldn't be drawn" icon="presentation">${err.message}<//></div>`;
  if (!data) return html`<div class="rd-msg"><${Spinner} label="Drawing slides…" /></div>`;
  const avail = Math.max(240, width - (width > 700 ? 64 : 20));
  const w = Math.round(Math.min(avail, 1280 * 1.15) * zoom);
  const scale = w / data.width;
  const h = Math.round(data.height * scale);
  return html`<div class="rd-pages">
    ${data.warnings?.length ? html`<div class="rd-warn">${data.warnings.join(' ')}</div>` : null}
    ${data.slides.map((s) => html`<${SlidePage} key=${s.n} s=${s} w=${w} h=${h} scale=${scale} register=${register} onReady=${onReady} />`)}
  </div>`;
}

function SlidePage({ s, w, h, scale, register, onReady }) {
  const ref = useRef(null);
  useEffect(() => {
    register(s.n, ref.current);
    onReady(s.n, ref.current);
    return () => register(s.n, null);
  }, [s.n, w]);
  return html`<section class=${`rd-page rd-slide${s.hidden ? ' is-hidden' : ''}`} data-page=${s.n} ref=${ref} style=${`width:${w}px;height:${h}px`}
      aria-label=${`Slide ${s.n}: ${s.title}`}>
    <div class="rd-text" style=${`transform:scale(${scale})`} dangerouslySetInnerHTML=${{ __html: s.html }}></div>
    <span class="rd-num">${s.n}${s.hidden ? ' · hidden' : ''}</span>
  </section>`;
}

function SlideRail({ deck, cur, onPick, anns }) {
  const ref = useRef(null);
  useEffect(() => {
    const el = ref.current?.querySelector(`[data-thumb="${cur}"]`);
    if (el) el.scrollIntoView({ block: 'nearest' });
  }, [cur]);
  const tw = 168;
  const sc = tw / deck.width;
  const marked = new Set(anns.map((a) => a.page));
  return html`<nav class="rd-rail" ref=${ref} aria-label="Slides">
    ${deck.slides.map((s) => html`<button type="button" class="rd-thumb" data-thumb=${s.n} aria-current=${s.n === cur ? 'true' : 'false'} onClick=${() => onPick(s.n)} title=${s.title}>
      <span class="n">${s.n}${marked.has(s.n) ? html`<i class="dot" title="Has highlights"></i>` : null}</span>
      <span class="frame" style=${`width:${tw}px;height:${Math.round(deck.height * sc)}px`}>
        <span class="inner" style=${`transform:scale(${sc})`} dangerouslySetInnerHTML=${{ __html: s.html }}></span>
      </span>
    </button>`)}
  </nav>`;
}

function PresentMode({ deck, start, onClose }) {
  const [n, setN] = useState(start);
  const [notes, setNotes] = useState(false);
  const [size, setSize] = useState({ w: window.innerWidth, h: window.innerHeight });
  const ref = useRef(null);
  const total = deck.slides.length;
  const go = (k) => setN((x) => Math.max(1, Math.min(total, typeof k === 'function' ? k(x) : k)));
  useEffect(() => {
    const el = ref.current;
    el?.focus();
    if (el?.requestFullscreen && !touch) el.requestFullscreen().catch(() => {});
    const onResize = () => setSize({ w: window.innerWidth, h: window.innerHeight });
    const onKey = (e) => {
      if (['ArrowRight', 'ArrowDown', 'PageDown', ' ', 'Enter'].includes(e.key)) { e.preventDefault(); go((x) => x + 1); }
      else if (['ArrowLeft', 'ArrowUp', 'PageUp', 'Backspace'].includes(e.key)) { e.preventDefault(); go((x) => x - 1); }
      else if (e.key === 'Home') go(1);
      else if (e.key === 'End') go(total);
      else if (e.key.toLowerCase() === 's' || e.key.toLowerCase() === 'n') setNotes((v) => !v);
      else if (e.key === 'Escape') close();
    };
    const onFs = () => { if (!document.fullscreenElement && !touch) setTimeout(() => onCloseRef.current(), 0); };
    // Capture phase, so Daybook's global shortcuts (n = new, / = search) don't fire while presenting.
    const onKeyCapture = (e) => { if (e.metaKey || e.ctrlKey || e.altKey) return; e.stopPropagation(); onKey(e); };
    window.addEventListener('resize', onResize);
    window.addEventListener('keydown', onKeyCapture, true);
    document.addEventListener('fullscreenchange', onFs);
    return () => { window.removeEventListener('resize', onResize); window.removeEventListener('keydown', onKeyCapture, true); document.removeEventListener('fullscreenchange', onFs); };
  }, []);
  const onCloseRef = useRef(null);
  const nRef = useRef(n);
  nRef.current = n;
  const close = () => {
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    onClose(nRef.current);
  };
  onCloseRef.current = () => onClose(nRef.current);
  const s = deck.slides[n - 1];
  const availH = size.h - (notes ? Math.min(220, size.h * 0.28) : 0) - 8;
  const scale = Math.min(size.w / deck.width, availH / deck.height);
  const touchStart = useRef(null);
  return html`<div class="rd-present" ref=${ref} tabindex="-1" role="dialog" aria-label="Presenting"
      onTouchStart=${(e) => { touchStart.current = e.touches[0].clientX; }}
      onTouchEnd=${(e) => { const dx = e.changedTouches[0].clientX - (touchStart.current ?? 0); if (Math.abs(dx) > 40) go((x) => x + (dx < 0 ? 1 : -1)); touchStart.current = null; }}
      onClick=${(e) => { if (e.target.closest('button, a')) return; go((x) => x + (e.clientX > window.innerWidth / 3 ? 1 : -1)); }}>
    <div class="stage" style=${`width:${Math.round(deck.width * scale)}px;height:${Math.round(deck.height * scale)}px`}>
      <div class="inner" style=${`transform:scale(${scale})`} dangerouslySetInnerHTML=${{ __html: s.html }}></div>
    </div>
    ${notes ? html`<div class="rd-present-notes">${s.notes ? s.notes : html`<span class="faint">No speaker notes on this slide.</span>`}</div>` : null}
    <div class="rd-present-bar">
      <span>${n} / ${total}</span>
      <button type="button" class="btn btn-sm btn-quiet" onClick=${() => setNotes(!notes)}>${notes ? 'Hide notes' : 'Notes'}</button>
      <button type="button" class="btn btn-sm btn-quiet" onClick=${close}>Exit</button>
    </div>
  </div>`;
}

// ---------------------------------------------------------------------------
// PDF (PDF.js, bundled with Daybook; nothing loads from the internet)
// ---------------------------------------------------------------------------
function PdfBody({ m, width, zoom, register, onReady, onCount, scroller }) {
  const [doc, setDoc] = useState(null);
  const [base, setBase] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    let pdf = null;
    let cancelled = false;
    (async () => {
      try {
        const lib = await pdfjs();
        const res = await fetch(m.raw, { credentials: 'same-origin' });
        if (!res.ok) throw new Error("The PDF couldn't be loaded.");
        const data = await res.arrayBuffer();
        pdf = await lib.getDocument({ data, isEvalSupported: false, standardFontDataUrl: '/vendor/pdfjs/standard_fonts/', useSystemFonts: true }).promise;
        const first = await pdf.getPage(1);
        const vp = first.getViewport({ scale: 1 });
        if (cancelled) return;
        setBase({ w: vp.width, h: vp.height, n: pdf.numPages, lib });
        setDoc(pdf);
        onCount(pdf.numPages);
      } catch (e) {
        if (!cancelled) setErr(e.name === 'PasswordException' ? 'This PDF is password-protected.' : (e.message || String(e)));
      }
    })();
    return () => { cancelled = true; pdf?.destroy(); };
  }, [m.raw]);
  if (err) return html`<div class="rd-msg"><${Empty} title="This PDF couldn't be opened" icon="file">${err}<//></div>`;
  if (!doc || !base) return html`<div class="rd-msg"><${Spinner} label="Opening the PDF…" /></div>`;
  const avail = Math.max(240, width - (width > 700 ? 64 : 20));
  const fitScale = Math.min(avail, 1100) / base.w;
  const scale = fitScale * zoom;
  return html`<div class="rd-pages">
    ${Array.from({ length: base.n }, (_, i) => html`<${PdfPage} key=${i + 1} n=${i + 1} doc=${doc} lib=${base.lib} scale=${scale}
      w0=${base.w} h0=${base.h} register=${register} onReady=${onReady} scroller=${scroller} />`)}
  </div>`;
}

function PdfPage({ n, doc, lib, scale, w0, h0, register, onReady, scroller }) {
  const ref = useRef(null);
  const [size, setSize] = useState({ w: w0, h: h0 });
  const job = useRef({ task: null, layer: null, drawnAt: 0 });
  useEffect(() => { register(n, ref.current); return () => register(n, null); }, [n]);
  useEffect(() => {
    const el = ref.current;
    let visible = false;
    const draw = async () => {
      if (!visible || job.current.drawnAt === scale) return;
      job.current.drawnAt = scale;
      try {
        job.current.task?.cancel();
        const page = await doc.getPage(n);
        const vp1 = page.getViewport({ scale: 1 });
        if (Math.abs(vp1.width - size.w) > 0.5 || Math.abs(vp1.height - size.h) > 0.5) setSize({ w: vp1.width, h: vp1.height });
        const dpr = Math.min(2, window.devicePixelRatio || 1);
        const vp = page.getViewport({ scale: scale * dpr });
        const canvas = el.querySelector('canvas');
        canvas.width = Math.floor(vp.width);
        canvas.height = Math.floor(vp.height);
        const task = page.render({ canvasContext: canvas.getContext('2d'), viewport: vp });
        job.current.task = task;
        await task.promise;
        const text = el.querySelector('.textLayer');
        text.textContent = '';
        el.style.setProperty('--scale-factor', String(scale));
        const layer = new lib.TextLayer({ textContentSource: page.streamTextContent(), container: text, viewport: page.getViewport({ scale }) });
        job.current.layer = layer;
        await layer.render();
        onReady(n, el);
      } catch (e) {
        if (e?.name !== 'RenderingCancelledException') job.current.drawnAt = 0;
      }
    };
    const unload = () => {
      job.current.task?.cancel();
      job.current.drawnAt = 0;
      const canvas = el.querySelector('canvas');
      if (canvas) { canvas.width = 0; canvas.height = 0; }
      const text = el.querySelector('.textLayer');
      if (text) text.textContent = '';
      el.dataset.ready = '0';
    };
    const io = new IntersectionObserver((entries) => {
      for (const en of entries) {
        visible = en.isIntersecting;
        if (visible) draw();
        else if (job.current.drawnAt) unload();
      }
    }, { root: scroller.current, rootMargin: '1200px 0px' });
    io.observe(el);
    return () => { io.disconnect(); job.current.task?.cancel(); };
  }, [scale, doc]);
  const w = Math.round(size.w * scale);
  const h = Math.round(size.h * scale);
  return html`<section class="rd-page rd-pdfpage" data-page=${n} ref=${ref} style=${`width:${w}px;height:${h}px`} aria-label=${`Page ${n}`}>
    <canvas style=${`width:${w}px;height:${h}px`}></canvas>
    <div class="textLayer rd-text"></div>
    <span class="rd-num">${n}</span>
  </section>`;
}

// ---------------------------------------------------------------------------
// Spreadsheets
// ---------------------------------------------------------------------------
function SheetBody({ id, filter, hits }) {
  const [name, setName] = useState(null);
  const [data, setData] = useState(null);
  const [rows, setRows] = useState([]);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const load = async (sheetName, offset = 0) => {
    setBusy(true);
    try {
      const d = await get(`/api/reader/${id}/sheet${qs({ name: sheetName || undefined, offset, limit: 500 })}`);
      setData(d);
      setName(d.name);
      setRows((r) => (offset ? [...r, ...d.rows] : d.rows));
    } catch (e) { setErr(e); }
    setBusy(false);
  };
  useEffect(() => { load(null); }, [id]);
  if (err) return html`<div class="rd-msg"><${Empty} title="This spreadsheet couldn't be opened" icon="file-spreadsheet">${err.message}<//></div>`;
  if (!data) return html`<div class="rd-msg"><${Spinner} label="Reading the sheet…" /></div>`;
  const q = (filter || '').trim().toLowerCase();
  const [head, ...body] = rows.length ? rows : [[]];
  const shown = q ? body.filter((r) => r.some((c) => c.toLowerCase().includes(q))) : body;
  const hitSheets = new Set((hits || []).map((h) => h.page));
  return html`<div class="rd-sheet">
    ${data.sheets.length > 1 ? html`<div class="rd-sheet-tabs" role="tablist">${data.sheets.map((s, i) => html`<button type="button" role="tab"
      aria-selected=${s.name === name ? 'true' : 'false'} onClick=${() => { setRows([]); load(s.name); }}>${s.name}${hitSheets.has(i + 1) ? html`<i class="dot"></i>` : null}</button>`)}</div>` : null}
    <div class="rd-table-wrap">
      <table class="rd-table">
        <thead><tr><th class="rn"></th>${head.map((c) => html`<th>${c}</th>`)}</tr></thead>
        <tbody>${shown.map((r, i) => html`<tr><td class="rn">${q ? '' : i + 2}</td>${r.map((c) => html`<td class=${/^[-$€£(]?[\d,.]+%?\)?$/.test(c) ? 'num' : ''}>${c}</td>`)}</tr>`)}</tbody>
      </table>
    </div>
    <div class="rd-sheet-foot muted">
      ${q ? `${shown.length} matching row${shown.length === 1 ? '' : 's'} · ` : ''}${Math.min(rows.length, data.total)} of ${data.total} rows
      ${rows.length < data.total ? html` · <button class="btn btn-sm" type="button" disabled=${busy} onClick=${() => load(name, rows.length)}>Show more</button>` : null}
    </div>
  </div>`;
}

// ---------------------------------------------------------------------------
// Documents (the note's text, laid out as a page you can highlight)
// ---------------------------------------------------------------------------
function DocBody({ id, width, zoom, register, onReady, onCount }) {
  const [note, setNote] = useState(null);
  const ref = useRef(null);
  useEffect(() => { get(`/api/notes/${id}`).then((n) => { setNote(n); onCount(1); }).catch(toastError); }, [id]);
  useEffect(() => {
    if (!note || !ref.current) return undefined;
    register(1, ref.current);
    onReady(1, ref.current);
    return () => register(1, null);
  }, [note, width, zoom]);
  if (!note) return html`<div class="rd-msg"><${Spinner} label="Opening…" /></div>`;
  const w = Math.round(Math.min(Math.max(280, width - (width > 700 ? 64 : 16)), 820) * Math.min(zoom, 1.6));
  return html`<div class="rd-pages"><section class="rd-page rd-docpage" data-page="1" ref=${ref} style=${`width:${w}px;font-size:${Math.round(16 * zoom)}px`}>
    <div class="rd-text"><h1 class="rd-doc-title">${note.title}</h1><${Markdown} text=${note.body || ''} cls="prose" /></div>
  </section></div>`;
}

// ---------------------------------------------------------------------------
// Side panel: speaker notes, highlights, find results
// ---------------------------------------------------------------------------
function SidePanel({ which, onClose, m, deck, cur, anns, word, findHits, onGo, onOpen, onSaveNote, wide }) {
  const groups = useMemo(() => {
    const g = new Map();
    for (const a of anns) {
      const k = a.page || 0;
      if (!g.has(k)) g.set(k, []);
      g.get(k).push(a);
    }
    return [...g.entries()].sort((x, y) => x[0] - y[0]);
  }, [anns]);
  const slide = deck?.slides?.[cur - 1];
  return html`<aside class=${`rd-panel${wide ? '' : ' sheet-panel'}`} aria-label=${which === 'notes' ? 'Speaker notes' : 'Highlights'}>
    <div class="rd-panel-head">
      <strong>${which === 'notes' ? `Speaker notes · ${word} ${cur}` : `Highlights${anns.length ? ` · ${anns.length}` : ''}`}</strong>
      <button class="icon-btn" type="button" title="Close" onClick=${onClose}><${Icon} name="x" /></button>
    </div>
    <div class="rd-panel-body">
      ${which === 'notes' ? html`
        ${slide ? html`<div class="rd-notes-title">${slide.title}</div>` : null}
        ${slide?.notes ? html`<div class="rd-notes">${slide.notes}</div>` : html`<p class="faint">No speaker notes on this slide.</p>`}
        ${(findHits || []).length ? html`<${FindList} hits=${findHits} word=${word} onGo=${onGo} />` : null}
      ` : html`
        ${(findHits || []).length ? html`<${FindList} hits=${findHits} word=${word} onGo=${onGo} />` : null}
        ${!anns.length ? html`<p class="muted">Select text on a ${word.toLowerCase()} and pick a colour to highlight it. Add a comment to keep a thought next to the passage.</p>` : null}
        ${groups.map(([p, list]) => html`<div class="rd-ann-group">
          ${m.mode !== 'doc' && p ? html`<button type="button" class="rd-ann-page" onClick=${() => onGo(p)}>${word} ${p}</button>` : null}
          ${list.map((a) => html`<button type="button" class=${`rd-ann-item c-${a.color}`} onClick=${() => onOpen(a)}>
            <span class="q">${a.quote}</span>${a.comment ? html`<span class="c">${a.comment}</span>` : null}
          </button>`)}
        </div>`)}
        ${anns.length ? html`<div class="rd-panel-foot">
          <button class="btn btn-sm" type="button" onClick=${onSaveNote}><${Icon} name="notebook-pen" />${m.highlights_note ? 'Update the highlights note' : 'Save highlights as a note'}</button>
          ${m.highlights_note ? html`<a class="btn btn-sm btn-quiet" href=${`#/notes/${m.highlights_note}`}>Open it</a>` : null}
        </div>` : null}
      `}
    </div>
  </aside>`;
}

function FindList({ hits, word, onGo }) {
  return html`<div class="rd-findlist"><div class="label">Matches</div>
    ${hits.slice(0, 60).map((h) => html`<button type="button" onClick=${() => onGo(h.page)}><strong>${h.label || `${word} ${h.page}`}</strong>
      <span class="muted">${h.count}×</span><span class="ex">${h.excerpt}</span></button>`)}</div>`;
}

// ---------------------------------------------------------------------------
// Selection toolbar and the highlight popover
// ---------------------------------------------------------------------------
function SelectionBar({ sel, onMark }) {
  const below = touch || sel.top < 120;
  const style = `left:${Math.round(Math.min(window.innerWidth - 150, Math.max(150, sel.x)))}px;top:${Math.round(below ? sel.bottom + 12 : sel.top - 50)}px`;
  return html`<div class="rd-selbar" style=${style} role="toolbar" aria-label="Highlight" onMouseDown=${(e) => e.preventDefault()}>
    ${COLORS.map((c) => html`<button type="button" class=${`sw c-${c}`} title=${`Highlight ${c}`} onClick=${() => onMark(c)}></button>`)}
    <span class="sep"></span>
    <button type="button" class="btn btn-sm btn-quiet" onClick=${() => onMark('yellow', true)}><${Icon} name="message-square-text" />Comment</button>
  </div>`;
}

function AnnotationPop({ pop, ann, onClose, onChange, onDelete }) {
  const [text, setText] = useState(ann?.comment || '');
  const [busy, setBusy] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    const t = ref.current?.querySelector('textarea');
    if (t && !ann?.comment) t.focus();
    const onDoc = (e) => { if (ref.current && !ref.current.contains(e.target) && !e.target.closest('.rd-page')) save(true); };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [pop.id]);
  if (!ann) return null;
  const save = async (close) => {
    if (text !== ann.comment) {
      setBusy(true);
      try { onChange(await put(`/api/annotations/${ann.id}`, { comment: text })); } catch (e) { toastError(e); }
      setBusy(false);
    }
    if (close) onClose();
  };
  const recolor = async (c) => { try { onChange(await put(`/api/annotations/${ann.id}`, { color: c })); } catch (e) { toastError(e); } };
  const remove = async () => {
    if (ann.comment && !(await confirmDialog({ title: 'Delete this highlight?', message: 'Its comment is deleted too.', confirm: 'Delete', danger: true }))) return;
    try { await del(`/api/annotations/${ann.id}`); onDelete(ann.id); } catch (e) { toastError(e); }
  };
  const mobile = window.innerWidth < 640;
  const style = mobile ? '' : `left:${Math.round(Math.min(window.innerWidth - 340, Math.max(12, pop.x - 160)))}px;top:${Math.round(Math.min(window.innerHeight - 260, pop.y + 8))}px`;
  return html`<div class=${`rd-pop${mobile ? ' as-sheet' : ''}`} style=${style} ref=${ref} role="dialog" aria-label="Highlight">
    <div class="q">${ann.quote}</div>
    <textarea rows="3" placeholder="Add a comment…" value=${text} onInput=${(e) => setText(e.target.value)}
      onKeyDown=${(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) save(true); if (e.key === 'Escape') onClose(); }}></textarea>
    <div class="row">
      ${COLORS.map((c) => html`<button type="button" class=${`sw c-${c}`} aria-pressed=${ann.color === c ? 'true' : 'false'} title=${c} onClick=${() => recolor(c)}></button>`)}
      <span style="flex:1"></span>
      <button class="icon-btn" type="button" title="Copy the quote" onClick=${() => navigator.clipboard?.writeText(ann.quote).then(() => toast('Copied'))}><${Icon} name="copy" /></button>
      <button class="icon-btn" type="button" title="Delete highlight" onClick=${remove}><${Icon} name="trash-2" /></button>
      <button class="btn btn-sm btn-primary" type="button" disabled=${busy} onClick=${() => save(true)}>Done</button>
    </div>
  </div>`;
}
