// Highlights: everything you've marked in decks, PDFs and documents, in one place.
import { html, useEffect, useRef, useState } from '/vendor/preact.js';
import { get, post, qs } from '../lib/api.js';
import { toast, toastError, useChanges } from '../lib/store.js';
import { navigate } from '../lib/router.js';
import { fmtAgo } from '../lib/dates.js';
import { Empty, Icon, Spinner, useDebounced } from '../lib/ui.js';

const COLORS = ['yellow', 'green', 'blue', 'pink', 'purple'];
const WORD = { deck: 'Slide', pdf: 'Page', sheet: 'Sheet' };

export function HighlightsView({ route }) {
  const [q, setQ] = useState(route.params.get('q') || '');
  const [color, setColor] = useState('');
  const [items, setItems] = useState(null);
  const load = useRef(null);
  load.current = async () => {
    try { setItems((await get(`/api/annotations${qs({ q, limit: 1000 })}`)).items); } catch (err) { toastError(err); }
  };
  const later = useDebounced(() => load.current(), 220);
  useEffect(() => { load.current(); }, []);
  useEffect(() => { later(); }, [q]);
  useChanges(['annotations', 'notes'], () => later());

  const shown = (items || []).filter((a) => !color || a.color === color);
  const groups = [];
  const byNote = new Map();
  for (const a of shown) {
    if (!byNote.has(a.note_id)) { const g = { id: a.note_id, title: a.note_title, kind: a.note_kind, items: [], latest: 0 }; byNote.set(a.note_id, g); groups.push(g); }
    const g = byNote.get(a.note_id);
    g.items.push(a);
    g.latest = Math.max(g.latest, a.updated_at);
  }
  for (const g of groups) g.items.sort((x, y) => (x.page ?? 0) - (y.page ?? 0) || x.created_at - y.created_at);
  groups.sort((a, b) => b.latest - a.latest);

  const saveNote = async (g) => {
    try {
      const r = await post(`/api/reader/${g.id}/highlights-note`);
      toast('Saved your highlights as a note.', { action: { label: 'Open', run: () => navigate(`#/notes/${r.id}`) } });
    } catch (err) { toastError(err); }
  };
  return html`<div class="view highlights">
    <div class="view-head">
      <div>
        <h1 class="view-title"><${Icon} name="highlighter" />Highlights</h1>
        <p class="view-sub">What you've marked in decks, PDFs and documents. Select text in the reader to add more.</p>
      </div>
    </div>
    <div class="gl-tools">
      <div class="gl-search"><${Icon} name="search" size="sm" />
        <input class="input" type="search" placeholder="Search highlights, comments and titles" value=${q} onInput=${(e) => setQ(e.currentTarget.value)} aria-label="Search highlights" /></div>
      <div class="hl-colors" role="group" aria-label="Colour">
        <button class=${`chip ${color ? '' : 'on'}`} onClick=${() => setColor('')}>All</button>
        ${COLORS.map((c) => html`<button class=${`sw c-${c}`} aria-pressed=${String(color === c)} title=${c} aria-label=${`Only ${c}`} onClick=${() => setColor(color === c ? '' : c)}></button>`)}
      </div>
    </div>
    ${!items ? html`<${Spinner} label="Loading" />` : !groups.length ? html`<${Empty} icon="highlighter" title=${q || color ? 'No highlights match' : 'No highlights yet'}>
      <span class="muted">Open a deck, PDF or document from your library, select some text and pick a colour.</span></${Empty}>`
    : html`<div class="hl-groups">${groups.map((g) => html`<section class="hl-group" key=${g.id}>
        <header class="hl-head">
          <a class="hl-doc" href=${`#/read/${g.id}`}><${Icon} name=${g.kind === 'deck' ? 'presentation' : g.kind === 'sheet' ? 'file-spreadsheet' : 'file-text'} />
            <span>${g.title || 'Untitled'}</span></a>
          <span class="faint">${g.items.length} highlight${g.items.length === 1 ? '' : 's'} · ${fmtAgo(g.latest)}</span>
          <button class="btn btn-ghost btn-sm" onClick=${() => saveNote(g)} title="Gather these into a note you can edit and search"><${Icon} name="notebook-pen" />Save as note</button>
        </header>
        ${g.items.map((a) => html`<a class=${`hl-item c-${a.color}`} href=${`#/read/${g.id}${a.page ? `?p=${a.page}` : ''}`}>
          <span class="hl-page faint">${a.page ? `${WORD[g.kind] || 'Page'} ${a.page}` : ''}</span>
          <span class="hl-text"><span class="hl-quote">${a.quote}</span>
            ${a.comment ? html`<span class="hl-comment"><${Icon} name="message-circle" size="sm" />${a.comment}</span>` : null}</span>
        </a>`)}
      </section>`)}</div>`}
  </div>`;
}
