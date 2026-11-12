// Glossary: the acronyms and terms in your notes, what they stand for, and how
// transcripts tend to mishear them. Found by rule on this computer; your own
// definitions always win.
import { html, useEffect, useRef, useState } from '/vendor/preact.js';
import { del, get, post, put, qs } from '../lib/api.js';
import { toast, toastError, useChanges, useStore } from '../lib/store.js';
import { fmtAgo } from '../lib/dates.js';
import { Empty, Icon, Modal, Seg, Spinner, useDebounced } from '../lib/ui.js';
import { resetGlossaryMap } from '../components/termpeek.js';

const SHOW = [
  { value: 'all', label: 'All' },
  { value: 'undefined', label: 'Needs a definition' },
  { value: 'spellings', label: 'Spellings' },
  { value: 'yours', label: 'Yours' },
  { value: 'hidden', label: 'Hidden' },
];

export function GlossaryView({ route }) {
  const [q, setQ] = useState(route.params.get('q') || '');
  const [show, setShow] = useState(route.params.get('show') || 'all');
  const [data, setData] = useState(null);
  const [editing, setEditing] = useState(null);
  const [busy, setBusy] = useState(false);
  const load = useRef(null);
  load.current = async () => {
    try { setData(await get(`/api/glossary${qs({ q, show, limit: 600 })}`)); } catch (err) { toastError(err); }
  };
  const later = useDebounced(() => load.current(), 220);
  useEffect(() => { load.current(); }, [show]);
  useEffect(() => { later(); }, [q]);
  useEffect(() => { const u = route.params.get('q'); if (u !== null && u !== q) setQ(u); }, [route.params.get('q')]);
  useChanges(['glossary', 'notes'], () => { resetGlossaryMap(); later(); });

  const rescan = async () => {
    setBusy(true);
    try { const r = await post('/api/glossary/rebuild'); toast(`Glossary updated: ${r.terms} terms.`); resetGlossaryMap(); await load.current(); }
    catch (err) { toastError(err); } finally { setBusy(false); }
  };
  const c = data?.counts || {};
  const label = (o) => (o.value === 'all' ? `All ${c.all ?? ''}` : o.value === 'undefined' ? `Needs a definition ${c.undefined ?? ''}`
    : o.value === 'spellings' ? `Spellings ${c.spellings ?? ''}` : o.value === 'yours' ? `Yours ${c.yours ?? ''}` : `Hidden ${c.hidden ?? ''}`);
  return html`<div class="view glossary">
    <div class="view-head">
      <div>
        <h1 class="view-title"><${Icon} name="book-a" />Glossary</h1>
        <p class="view-sub">Acronyms and terms from your notes, what they stand for, and how transcripts mishear them. Select a term anywhere in Daybook to see its definition.</p>
      </div>
      <div class="row">
        <button class="btn" onClick=${rescan} disabled=${busy}><${Icon} name="refresh-cw" />${busy ? 'Reading notes…' : 'Rescan'}</button>
        <button class="btn btn-primary" onClick=${() => setEditing({ term: '', definition: '', aliases: [], isNew: true })}><${Icon} name="plus" />Add term</button>
      </div>
    </div>
    <div class="gl-tools">
      <div class="gl-search"><${Icon} name="search" size="sm" />
        <input class="input" type="search" placeholder="Search terms, meanings and spellings" value=${q} onInput=${(e) => setQ(e.currentTarget.value)} aria-label="Search the glossary" /></div>
      <div class="gl-seg"><${Seg} value=${show} onChange=${setShow} label="Show" options=${SHOW.map((o) => ({ value: o.value, label: label(o) }))} /></div>
    </div>
    ${!data ? html`<${Spinner} label="Loading" />` : data.items.length ? html`<div class="gl-list">
      ${data.items.map((it) => html`<${TermRow} key=${it.term} it=${it} onEdit=${() => setEditing(it)} reload=${() => load.current()} />`)}
    </div>` : html`<${Empty} icon="book-a" title=${q ? 'No terms match' : show === 'undefined' ? 'Nothing needs a definition' : 'No terms yet'}>
      <span class="muted">${show === 'all' && !q ? 'Daybook lists terms your notes define, like "Data private cloud (DPC)", glossary tables, and acronyms you use often. Add your own with Add term.' : ''}</span>
    </${Empty}>`}
    ${data?.rebuilt_at ? html`<p class="faint gl-foot">Read from your notes ${fmtAgo(data.rebuilt_at)}. Locked notes are never read.</p>` : null}
    ${editing ? html`<${TermEditor} it=${editing} onClose=${() => setEditing(null)} onSaved=${() => { setEditing(null); resetGlossaryMap(); load.current(); }} />` : null}
  </div>`;
}

function TermRow({ it, onEdit, reload }) {
  const hide = async () => {
    try {
      const r = it.hidden ? await put(`/api/glossary/${encodeURIComponent(it.term)}`, { hidden: false }) : await del(`/api/glossary/${encodeURIComponent(it.term)}`);
      toast(it.hidden ? `${it.term} is back.` : r.deleted ? `Deleted ${it.term}.` : `Hidden ${it.term}. It won't come back on a rescan.`);
      resetGlossaryMap();
      reload();
    } catch (err) { toastError(err); }
  };
  return html`<div class=${`gl-row ${it.definition ? '' : 'undef'}`}>
    <div class="gl-term"><span class="t">${it.term}</span>
      ${it.origin === 'user' ? html`<span class="chip" title="Your definition; rescans never change it">Yours</span>` : null}</div>
    <div class="gl-body">
      ${it.definition ? html`<div class="gl-def">${it.definition}</div>`
        : it.aliases.length ? html`<div class="gl-def faint">Spelling only</div>`
        : html`<button class="linkish" onClick=${onEdit}>Add a definition</button>`}
      ${it.aliases.length ? html`<div class="gl-aliases" title="How transcripts have written it"><${Icon} name="audio-lines" size="sm" />
        <span class="faint">Heard as</span>${it.aliases.map((a) => html`<span class="chip">${a}</span>`)}</div>` : null}
      <div class="gl-meta faint">
        ${it.uses ? html`<span>Used ${it.uses}× in ${it.note_count} note${it.note_count === 1 ? '' : 's'}</span>` : html`<span>Not used in your notes yet</span>`}
        ${it.uses ? html`<a href=${`#/notes?q=${encodeURIComponent(/\s/.test(it.term) ? `"${it.term}"` : it.term)}`}>Find</a>` : null}
        ${it.source_note && it.source_title ? html`<a href=${`#/notes/${it.source_note}`}>Defined in ${it.source_title}</a>` : null}
      </div>
    </div>
    <div class="gl-actions">
      <button class="btn btn-ghost btn-icon btn-sm" onClick=${onEdit} title="Edit" aria-label=${`Edit ${it.term}`}><${Icon} name="pencil" /></button>
      <button class="btn btn-ghost btn-icon btn-sm" onClick=${hide} title=${it.hidden ? 'Show again' : it.origin === 'user' && !it.source_note ? 'Delete' : 'Hide'}
        aria-label=${`${it.hidden ? 'Show' : 'Hide'} ${it.term}`}><${Icon} name=${it.hidden ? 'eye' : it.origin === 'user' && !it.source_note ? 'trash-2' : 'eye-off'} /></button>
    </div>
  </div>`;
}

function TermEditor({ it, onClose, onSaved }) {
  const [term, setTerm] = useState(it.term);
  const [definition, setDefinition] = useState(it.definition || '');
  const [aliases, setAliases] = useState((it.aliases || []).join(', '));
  const [saving, setSaving] = useState(false);
  const ai = useStore((s) => s.ai);
  const [hint, setHint] = useState(null);          // { busy } | { definition, sources, model, ms } | { reason }
  const suggest = async () => {
    setHint({ busy: true });
    try {
      const r = await post('/api/ai/define-term', { term: term.trim() || it.term });
      setHint(r);
      if (r.definition) setDefinition(r.definition);
    } catch (err) { setHint(null); toastError(err); }
  };
  const save = async (e) => {
    e && e.preventDefault();
    if (!term.trim()) return;
    setSaving(true);
    try {
      if (it.isNew) await post('/api/glossary', { term, definition, aliases });
      else await put(`/api/glossary/${encodeURIComponent(it.term)}`, { term, definition, aliases });
      toast(`Saved ${term.trim()}.`);
      onSaved();
    } catch (err) { toastError(err); setSaving(false); }
  };
  return html`<${Modal} title=${it.isNew ? 'Add a term' : `Edit ${it.term}`} onClose=${onClose}
    footer=${html`<button class="btn" onClick=${onClose}>Cancel</button><button class="btn btn-primary" onClick=${save} disabled=${saving || !term.trim()}>Save</button>`}>
    <form class="stack" onSubmit=${save}>
      <label class="field"><span>Term</span><input class="input" value=${term} maxlength="60" onInput=${(e) => setTerm(e.currentTarget.value)} placeholder="e.g. DPC" autofocus=${it.isNew} /></label>
      <label class="field"><span>What it means</span><textarea class="textarea" rows="3" maxlength="400" value=${definition}
        onInput=${(e) => setDefinition(e.currentTarget.value)} placeholder="e.g. Data private cloud: the bank's on-premises data platform"></textarea></label>
      ${ai?.ok && (it.uses || !it.isNew) ? html`<div class="gl-suggest">
        <button type="button" class="btn btn-sm" onClick=${suggest} disabled=${hint?.busy || !term.trim()}>
          ${hint?.busy ? html`<span class="spinner"></span>` : html`<${Icon} name="sparkles" />`}${definition.trim() ? 'Suggest another from my notes' : 'Suggest from my notes'}</button>
        ${hint && !hint.busy ? html`<p class="hint">${hint.definition
          ? html`Suggested by your local AI${hint.model ? html` (${hint.model}, ${(hint.ms / 1000).toFixed(1)}s)` : ''} from ${hint.sources.slice(0, 3).map((n, i) => html`${i ? ', ' : ''}<a href=${`#/notes/${n.id}`} onClick=${onClose}>${n.title}</a>`)}${hint.sources.length > 3 ? ` and ${hint.sources.length - 3} more` : ''}. Check it before you save.`
          : hint.reason}</p>` : null}
      </div>` : null}
      <label class="field"><span>Sounds like</span><input class="input" value=${aliases} onInput=${(e) => setAliases(e.currentTarget.value)} placeholder="e.g. Gizmo, Gismos" />
        <span class="hint">How transcripts write it, separated by commas. Daybook offers to fix these in your notes, helps local Whisper spell the term, and tells the local AI what they mean.</span></label>
      ${!it.isNew && it.origin !== 'user' ? html`<p class="hint faint">Saving makes this definition yours; rescans won't change it.</p>` : null}
    </form>
  </${Modal}>`;
}

// ---------------------------------------------------------------------------
// "Sounds like" fixes for one note, shown above the editor
// ---------------------------------------------------------------------------
export function TermFixes({ note, beforeApply, onApplied }) {
  const [fixes, setFixes] = useState(null);
  const [dismissed, setDismissed] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let live = true;
    setDismissed(false);
    if (!note || note.locked) { setFixes(null); return undefined; }
    get(`/api/notes/${note.id}/term-fixes`).then((r) => live && setFixes(r)).catch(() => live && setFixes(null));
    return () => { live = false; };
  }, [note?.id, note?.rev]);
  if (dismissed || !fixes || !fixes.fixes.length) return null;
  const apply = async (list) => {
    setBusy(true);
    try {
      if (beforeApply) await beforeApply();
      const r = await post(`/api/notes/${note.id}/term-fixes`, { fixes: list.map((f) => ({ variant: f.variant, term: f.term })) });
      toast(`Fixed ${r.replaced} spelling${r.replaced === 1 ? '' : 's'}.`);
      onApplied && onApplied(r.note);
    } catch (err) { toastError(err); } finally { setBusy(false); }
  };
  const total = fixes.fixes.reduce((n, f) => n + f.count, 0);
  return html`<div class="term-fixes" role="status">
    <${Icon} name="spell-check" />
    <div class="grow">
      <strong>${total} likely mishearing${total === 1 ? '' : 's'}</strong>
      <span class="tf-list">${fixes.fixes.slice(0, 6).map((f) => html`<button class="tf-item" disabled=${busy || !fixes.editable}
        title=${fixes.editable ? `Replace “${f.wrong}” with ${f.term}` : ''} onClick=${() => apply([f])}>
        <s>${f.wrong}</s> → ${f.term}${f.count > 1 ? html` <span class="faint">×${f.count}</span>` : null}</button>`)}</span>
      ${fixes.editable ? null : html`<span class="faint"> This file is read-only here, so it can't be changed; the local AI still reads these right.</span>`}
    </div>
    ${fixes.editable ? html`<button class="btn btn-sm" disabled=${busy} onClick=${() => apply(fixes.fixes)}>Fix all</button>` : null}
    <button class="btn btn-ghost btn-icon btn-sm" onClick=${() => setDismissed(true)} aria-label="Dismiss"><${Icon} name="x" /></button>
  </div>`;
}
