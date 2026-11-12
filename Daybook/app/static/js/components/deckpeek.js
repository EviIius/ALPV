// The first slides of a deck, shown at the top of its note, so opening a PowerPoint
// shows the PowerPoint. Tap a slide to read the deck from there.
import { html, useEffect, useRef, useState } from '/vendor/preact.js';
import { get } from '../lib/api.js';
import { Icon } from '../lib/ui.js';

const PEEK = 7;

function Frame({ deck, s, w }) {
  const sc = w / deck.width;
  return html`<span class="frame" style=${`width:${w}px;height:${Math.round(deck.height * sc)}px`}>
    <span class="inner" style=${`transform:scale(${sc})`} dangerouslySetInnerHTML=${{ __html: s.html }}></span></span>`;
}

export function DeckPeek({ note }) {
  const [deck, setDeck] = useState(null);
  const [problem, setProblem] = useState(null);
  const [w, setW] = useState(0);
  const box = useRef(null);
  useEffect(() => {
    let live = true;
    setDeck(null);
    setProblem(null);
    get(`/api/reader/${note.id}/slides?limit=${PEEK}`).then((d) => { if (live) setDeck(d); }).catch((err) => { if (live) setProblem(err); });
    return () => { live = false; };
  }, [note.id]);
  useEffect(() => {
    const el = box.current;
    if (!el) return undefined;
    const ro = new ResizeObserver(() => setW(el.clientWidth));
    ro.observe(el);
    setW(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  const href = (n) => `#/read/${note.id}${n > 1 ? `?p=${n}` : ''}`;
  const count = deck?.count || note.lib?.slides || 0;
  const first = deck?.slides?.[0];
  const rest = deck ? deck.slides.slice(1) : [];
  const big = Math.min(w, 720);
  const small = w < 480 ? 132 : 168;
  return html`<section class="deck-peek" ref=${box} aria-label="Slides">
    ${first && big ? html`<a class="dp-first" href=${href(1)} title="Open the slides">
        <${Frame} deck=${deck} s=${first} w=${big} />
        <span class="dp-open"><${Icon} name="presentation" />View slides${count ? html`<span class="n">${count}</span>` : null}</span>
      </a>`
      : html`<a class=${`dp-wait ${problem ? 'is-off' : ''}`} href=${href(1)}>
        ${problem ? html`<${Icon} name="presentation" /><span><strong>View slides</strong>${problem.missing ? html`<small>${problem.message}</small>` : html`<small>The preview couldn't be drawn here. The reader may still open it.</small>`}</span>`
          : html`<span class="spinner"></span><span>Drawing the slides…</span>`}
      </a>`}
    ${rest.length ? html`<div class="dp-strip">
      ${rest.map((s) => html`<a class="dp-thumb" href=${href(s.n)} title=${`Slide ${s.n}${s.title ? `: ${s.title}` : ''}`}>
        <${Frame} deck=${deck} s=${s} w=${small} /><span class="num">${s.n}</span></a>`)}
      ${count > deck.slides.length ? html`<a class="dp-more" href=${href(deck.slides.length + 1)} style=${`width:${small}px;height:${Math.round(deck.height * (small / deck.width))}px`}>
        <strong>+${count - deck.slides.length}</strong><span>more slides</span></a>` : null}
    </div>` : null}
  </section>`;
}
