// Notes: list, rich Markdown editor, local-AI assist, locking, history.
import { html, useEffect, useMemo, useRef, useState } from '/vendor/preact.js';
import { del, downloadUrl, get, post, put, qs, streamNdjson, upload } from '../lib/api.js';
import { emitChange, getState, setState, toast, toastError, useChanges, useStore } from '../lib/store.js';
import { addRouteGuard, navigate } from '../lib/router.js';
import { fmtAgo, fmtBytes, fmtDate, today } from '../lib/dates.js';
import {
  Empty, Field, Icon, Markdown, Modal, Seg, Spinner, confirmDialog, openMenu, promptDialog, useDebounced, useMedia,
} from '../lib/ui.js';
import { openEventEditor } from './calendar.js';
import { TermFixes } from './glossary.js';
import { DateChip, SOURCE, WhyDate, dateMenu, dateText, kindOf, openDateDialog, useIsLocal } from '../lib/library.js';

const VIEW_LABELS = { all: 'All notes', pinned: 'Pinned', daily: 'Daily notes', library: 'Library files', review: 'Needs a date', dated: 'On the calendar', imported: 'Imported', locked: 'Locked', archived: 'Archive', trash: 'Trash' };
const COLORS = { blue: 'var(--blue)', green: 'var(--teal)', violet: 'var(--violet)', amber: 'var(--amber)', red: 'var(--red)' };

let editorModule = null;
const loadEditor = async () => (editorModule ||= await import('/vendor/editor.js'));

function listHref(params, id) {
  const q = params.toString();
  return `#/notes${id ? `/${id}` : ''}${q ? `?${q}` : ''}`;
}

export function NotesView({ route }) {
  const noteId = route.parts[1] || null;
  const params = route.params;
  const narrow = useMedia('(max-width: 899px)');
  return html`<div class=${`notes-layout ${noteId ? 'has-note' : ''}`}>
    <${NotesList} params=${params} activeId=${noteId} />
    <section class="notes-editor-pane" aria-label="Note">
      ${noteId ? html`<${NoteEditor} key=${noteId} id=${noteId} params=${params} narrow=${narrow} />`
        : html`<div class="notes-empty paper-grid"><div class="stack" style="align-items:center;max-width:360px">
            <${Icon} name="notebook-pen" size="lg" />
            <p>Pick a note on the left, or start a new one. Press <span class="kbd">/</span> inside a note for headings, checklists and tables.</p>
            <button class="btn btn-primary" onClick=${() => newNote(params)}><${Icon} name="plus" />New note</button></div></div>`}
    </section>
  </div>`;
}

async function newNote(params) {
  try {
    const note = await post('/api/notes', { notebook_id: params.get('notebook') || null, tags: params.get('tag') ? [params.get('tag')] : [] });
    navigate(listHref(params, note.id));
  } catch (err) { toastError(err); }
}

// ---------------------------------------------------------------------------
// List
// ---------------------------------------------------------------------------
const SEARCH_TIPS = [
  ['in:Kelly', 'In a folder or notebook'], ['type:deck', 'Decks (also pdf, doc, sheet, mockup, transcript, email)'],
  ['person:Jacqueline', 'Meetings that person was in'], ['after:2026-10', 'Dated after a day or month'],
  ['before:2026-10-20', 'Dated before a day'], ['on:2026-11-17', 'On a day (or a month: on:2026-11)'],
  ['has:actions', 'With action items (also decisions, questions, highlights, tasks)'], ['is:undated', 'Without a date (also pinned, library, private, highlighted)'],
  ['tag:sas', 'With a tag (or #sas)'], ['"exact phrase"', 'Words in this order'], ['-word', 'Leave out notes with a word'],
];
function searchTips(anchor, add) {
  openMenu(anchor, [{ header: 'Narrow a search' }, ...SEARCH_TIPS.map(([t, l]) => ({ label: `${t}  ·  ${l}`, onSelect: () => add(t) }))], { align: 'end' });
}
async function saveSearch(q) {
  const name = await promptDialog({ title: 'Save this search', label: 'Name', value: q.replace(/\w+:("[^"]*"|\S+)/g, '').trim() || q, confirm: 'Save', hint: `Shows up in the sidebar and runs “${q}” whenever you open it.` });
  if (!name) return;
  const settings = getState().settings;
  const saved = [...(settings.search?.saved || []).filter((s) => s.name !== name), { name, q }];
  try {
    const next = await put('/api/settings', { search: { saved } });
    setState({ settings: next });
    toast(`Saved “${name}”`);
  } catch (err) { toastError(err); }
}

function NotesList({ params, activeId }) {
  const notebooks = useStore((s) => s.notebooks);
  const [data, setData] = useState(null);
  const [q, setQ] = useState(params.get('q') || '');
  const view = params.get('view') || 'all';
  const notebook = params.get('notebook');
  const tag = params.get('tag');
  const sort = params.get('sort') || 'updated';
  const kind = params.get('kind');
  const key = `${view}|${notebook}|${tag}|${sort}|${kind}`;

  const load = async (query = q) => {
    try { setData(await get(`/api/notes${qs({ view, notebook, tag, sort, kind, q: query.trim() || undefined, limit: 400 })}`)); }
    catch (err) { toastError(err); }
  };
  const search = useDebounced((v) => load(v), 160);
  useEffect(() => { load(); }, [key]);
  const urlQ = params.get('q') || '';
  useEffect(() => { if (urlQ !== q) { setQ(urlQ); load(urlQ); } }, [urlQ]);
  useChanges(['notes', 'notebooks'], () => search(q), [key, q]);

  const nb = notebooks.find((b) => b.id === notebook);
  const heading = nb ? (nb.lib_dir === '' ? 'Top level' : nb.name) : tag ? `#${tag}` : VIEW_LABELS[view] || 'Notes';
  const setParam = (k, v) => {
    const p = new URLSearchParams(params);
    if (v) p.set(k, v); else p.delete(k);
    navigate(listHref(p, activeId));
  };
  const filterMenu = (e) => openMenu(e.currentTarget, [
    { header: 'Show' },
    ...Object.entries(VIEW_LABELS).map(([k, l]) => ({ label: `${l}${data?.counts?.[k === 'all' ? 'all_notes' : k] ? ` (${data.counts[k === 'all' ? 'all_notes' : k]})` : ''}`, icon: k === view ? 'check' : null, onSelect: () => { const p = new URLSearchParams(); if (k !== 'all') p.set('view', k); navigate(listHref(p, activeId)); } })),
    { sep: true }, { header: 'Sort by' },
    ...[['updated', 'Last edited'], ['date', 'Date it belongs to'], ['created', 'Date created'], ['title', 'Title']].map(([k, l]) => ({ label: l, icon: k === sort ? 'check' : null, onSelect: () => setParam('sort', k === 'updated' ? null : k) })),
    ...(nb && nb.lib_dir == null ? [{ sep: true }, { label: 'Rename notebook', icon: 'pencil', onSelect: () => renameNotebook(nb) }, { label: 'Delete notebook', icon: 'trash-2', danger: true, onSelect: () => deleteNotebook(nb) }] : []),
    ...(view === 'trash' ? [{ sep: true }, { label: 'Empty trash', icon: 'trash-2', danger: true, onSelect: emptyTrash }] : []),
  ]);

  return html`<aside class="notes-list-pane" aria-label="Notes list">
    <div class="notes-list-head">
      <div class="row row-between">
        <h1 class="view-title">${heading}</h1>
        <div class="row" style="gap:4px">
          <button class="btn btn-icon" onClick=${filterMenu} aria-label="Filter and sort"><${Icon} name="sliders-horizontal" /></button>
          ${view !== 'trash' ? html`<button class="btn btn-primary btn-icon" onClick=${() => newNote(params)} aria-label="New note"><${Icon} name="plus" /></button>` : null}
        </div>
      </div>
      <div class="capture" style="padding:2px 4px 2px 10px">
        <${Icon} name="search" cls="faint" />
        <input value=${q} placeholder="Search notes" aria-label="Search notes" onInput=${(e) => { setQ(e.currentTarget.value); search(e.currentTarget.value); }} />
        ${q ? html`<button class="btn btn-ghost btn-icon btn-sm" onClick=${() => { setQ(''); load(''); }} aria-label="Clear search"><${Icon} name="x" /></button>` : null}
        <button class="btn btn-ghost btn-icon btn-sm" title="Search tips" aria-label="Search tips" onClick=${(e) => searchTips(e.currentTarget, (t) => { const v = `${q.trim()} ${t}`.trim() + ' '; setQ(v); })}><${Icon} name="circle-help" /></button>
      </div>
      ${data?.filters?.length || (q.trim() && data) ? html`<div class="row row-wrap search-chips" style="gap:6px">
        ${(data.filters || []).map((f) => html`<span class="chip">${f.negated ? 'not ' : ''}${f.key}: ${f.value}</span>`)}
        ${q.trim() ? html`<button class="btn btn-ghost btn-sm" type="button" onClick=${() => saveSearch(q.trim())}><${Icon} name="star" size="sm" />Save search</button>` : null}
      </div>` : null}
      ${nb || tag || kind || view !== 'all' ? html`<div class="row row-wrap" style="gap:6px">
        <span class="chip chip-on">${heading}<button onClick=${() => navigate(listHref(new URLSearchParams(), activeId))} aria-label="Show all notes"><${Icon} name="x" size="sm" /></button></span>
        ${data ? html`<span class="faint" style="font-size:12px">${data.total} note${data.total === 1 ? '' : 's'}</span>` : null}</div>` : null}
    </div>
    <div class="notes-list" role="list">
      ${!data ? html`<div style="padding:16px"><${Spinner} /></div>` : null}
      ${data && !data.items.length ? html`<div style="padding:16px"><${Empty} title=${q ? 'No notes match.' : view === 'trash' ? 'Trash is empty.' : 'No notes here yet.'}>
        ${q ? html`<span>Try fewer words, or <a href="#/ask">ask your notes</a> in plain language.</span>` : view === 'all' ? html`<span>Create one with +, or <a href="#/import">import documents</a>.</span>` : null}</${Empty}></div>` : null}
      ${data?.items.map((n) => {
        const book = notebooks.find((b) => b.id === n.notebook_id);
        return html`<a class="note-item" role="listitem" href=${listHref(params, n.id)} aria-current=${String(n.id === activeId)}>
          <div class="nt">${n.pinned ? html`<${Icon} name="pin" />` : null}${n.locked ? html`<${Icon} name="lock" />` : null}
            ${n.color ? html`<span class="dot" style=${`background:${COLORS[n.color] || 'var(--faint)'}`}></span>` : null}
            <span>${n.title || 'Untitled'}</span></div>
          ${n.locked ? html`<div class="ns faint">Locked</div>` : n.snippet_html ? html`<div class="ns" dangerouslySetInnerHTML=${{ __html: n.snippet_html }}></div>` : n.snippet ? html`<div class="ns">${n.snippet}</div>` : null}
          <div class="nm">${n.linked_date || n.date_status === 'suggested' || (n.lib_path && n.date_status === 'none') ? html`<${DateChip} n=${n} compact=${true} />` : html`<span>${fmtAgo(n.updated_at)}</span>`}
            ${book && !notebook ? html`<span class="row" style="gap:4px">${book.lib_dir != null ? html`<${Icon} name="folder" />` : html`<span class="swatch" style=${`background:${book.color}`}></span>`}${book.lib_dir === '' ? 'Top level' : book.name}</span>` : null}
            ${n.lib_path ? html`<span class="row" style="gap:3px"><${Icon} name=${kindOf(n).icon} />${kindOf(n).label}</span>` : null}
            ${n.kind === 'import' ? html`<span class="row" style="gap:3px"><${Icon} name="file-up" />${n.source_name ? n.source_name.split('.').pop().toUpperCase() : 'Imported'}</span>` : null}
            ${n.has_suggestions ? html`<span class="row" style="gap:3px;color:var(--violet)"><${Icon} name="sparkles" />Review</span>` : null}
            ${n.tags.slice(0, 3).map((t) => html`<span>#${t}</span>`)}</div>
        </a>`;
      })}
    </div>
  </aside>`;
}

async function renameNotebook(nb) {
  const name = await promptDialog({ title: 'Rename notebook', label: 'Name', value: nb.name, confirm: 'Rename' });
  if (!name) return;
  try { await put(`/api/notebooks/${nb.id}`, { name }); emitChange({ entity: 'notebooks' }); setState({ notebooks: await get('/api/notebooks') }); } catch (err) { toastError(err); }
}
async function deleteNotebook(nb) {
  if (!(await confirmDialog({ title: `Delete “${nb.name}”?`, message: 'Its notes are kept and moved to "No notebook".', confirm: 'Delete notebook', danger: true }))) return;
  try { await del(`/api/notebooks/${nb.id}`); setState({ notebooks: await get('/api/notebooks') }); navigate('#/notes'); } catch (err) { toastError(err); }
}
async function emptyTrash() {
  if (!(await confirmDialog({ title: 'Empty trash?', message: 'Notes in the trash and their attachments will be deleted permanently.', confirm: 'Empty trash', danger: true }))) return;
  try { const r = await post('/api/trash/empty'); toast(`Deleted ${r.deleted} notes`); emitChange({ entity: 'notes' }); } catch (err) { toastError(err); }
}

// ---------------------------------------------------------------------------
// Editor
// ---------------------------------------------------------------------------
function TagsInput({ tags, onChange }) {
  const [v, setV] = useState('');
  const all = useStore((s) => s.tags);
  const add = (raw) => {
    const t = raw.trim().replace(/^#/, '').toLowerCase().replace(/[^\w\-/]+/g, '-');
    if (t && !tags.includes(t)) onChange([...tags, t]);
    setV('');
  };
  return html`<div class="tag-input">
    ${tags.map((t) => html`<span class="chip">#${t}<button onClick=${() => onChange(tags.filter((x) => x !== t))} aria-label=${`Remove tag ${t}`}><${Icon} name="x" size="sm" /></button></span>`)}
    <input list="tag-suggestions" value=${v} placeholder=${tags.length ? '' : 'Add tags'} aria-label="Add a tag"
      onInput=${(e) => { const val = e.currentTarget.value; if (/[,\s]$/.test(val)) add(val.slice(0, -1)); else setV(val); }}
      onKeyDown=${(e) => { if (e.key === 'Enter') { e.preventDefault(); add(v); } else if (e.key === 'Backspace' && !v && tags.length) onChange(tags.slice(0, -1)); }}
      onBlur=${() => v && add(v)} />
    <datalist id="tag-suggestions">${all.map((t) => html`<option value=${t.tag} />`)}</datalist>
  </div>`;
}

function Toolbar({ ed, state, onAi, onMore, onPanel, panelOpen, narrow }) {
  if (!ed) return html`<div class="editor-toolbar"></div>`;
  const c = () => ed.chain();
  const b = (icon, label, active, run) => html`<button class="tb" type="button" title=${label} aria-label=${label} aria-pressed=${active ? 'true' : 'false'}
    onMouseDown=${(e) => e.preventDefault()} onClick=${run}><${Icon} name=${icon} /></button>`;
  const heading = state.h1 ? 'Heading 1' : state.h2 ? 'Heading 2' : state.h3 ? 'Heading 3' : 'Text';
  const styleMenu = (e) => openMenu(e.currentTarget, [
    { label: 'Text', icon: 'pilcrow', onSelect: () => c().setParagraph().run() },
    { label: 'Heading 1', icon: 'heading-1', onSelect: () => c().toggleHeading({ level: 1 }).run() },
    { label: 'Heading 2', icon: 'heading-2', onSelect: () => c().toggleHeading({ level: 2 }).run() },
    { label: 'Heading 3', icon: 'heading-3', onSelect: () => c().toggleHeading({ level: 3 }).run() },
    { label: 'Quote', icon: 'quote', onSelect: () => c().toggleBlockquote().run() },
    { label: 'Code block', icon: 'code', onSelect: () => c().toggleCodeBlock().run() },
  ]);
  const link = async () => {
    const prev = ed.editor.getAttributes('link').href || '';
    const url = await promptDialog({ title: 'Link', label: 'Address', value: prev, placeholder: 'https:// or #/notes/…', confirm: 'Apply' });
    if (url === null) return;
    if (!url) c().unsetLink().run(); else c().extendMarkRange('link').setLink({ href: url }).run();
  };
  return html`<div class="editor-toolbar" role="toolbar" aria-label="Formatting">
    ${b('undo-2', 'Undo', false, () => c().undo().run())}${b('redo-2', 'Redo', false, () => c().redo().run())}
    <span class="tb-sep"></span>
    <button class="tb tb-text" type="button" onMouseDown=${(e) => e.preventDefault()} onClick=${styleMenu}>${heading}<${Icon} name="chevron-down" size="sm" /></button>
    ${b('bold', 'Bold', state.bold, () => c().toggleBold().run())}
    ${b('italic', 'Italic', state.italic, () => c().toggleItalic().run())}
    ${narrow ? null : b('underline', 'Underline', state.underline, () => c().toggleUnderline().run())}
    ${narrow ? null : b('strikethrough', 'Strikethrough', state.strike, () => c().toggleStrike().run())}
    ${b('highlighter', 'Highlight', state.highlight, () => c().toggleHighlight().run())}
    ${b('link', 'Link', state.link, link)}
    <span class="tb-sep"></span>
    ${b('list-checks', 'Checklist', state.taskList, () => c().toggleTaskList().run())}
    ${b('list', 'Bulleted list', state.bulletList, () => c().toggleBulletList().run())}
    ${b('list-ordered', 'Numbered list', state.orderedList, () => c().toggleOrderedList().run())}
    ${narrow ? null : b('table', 'Table', false, () => c().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run())}
    ${b('image', 'Image', false, () => pickFiles('image/*', (files) => ed.insertFiles(files)))}
    <span class="tb-sep"></span>
    <button class="tb tb-text tb-ai" type="button" onClick=${onAi} title="Local AI assist"><${Icon} name="sparkles" />${narrow ? '' : 'Assist'}</button>
    <span style="flex:1"></span>
    ${narrow ? null : b('panel-left', panelOpen ? 'Hide side panel' : 'Show side panel', panelOpen, onPanel)}
    ${b('ellipsis', 'More', false, onMore)}
  </div>`;
}

function pickFiles(accept, cb) {
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = accept;
  input.multiple = true;
  input.onchange = () => cb([...input.files]);
  input.click();
}

function NoteEditor({ id, params, narrow }) {
  const ai = useStore((s) => s.ai);
  const notebooks = useStore((s) => s.notebooks);
  const vault = useStore((s) => s.vault);
  const [note, setNote] = useState(null);
  const [error, setError] = useState(null);
  const [locked, setLocked] = useState(false);
  const [status, setStatus] = useState('saved');
  const [conflict, setConflict] = useState(null);
  const [tbState, setTbState] = useState({});
  const [words, setWords] = useState(0);
  const [panel, setPanel] = useState(() => { try { return localStorage.getItem('daybook_panel') || ''; } catch { return ''; } });
  const [bubble, setBubble] = useState(null);
  const [history, setHistory] = useState(false);
  const host = useRef(null);
  const edRef = useRef(null);
  const [ed, setEd] = useState(null);
  const noteRef = useRef(null);
  const dirty = useRef(false);
  const titleRef = useRef(null);
  noteRef.current = note;

  // Load
  const load = async () => {
    try {
      const n = await get(`/api/notes/${id}`);
      if (n.locked) {
        const pass = getState().vault;
        if (pass) {
          try { const u = await post(`/api/notes/${id}/unlock`, { passphrase: pass }); setNote(u); setLocked(false); return u; }
          catch { setState({ vault: null }); }
        }
        setNote(n); setLocked(true); return n;
      }
      setNote(n); setLocked(false); return n;
    } catch (err) { setError(err.message); return null; }
  };
  useEffect(() => { load(); }, [id]);

  // Save
  const save = async ({ force = false } = {}) => {
    const n = noteRef.current;
    const e = edRef.current;
    if (!n || !e || !dirty.current) return;
    if (n.editable === false) { dirty.current = false; setStatus('saved'); return; }
    const body = e.getMarkdown();
    const title = titleRef.current ? titleRef.current.value : n.title;
    dirty.current = false;
    setStatus('saving');
    try {
      const saved = n.locked
        ? await post(`/api/notes/${id}/save-locked`, { passphrase: getState().vault, title, body, base_rev: force ? undefined : n.rev })
        : await put(`/api/notes/${id}`, { title, body, base_rev: n.rev, force });
      setNote((cur) => ({ ...cur, rev: saved.rev, updated_at: saved.updated_at, word_count: saved.word_count, title: saved.title,
        lib_path: saved.lib_path, lib: saved.lib, linked_date: saved.linked_date, date_end: saved.date_end, date_time: saved.date_time,
        date_source: saved.date_source, date_status: saved.date_status, date_confidence: saved.date_confidence, date_info: saved.date_info,
        facts: saved.facts, mentions: saved.mentions, lib_pending: saved.lib_pending }));
      noteRef.current = { ...noteRef.current, rev: saved.rev };
      if (saved.lib_conflict) toast(`This file changed on disk while you were editing. Your version was saved as ${saved.lib_conflict.split('/').pop()}.`, { timeout: 12000 });
      setStatus(dirty.current ? 'unsaved' : 'saved');
      setConflict(null);
    } catch (err) {
      dirty.current = true;
      if (err.status === 409) { setConflict(err.data?.note || {}); setStatus('conflict'); }
      else { setStatus('error'); toastError(err); }
    }
  };
  const saveSoon = useDebounced(save, 800);
  const markDirty = () => { dirty.current = true; setStatus('unsaved'); saveSoon(); };

  useEffect(() => {
    const flush = () => { if (dirty.current) saveSoon.flush(); };
    const off = addRouteGuard(flush);
    const unload = (e) => { if (dirty.current) { flush(); e.preventDefault(); } };
    const hide = () => { if (document.visibilityState === 'hidden') flush(); };
    window.addEventListener('beforeunload', unload);
    document.addEventListener('visibilitychange', hide);
    return () => { flush(); off(); window.removeEventListener('beforeunload', unload); document.removeEventListener('visibilitychange', hide); };
  }, [id]);

  // Remote changes (another device)
  useChanges(['notes'], async (evt) => {
    if (evt.id !== id || dirty.current) return;
    try {
      const fresh = await get(`/api/notes/${id}`);
      const cur = noteRef.current;
      if (!cur || fresh.rev <= cur.rev) {
        if (cur) setNote((n) => ({ ...n, backlinks: fresh.backlinks, attachments: fresh.attachments, tasks: fresh.tasks, events: fresh.events, ai_suggestions: fresh.ai_suggestions, summary: fresh.summary, tags: fresh.tags, pinned: fresh.pinned, notebook_id: fresh.notebook_id, trashed_at: fresh.trashed_at, archived: fresh.archived,
          linked_date: fresh.linked_date, date_end: fresh.date_end, date_time: fresh.date_time, date_source: fresh.date_source, date_status: fresh.date_status,
          date_confidence: fresh.date_confidence, date_info: fresh.date_info, facts: fresh.facts, mentions: fresh.mentions, lib: fresh.lib, lib_path: fresh.lib_path, related: fresh.related, lib_pending: fresh.lib_pending }));
        return;
      }
      if (fresh.locked) return;
      setNote(fresh);
      if (edRef.current) edRef.current.setMarkdown(fresh.body);
      if (titleRef.current) titleRef.current.value = fresh.title;
    } catch { /* ignore */ }
  }, [id]);

  // Mount the editor once the note body is available.
  const ready = note && !locked;
  useEffect(() => {
    if (!ready || !host.current) return undefined;
    let destroyed = false;
    let instance = null;
    (async () => {
      const mod = await loadEditor();
      if (destroyed) return;
      instance = mod.createEditor({
        element: host.current,
        markdown: noteRef.current.body || '',
        editable: !noteRef.current.trashed_at && noteRef.current.editable !== false,
        // Read-only files never save: editor plugins can still adjust the document (e.g. add a trailing
        // paragraph) the first time you click or select in it.
        onUpdate: () => { if (noteRef.current.editable === false || noteRef.current.trashed_at) return; markDirty(); setWords(instance.wordCount()); },
        onTransaction: () => {
          const e = instance;
          setTbState({
            bold: e.isActive('bold'), italic: e.isActive('italic'), underline: e.isActive('underline'), strike: e.isActive('strike'),
            highlight: e.isActive('highlight'), link: e.isActive('link'), taskList: e.isActive('taskList'), bulletList: e.isActive('bulletList'),
            orderedList: e.isActive('orderedList'), h1: e.isActive('heading', { level: 1 }), h2: e.isActive('heading', { level: 2 }), h3: e.isActive('heading', { level: 3 }),
          });
        },
        onSelectionUpdate: () => updateBubble(instance),
        onBlur: () => setTimeout(() => { if (!document.activeElement?.closest?.('.bubble')) setBubble(null); }, 120),
        onUploadImage: async (file) => {
          const fd = new FormData(); fd.append('file', file); fd.append('note_id', id);
          try { const r = await upload('/api/attachments', fd); return r.url; } catch (err) { toastError(err); return null; }
        },
        onUploadFile: async (file) => {
          const fd = new FormData(); fd.append('file', file); fd.append('note_id', id);
          try { const r = await upload('/api/attachments', fd); return { url: r.url, name: r.filename }; } catch (err) { toastError(err); return null; }
        },
        pickImage: () => pickFiles('image/*', (files) => instance.insertFiles(files)),
        searchNotes: async (query) => {
          const res = await get(`/api/notes${qs({ q: query || undefined, limit: 8 })}`);
          return res.items.filter((n) => n.id !== id).map((n) => ({ id: n.id, title: n.title || 'Untitled' }));
        },
        createNote: async (title) => { try { const n = await post('/api/notes', { title }); return { id: n.id, title: n.title }; } catch (err) { toastError(err); return null; } },
        onOpenLink: (href) => { if (href.startsWith('#/')) navigate(href); else window.open(href, '_blank', 'noopener,noreferrer'); },
        extraCommands: () => (getState().ai?.ok ? [
          { title: 'Summarize this note', icon: 'sparkles', group: 'Local AI', keywords: 'ai summary tldr', run: (e, r) => { e.chain().focus().deleteRange(r).run(); openAssist('summarize'); } },
          { title: 'Continue writing', icon: 'sparkles', group: 'Local AI', keywords: 'ai write more', run: (e, r) => { e.chain().focus().deleteRange(r).run(); openAssist('continue'); } },
          { title: 'Find action items', icon: 'sparkles', group: 'Local AI', keywords: 'ai todo tasks', run: (e, r) => { e.chain().focus().deleteRange(r).run(); openAssist('actions'); } },
        ] : []),
        onError: toastError,
      });
      edRef.current = instance;
      setEd(instance);
      setWords(instance.wordCount());
      if (!noteRef.current.body && !noteRef.current.title && titleRef.current) titleRef.current.focus();
    })();
    return () => { destroyed = true; if (instance) instance.destroy(); edRef.current = null; setEd(null); };
  }, [ready, id]);

  const updateBubble = (instance) => {
    const { state, view } = instance.editor;
    const { from, to, empty } = state.selection;
    if (empty || !view.hasFocus()) { setBubble(null); return; }
    const a = view.coordsAtPos(from);
    const b = view.coordsAtPos(to);
    setBubble({ left: Math.max(8, Math.min((a.left + b.right) / 2 - 150, window.innerWidth - 320)), top: Math.max(8, a.top - 46) });
  };

  // Auto-size the title
  const fitTitle = () => { const t = titleRef.current; if (t) { t.style.height = 'auto'; t.style.height = `${t.scrollHeight}px`; } };
  useEffect(fitTitle, [note && note.id, locked]);

  const moveTo = async (nbId) => {
    const target = notebooks.find((b) => b.id === nbId);
    if (note.lib_path && (!target || target.lib_dir == null)) {
      if (!(await confirmDialog({ title: 'Take this note out of your library?', message: `The Markdown file moves to ${note.lib.library}/.daybook/trash and the note stays in Daybook, private.`, confirm: 'Make private' }))) { setNote((n) => ({ ...n })); return; }
    }
    await save();
    await patchMeta({ notebook_id: nbId });
    load();
  };
  const patchMeta = async (patch) => {
    try {
      const saved = await put(`/api/notes/${id}`, patch);
      setNote((n) => ({ ...n, ...patch, rev: saved.rev, updated_at: saved.updated_at }));
      noteRef.current = { ...noteRef.current, rev: saved.rev };
    } catch (err) { toastError(err); }
  };

  const [assistAction, setAssistAction] = useState(null);
  const openAssist = (action) => { setPanel('assist'); setAssistAction({ action, at: Date.now() }); };
  const togglePanel = (which) => {
    const next = panel === which ? '' : which;
    setPanel(next);
    try { localStorage.setItem('daybook_panel', next); } catch { /* ignore */ }
  };

  if (error) return html`<div class="view"><div class="banner banner-error"><${Icon} name="circle-alert" /><div>${error} <a href="#/notes">Back to notes</a></div></div></div>`;
  if (!note) return html`<div class="view"><${Spinner} /></div>`;
  if (locked) return html`<${LockScreen} note=${note} onUnlocked=${(n) => { setNote(n); setLocked(false); }} />`;

  const trashed = !!note.trashed_at;
  const book = notebooks.find((b) => b.id === note.notebook_id);
  const more = (e) => openMenu(e.currentTarget, trashed ? [
    { label: 'Restore note', icon: 'archive-restore', onSelect: async () => { await post(`/api/notes/${id}/restore`); load(); emitChange({ entity: 'notes' }); } },
    { label: 'Delete forever', icon: 'trash-2', danger: true, onSelect: async () => {
      if (await confirmDialog({ title: 'Delete forever?', message: 'This note and its attachments will be gone for good.', confirm: 'Delete forever', danger: true })) {
        await del(`/api/notes/${id}?permanent=true`); navigate(listHref(params)); emitChange({ entity: 'notes' });
      } } },
  ] : [
    note.locked ? null : { label: readable(note) ? `Read the ${readable(note)}` : 'Open in the reader', icon: 'book-open', onSelect: () => navigate(`#/read/${id}`) },
    { label: note.pinned ? 'Unpin' : 'Pin to top', icon: note.pinned ? 'pin-off' : 'pin', onSelect: () => patchMeta({ pinned: !note.pinned }) },
    note.lib_path && !note.editable ? null : { label: 'Duplicate', icon: 'copy', onSelect: async () => { const d = await post(`/api/notes/${id}/duplicate`); navigate(listHref(params, d.id)); } },
    note.lib_path ? null : { label: note.locked ? 'Remove lock' : 'Lock note', icon: note.locked ? 'lock-open' : 'lock', onSelect: () => (note.locked ? removeLock(id, load) : lockNote(id, save, load)) },
    note.lib_path && !note.editable ? { label: 'Make an editable copy', icon: 'copy', onSelect: async () => { const d = await post(`/api/notes/${id}/duplicate`); toast('Editable copy created'); navigate(listHref(params, d.id)); } } : null,
    { label: 'Version history', icon: 'history', onSelect: () => setHistory(true), disabled: note.locked },
    { label: 'Info and links', icon: 'link-2', onSelect: () => togglePanel('info') },
    { sep: true },
    { header: 'Colour' },
    ...[['', 'None'], ['blue', 'Blue'], ['green', 'Green'], ['violet', 'Violet'], ['amber', 'Amber'], ['red', 'Red']].map(([k, l]) => ({ label: l, icon: (note.color || '') === k ? 'check' : null, onSelect: () => patchMeta({ color: k || null }) })),
    { sep: true },
    { label: 'Export as Markdown', icon: 'download', onSelect: () => (note.locked ? toast('Remove the lock to export this note.', { error: true }) : downloadUrl(`/api/notes/${id}/export.md`)) },
    { label: 'Print or save as PDF', icon: 'printer', onSelect: () => window.print() },
    { label: note.archived ? 'Move out of archive' : 'Archive', icon: 'archive', onSelect: () => patchMeta({ archived: !note.archived }) },
    { label: 'Move to trash', icon: 'trash-2', danger: true, onSelect: async () => {
      if (note.lib_path && !(await confirmDialog({ title: 'Move this file to the trash?', message: `${note.lib.filename} moves out of ${note.lib.folder || note.lib.library} into ${note.lib.library}/.daybook/trash. Restore it from Daybook's trash within 30 days.`, confirm: 'Move to trash', danger: true }))) return;
      await save(); await del(`/api/notes/${id}`);
      toast('Moved to trash', { action: { label: 'Undo', run: async () => { await post(`/api/notes/${id}/restore`); navigate(listHref(params, id)); emitChange({ entity: 'notes' }); } } });
      navigate(listHref(params)); emitChange({ entity: 'notes' });
    } },
  ], { align: 'end' });

  return html`<div class="editor-shell">
    <div class="editor-main">
      ${narrow ? html`<div class="row" style="padding:6px 8px;border-bottom:1px solid var(--rule);background:var(--sheet)">
        <a class="btn btn-ghost btn-sm" href=${listHref(params)}><${Icon} name="chevron-left" />Notes</a>
        <span class="grow"></span>
        <button class="btn btn-ghost btn-sm" onClick=${() => togglePanel('info')}><${Icon} name="link-2" />Info</button></div>` : null}
      ${trashed ? null : note.editable === false ? html`<${FileBar} note=${note} narrow=${narrow} onAi=${() => togglePanel('assist')} onMore=${more}
        onPanel=${() => togglePanel(panel ? panel : 'info')} panelOpen=${!!panel} />` : html`<${Toolbar} ed=${ed} state=${tbState} narrow=${narrow} onAi=${() => togglePanel('assist')} onMore=${more}
        onPanel=${() => togglePanel(panel ? panel : 'info')} panelOpen=${!!panel} />`}
      <div class="editor-scroll paper-grid">
        <div class="sheet-page">
          ${trashed ? html`<div class="banner banner-warn" style="margin-bottom:16px"><${Icon} name="trash-2" /><div class="grow">This note is in the trash and will be deleted after 30 days.</div>
            <button class="btn btn-sm" onClick=${more}>Options</button></div>` : null}
          ${conflict ? html`<div class="banner banner-warn conflict"><${Icon} name="triangle-alert" /><div class="grow">
            <strong>This note changed on another device.</strong> Your edits haven't been saved yet.
            <div class="row" style="margin-top:8px"><button class="btn btn-sm" onClick=${async () => { dirty.current = false; const n = await load(); if (n && edRef.current) edRef.current.setMarkdown(n.body); if (titleRef.current && n) titleRef.current.value = n.title; setConflict(null); setStatus('saved'); }}>Load the latest version</button>
            <button class="btn btn-sm btn-primary" onClick=${() => { dirty.current = true; save({ force: true }); }}>Keep my version</button></div></div></div>` : null}
          <div class="note-head">
            <div class="header-block">
              <div class="cell"><span class="label">${note.lib_path || note.lib_pending ? 'Folder' : 'Notebook'}</span>
                <select aria-label="Notebook" value=${note.notebook_id || ''} disabled=${trashed || note.editable === false} onChange=${(e) => moveTo(e.currentTarget.value || null)}>
                  ${note.lib_path ? null : html`<option value="">No notebook</option>`}
                  ${notebooks.filter((b) => !note.lib_path || b.lib_dir != null || note.editable !== false).map((b) => html`<option value=${b.id}>${b.lib_dir != null ? `${b.lib_dir === '' ? 'Top level' : b.name} (folder)` : b.name}</option>`)}</select></div>
              <div class="cell"><span class="label">Date</span>
                <button type="button" class=${`date-cell ${note.date_status === 'review' ? 'is-review' : ''} ${!note.linked_date ? 'is-empty' : ''}`} disabled=${trashed}
                  onClick=${(e) => dateMenu(e.currentTarget, note)} title=${SOURCE[note.date_source] || 'Pick the day this belongs to'}>
                  ${note.linked_date ? dateText(note) : note.date_status === 'suggested' ? 'Needs a date' : 'Add date'}
                  ${note.date_source && note.linked_date ? html`<small>${SOURCE[note.date_source] || ''}</small>` : null}</button></div>
              <div class="cell"><span class="label">Tags</span><${TagsInput} tags=${note.tags || []} onChange=${(tags) => patchMeta({ tags })} /></div>
              <div class="cell"><span class="label">Words</span><span class="value">${(ed ? words : note.word_count).toLocaleString()}</span></div>
            </div>
          </div>
          ${note.lib ? html`<${FileBanner} note=${note} reload=${load} />` : null}
          <textarea ref=${titleRef} class="note-title" rows="1" placeholder="Title" aria-label="Title" readonly=${trashed || note.editable === false}
            defaultValue=${note.title} onInput=${() => { fitTitle(); markDirty(); }}
            onKeyDown=${(e) => { if (e.key === 'Enter') { e.preventDefault(); edRef.current && edRef.current.focus('start'); } }}></textarea>
          ${!trashed && !note.locked ? html`<${TermFixes} note=${note} beforeApply=${() => save()} onApplied=${async () => {
            dirty.current = false; const n = await load(); if (n && edRef.current) edRef.current.setMarkdown(n.body);
            if (titleRef.current && n) titleRef.current.value = n.title; setStatus('saved'); }} />` : null}
          ${note.ai_suggestions && !trashed ? html`<${Suggestions} note=${note} ed=${ed} onChange=${(sug) => setNote((n) => ({ ...n, ai_suggestions: sug }))} onApplied=${load} />` : null}
          ${note.summary ? html`<details class="briefing" style="margin-bottom:16px" open><summary style="cursor:pointer;font-family:var(--ui);font-size:12px;font-weight:600;color:var(--graphite)">Summary</summary>
            <${Markdown} text=${note.summary} /></details>` : null}
          ${note.facts && !trashed ? html`<${MeetingCard} note=${note} />` : null}
          ${note.lib_kind === 'mockup' && note.lib ? html`<${MockupPreview} note=${note} />` : null}
          <div ref=${host} class=${note.editable === false ? 'is-readonly' : ''}></div>
        </div>
      </div>
      <div class="editor-status" role="status">
        <span class=${status === 'saved' ? 'saved' : status === 'saving' ? 'saving' : status === 'error' || status === 'conflict' ? 'error' : ''}>
          ${{ saved: 'Saved', saving: 'Saving…', unsaved: 'Editing…', error: "Couldn't save — retrying when you type", conflict: 'Not saved: conflict' }[status]}</span>
        <span>Edited ${fmtAgo(note.updated_at)}</span>
        ${note.lib ? html`<span class="row" style="gap:4px"><${Icon} name="folder" size="sm" />${note.editable === false ? `Read-only copy of ${note.lib.filename}` : `Saved to ${note.lib.library}/${note.lib.path}`}</span>`
          : note.lib_pending ? html`<span class="row" style="gap:4px"><${Icon} name="folder" size="sm" />Saved to your library when you start writing</span>`
            : note.source_name ? html`<span>Imported from ${note.source_name}</span>` : null}
        ${note.locked ? html`<span class="row" style="gap:4px"><${Icon} name="lock" size="sm" />Encrypted</span>` : null}
        ${book ? html`<span class="row" style="gap:4px;margin-left:auto"><span class="swatch" style=${`background:${book.color}`}></span>${book.name}</span>` : null}
      </div>
    </div>
    ${panel && !trashed ? html`<${SidePanel} which=${panel} setWhich=${togglePanel} note=${note} ed=${ed} id=${id} assist=${assistAction} onClose=${() => togglePanel(panel)} ai=${ai} save=${save} reload=${load} />` : null}
    ${bubble && ed && !trashed ? html`<${Bubble} pos=${bubble} ed=${ed} ai=${ai} onClose=${() => setBubble(null)} />` : null}
    ${history ? html`<${HistoryModal} id=${id} onClose=${() => setHistory(false)} onRestored=${async () => { setHistory(false); dirty.current = false; const n = await load(); if (n && edRef.current) edRef.current.setMarkdown(n.body); if (titleRef.current && n) titleRef.current.value = n.title; }} />` : null}
  </div>`;
}

// ---------------------------------------------------------------------------
// Locking
// ---------------------------------------------------------------------------
async function lockNote(id, save, reload) {
  const st = await get('/api/vault');
  if (!st.set) {
    const p1 = await promptDialog({ title: 'Create a vault passphrase', label: 'Vault passphrase', type: 'password', confirm: 'Continue',
      hint: 'Locked notes are encrypted with this passphrase. It is separate from your sign-in and cannot be recovered if you forget it.' });
    if (!p1) return;
    const p2 = await promptDialog({ title: 'Confirm vault passphrase', label: 'Type it again', type: 'password', confirm: 'Lock note' });
    if (p1 !== p2) { toast("The passphrases don't match.", { error: true }); return; }
    await doLock(id, p1, save, reload);
    return;
  }
  const pass = getState().vault || await promptDialog({ title: 'Lock note', label: 'Vault passphrase', type: 'password', confirm: 'Lock note' });
  if (pass) await doLock(id, pass, save, reload);
}
async function doLock(id, pass, save, reload) {
  try {
    await save();
    await post(`/api/notes/${id}/lock`, { passphrase: pass });
    setState({ vault: pass });
    toast('Note locked and encrypted. Its history, search text, highlights and AI index were removed.');
    await reload();
    emitChange({ entity: 'notes' });
  } catch (err) { toastError(err); }
}
async function removeLock(id, reload) {
  const pass = getState().vault || await promptDialog({ title: 'Remove lock', label: 'Vault passphrase', type: 'password', confirm: 'Remove lock' });
  if (!pass) return;
  try { await post(`/api/notes/${id}/remove-lock`, { passphrase: pass }); toast('Lock removed'); await reload(); emitChange({ entity: 'notes' }); } catch (err) { toastError(err); }
}

function LockScreen({ note, onUnlocked }) {
  const [pass, setPass] = useState('');
  const [err, setErr] = useState('');
  const submit = async (e) => {
    e.preventDefault();
    try {
      const n = await post(`/api/notes/${note.id}/unlock`, { passphrase: pass });
      setState({ vault: pass });
      onUnlocked(n);
    } catch (error) { setErr(error.message); }
  };
  return html`<div class="editor-scroll paper-grid"><form class="lock-screen" onSubmit=${submit}>
    <${Icon} name="lock" size="lg" />
    <h2 class="display" style="font-size:28px">${note.title || 'Locked note'}</h2>
    <p class="muted">This note is encrypted. Enter your vault passphrase to read it. It stays unlocked until you reload Daybook.</p>
    <input class="input" type="password" value=${pass} onInput=${(e) => setPass(e.currentTarget.value)} placeholder="Vault passphrase" autofocus aria-label="Vault passphrase" />
    ${err ? html`<p class="error-text">${err}</p>` : null}
    <button class="btn btn-ink btn-lg">Unlock note</button>
  </form></div>`;
}

// ---------------------------------------------------------------------------
// Selection bubble
// ---------------------------------------------------------------------------
function Bubble({ pos, ed, ai, onClose }) {
  const [busy, setBusy] = useState(false);
  const transform = async (action, custom) => {
    const text = ed.selectionMarkdown() || ed.selectionText();
    if (!text) return;
    setBusy(true);
    try {
      const res = await post('/api/ai/transform', { text, action, custom });
      ed.replaceSelection(res.markdown);
      toast(`Rewritten by ${res.model} in ${(res.ms / 1000).toFixed(1)}s`, { action: { label: 'Undo', run: () => ed.chain().undo().run() } });
      onClose();
    } catch (err) { toastError(err); }
    setBusy(false);
  };
  const aiMenu = (e) => openMenu(e.currentTarget, [
    { header: 'Rewrite with local AI' },
    { label: 'Improve writing', icon: 'wand-sparkles', onSelect: () => transform('improve') },
    { label: 'Make shorter', icon: 'minus', onSelect: () => transform('shorten') },
    { label: 'Expand', icon: 'plus', onSelect: () => transform('expand') },
    { label: 'Turn into bullets', icon: 'list', onSelect: () => transform('bullets') },
    { label: 'Turn into a checklist', icon: 'list-checks', onSelect: () => transform('checklist') },
    { label: 'Professional tone', icon: 'mail', onSelect: () => transform('professional') },
    { label: 'Plain language', icon: 'book-open', onSelect: () => transform('simplify') },
    { label: 'Custom instruction…', icon: 'pencil', onSelect: async () => { const c = await promptDialog({ title: 'Rewrite selection', label: 'Instruction', placeholder: 'e.g. translate to Spanish', confirm: 'Rewrite' }); if (c) transform('custom', c); } },
  ]);
  const c = () => ed.chain();
  return html`<div class="bubble" style=${`left:${pos.left}px;top:${pos.top}px`} onMouseDown=${(e) => e.preventDefault()}>
    <button aria-label="Bold" aria-pressed=${String(ed.isActive('bold'))} onClick=${() => c().toggleBold().run()}><${Icon} name="bold" /></button>
    <button aria-label="Italic" aria-pressed=${String(ed.isActive('italic'))} onClick=${() => c().toggleItalic().run()}><${Icon} name="italic" /></button>
    <button aria-label="Highlight" aria-pressed=${String(ed.isActive('highlight'))} onClick=${() => c().toggleHighlight().run()}><${Icon} name="highlighter" /></button>
    <button aria-label="Link" onClick=${async () => { const u = await promptDialog({ title: 'Link', label: 'Address', value: ed.editor.getAttributes('link').href || '', confirm: 'Apply' }); if (u !== null) (u ? c().extendMarkRange('link').setLink({ href: u }).run() : c().unsetLink().run()); }}><${Icon} name="link" /></button>
    ${ed.isActive('link') ? html`<button onClick=${() => { const h = ed.editor.getAttributes('link').href; if (h) (h.startsWith('#/') ? navigate(h) : window.open(h, '_blank', 'noopener,noreferrer')); }}><${Icon} name="external-link" />Open</button>` : null}
    ${ai?.ok ? html`<span class="sep"></span><button onClick=${aiMenu} disabled=${busy}>${busy ? html`<span class="spinner"></span>` : html`<${Icon} name="sparkles" />`}Rewrite</button>` : null}
  </div>`;
}

// ---------------------------------------------------------------------------
// AI suggestions saved on imported notes
// ---------------------------------------------------------------------------
function Suggestions({ note, ed, onChange, onApplied }) {
  const s = note.ai_suggestions || {};
  const [picked, setPicked] = useState(() => (s.action_items || []).map(() => true));
  const store = async (next) => {
    const empty = !next || (!next.title && !(next.tags || []).length && !next.summary && !(next.action_items || []).length && !(next.events || []).length);
    try { await put(`/api/notes/${note.id}/suggestions`, { suggestions: empty ? null : next }); onChange(empty ? null : next); emitChange({ entity: 'notes', id: note.id }); }
    catch (err) { toastError(err); }
  };
  const applyTitle = async () => { await put(`/api/notes/${note.id}`, { title: s.title }); await store({ ...s, title: '' }); onApplied(); };
  const applyTags = async () => { await put(`/api/notes/${note.id}`, { tags: [...new Set([...(note.tags || []), ...s.tags])] }); await store({ ...s, tags: [] }); onApplied(); };
  const applySummary = async () => { await put(`/api/notes/${note.id}`, { summary: s.summary }); await store({ ...s, summary: '' }); onApplied(); };
  const createTasks = async () => {
    const items = (s.action_items || []).filter((_, i) => picked[i]);
    for (const it of items) await post('/api/tasks', { title: it.title, due_date: it.due, priority: it.priority, note_id: note.id });
    toast(`Created ${items.length} task${items.length === 1 ? '' : 's'}`);
    emitChange({ entity: 'tasks' });
    await store({ ...s, action_items: [] });
  };
  const addEvent = (ev, i) => {
    openEventEditor({ date: ev.date, time: ev.start_time || undefined, all_day: ev.all_day, title: ev.title });
    store({ ...s, events: s.events.filter((_, j) => j !== i) });
  };
  return html`<section class="ai-result" style="margin-bottom:18px" aria-label="Suggestions from local AI">
    <div class="ai-result-head"><span class="row" style="gap:6px;color:var(--violet);font-weight:650"><${Icon} name="sparkles" />Filing suggestions from your local AI</span>
      <button class="btn btn-ghost btn-sm" onClick=${() => store(null)}>Dismiss all</button></div>
    <div class="ai-result-body stack">
      ${s.title && s.title !== note.title ? html`<div class="suggest-item"><span class="grow"><span class="label">Title</span><br />${s.title}</span><button class="btn btn-sm" onClick=${applyTitle}>Use title</button></div>` : null}
      ${(s.tags || []).length ? html`<div class="suggest-item"><span class="grow"><span class="label">Tags</span><br />${s.tags.map((t) => html`<span class="chip" style="margin:2px">#${t}</span>`)}</span><button class="btn btn-sm" onClick=${applyTags}>Add tags</button></div>` : null}
      ${s.summary ? html`<div class="suggest-item"><span class="grow"><span class="label">Summary</span><${Markdown} text=${s.summary} /></span><button class="btn btn-sm" onClick=${applySummary}>Keep summary</button></div>` : null}
      ${(s.action_items || []).length ? html`<div class="suggest-item" style="flex-direction:column;align-items:stretch"><span class="label">Action items</span>
        ${s.action_items.map((it, i) => html`<label class="row" style="padding:3px 0"><input type="checkbox" class="check" checked=${picked[i]} onChange=${(e) => setPicked(picked.map((p, j) => (j === i ? e.currentTarget.checked : p)))} />
          <span class="grow">${it.title}${it.due ? html` <span class="faint">(due ${fmtDate(it.due)})</span>` : null}</span></label>`)}
        <div><button class="btn btn-sm btn-primary" onClick=${createTasks}>Create selected tasks</button></div></div>` : null}
      ${(s.events || []).length ? html`<div class="suggest-item" style="flex-direction:column;align-items:stretch"><span class="label">Dates found</span>
        ${s.events.map((ev, i) => html`<div class="row" style="padding:3px 0"><span class="grow">${ev.title} <span class="faint">${fmtDate(ev.date, { weekday: true })}${ev.start_time ? `, ${ev.start_time}` : ''}</span></span>
          <button class="btn btn-sm" onClick=${() => addEvent(ev, i)}>Add to calendar</button></div>`)}</div>` : null}
      ${s.model ? html`<span class="ai-stamp">${s.model}${s.ms ? `, ${(s.ms / 1000).toFixed(1)}s` : ''}, on your hardware</span>` : null}
    </div>
  </section>`;
}

// ---------------------------------------------------------------------------
// Side panel: Assist (local AI) and Info
// ---------------------------------------------------------------------------
function SidePanel({ which, setWhich, note, ed, id, assist, onClose, ai, save, reload }) {
  return html`<aside class="side-panel" aria-label="Side panel">
    <div class="side-panel-head">
      <${Seg} value=${which} onChange=${(v) => setWhich(v)} options=${[{ value: 'assist', label: 'Assist' }, { value: 'info', label: 'Info' }]} label="Panel" />
      <button class="btn btn-ghost btn-icon btn-sm" onClick=${onClose} aria-label="Close panel"><${Icon} name="x" /></button>
    </div>
    <div class="side-panel-body">
      ${which === 'assist' ? html`<${Assist} note=${note} ed=${ed} id=${id} trigger=${assist} ai=${ai} save=${save} reload=${reload} />` : html`<${Info} note=${note} id=${id} ai=${ai} />`}
    </div>
  </aside>`;
}

function Assist({ note, ed, id, trigger, ai, save, reload }) {
  const [results, setResults] = useState([]);
  const [busy, setBusy] = useState(null);
  const [question, setQuestion] = useState('');
  const [answer, setAnswer] = useState(null);

  const run = async (action, extra = {}) => {
    if (busy) return;
    await save();
    setBusy(action);
    try {
      const res = await post(`/api/ai/note/${id}/${action}`, extra);
      setResults((r) => [{ action, style: extra.style, res, at: Date.now() }, ...r].slice(0, 6));
    } catch (err) { toastError(err); }
    setBusy(null);
  };
  useEffect(() => { if (trigger && ai?.ok) run(trigger.action); }, [trigger && trigger.at]);

  const ask = async (e) => {
    e.preventDefault();
    if (!question.trim()) return;
    await save();
    const a = { q: question, text: '', done: false };
    setAnswer({ ...a });
    try {
      await streamNdjson(`/api/ai/ask-note/${id}`, { question }, (evt) => {
        if (evt.type === 'delta') { a.text += evt.text; setAnswer({ ...a }); }
        if (evt.type === 'done') { a.done = true; a.stats = evt; setAnswer({ ...a }); }
        if (evt.type === 'error') { a.done = true; a.text += `\n\n_${evt.message}_`; setAnswer({ ...a }); }
      });
    } catch (err) { toastError(err); setAnswer(null); }
  };

  if (!ai || !ai.enabled) return html`<div class="stack"><p class="muted">Local AI is turned off.</p><a class="btn" href="#/settings/ai">Set up local AI</a></div>`;
  if (!ai.ok) return html`<div class="stack"><div class="banner banner-warn"><${Icon} name="cloud-off" /><div>${ai.error || "Your local AI app isn't reachable."}</div></div><a class="btn" href="#/settings/ai">Check AI settings</a></div>`;
  if (note.locked) return html`<div class="banner"><${Icon} name="lock" /><div>Locked notes are never sent to AI, even locally.</div></div>`;

  const cleanupMenu = (e) => openMenu(e.currentTarget, [
    { label: 'Organize with headings', icon: 'list-tree', onSelect: () => run('cleanup', { style: 'structure' }) },
    { label: 'Make it concise', icon: 'minus', onSelect: () => run('cleanup', { style: 'concise' }) },
    { label: 'Fix spelling and grammar', icon: 'check', onSelect: () => run('cleanup', { style: 'fix' }) },
    { label: 'Format as meeting notes', icon: 'users', onSelect: () => run('cleanup', { style: 'meeting' }) },
    { label: 'Turn a transcript into notes', icon: 'audio-lines', onSelect: () => run('cleanup', { style: 'transcript' }) },
  ]);
  const btn = (action, icon, label) => html`<button class="btn btn-sm" onClick=${() => run(action)} disabled=${!!busy}>${busy === action ? html`<span class="spinner"></span>` : html`<${Icon} name=${icon} />`}${label}</button>`;
  return html`
    <p class="muted" style="font-size:13px;line-height:1.5">Runs on ${ai.picked?.smart || 'your local model'}. Nothing leaves your network. Results are suggestions; nothing changes until you choose.</p>
    <div class="ai-actions">
      ${btn('summarize', 'sparkles', 'Summarize')}
      ${btn('actions', 'list-checks', 'Action items')}
      ${btn('events', 'calendar-clock', 'Find dates')}
      ${btn('meta', 'tag', 'Title & tags')}
      <button class="btn btn-sm" onClick=${cleanupMenu} disabled=${!!busy}>${busy === 'cleanup' ? html`<span class="spinner"></span>` : html`<${Icon} name="wand-sparkles" />`}Clean up<${Icon} name="chevron-down" size="sm" /></button>
      ${btn('continue', 'pencil', 'Continue')}
    </div>
    <form class="capture" onSubmit=${ask}><input value=${question} onInput=${(e) => setQuestion(e.currentTarget.value)} placeholder="Ask about this note" aria-label="Ask about this note" />
      <button class="btn btn-sm btn-primary" disabled=${!question.trim()}>Ask</button></form>
    ${answer ? html`<div class="ai-result"><div class="ai-result-head"><span>${answer.q}</span><button class="btn btn-ghost btn-sm" onClick=${() => setAnswer(null)} aria-label="Dismiss"><${Icon} name="x" /></button></div>
      <div class="ai-result-body"><div class=${answer.done ? '' : 'typing'}><${Markdown} text=${answer.text || '…'} /></div>
      ${answer.stats ? html`<p class="ai-stamp">${answer.stats.model}, ${(answer.stats.ms / 1000).toFixed(1)}s</p>` : null}</div></div>` : null}
    ${results.map((r) => html`<${AiResult} key=${r.at} r=${r} ed=${ed} note=${note} id=${id} reload=${reload}
      onDismiss=${() => setResults((list) => list.filter((x) => x !== r))} />`)}
  `;
}

const ACTION_TITLES = { summarize: 'Summary', actions: 'Action items', events: 'Dates found', meta: 'Title and tags', cleanup: 'Rewritten note', continue: 'Continuation' };

function AiResult({ r, ed, note, id, reload, onDismiss }) {
  const { action, res } = r;
  const [picked, setPicked] = useState(() => (res.items || []).map(() => true));
  const stamp = html`<span class="ai-stamp">${res.model}, ${(res.ms / 1000).toFixed(1)}s</span>`;
  const insertTop = (md) => { ed.chain().focus('start').insertContentAt(0, md, { contentType: 'markdown' }).run(); toast('Inserted at the top'); };
  const insertEnd = (md) => { ed.chain().focus('end').insertContent(md, { contentType: 'markdown' }).run(); toast('Added to the end'); };
  let body = null;
  let foot = null;
  if (action === 'summarize' || action === 'continue') {
    body = html`<${Markdown} text=${res.markdown} />`;
    foot = action === 'summarize'
      ? html`<button class="btn btn-sm btn-primary" onClick=${async () => { await put(`/api/notes/${id}`, { summary: res.markdown }); reload(); onDismiss(); toast('Saved as the note summary'); }}>Save as summary</button>
        <button class="btn btn-sm" onClick=${() => insertTop(`## Summary\n\n${res.markdown}\n\n`)}>Insert at top</button>
        <button class="btn btn-sm" onClick=${() => navigator.clipboard?.writeText(res.markdown).then(() => toast('Copied'))}>Copy</button>`
      : html`<button class="btn btn-sm btn-primary" onClick=${() => { insertEnd(`\n\n${res.markdown}`); onDismiss(); }}>Add to note</button>`;
  } else if (action === 'actions') {
    body = res.items.length ? res.items.map((it, i) => html`<label class="row" style="padding:4px 0;align-items:flex-start">
        <input type="checkbox" class="check" checked=${picked[i]} onChange=${(e) => setPicked(picked.map((p, j) => (j === i ? e.currentTarget.checked : p)))} />
        <span class="grow" style="font-size:13px">${it.title}${it.owner ? html` <span class="faint">(${it.owner})</span>` : null}${it.due ? html` <span class="faint">due ${fmtDate(it.due)}</span>` : null}</span></label>`)
      : html`<p class="muted">No action items found.</p>`;
    foot = res.items.length ? html`
      <button class="btn btn-sm btn-primary" onClick=${async () => {
        const items = res.items.filter((_, i) => picked[i]);
        try { for (const it of items) await post('/api/tasks', { title: it.title, due_date: it.due, priority: it.priority, note_id: id }); toast(`Created ${items.length} tasks`); emitChange({ entity: 'tasks' }); reload(); onDismiss(); } catch (err) { toastError(err); }
      }}>Create tasks</button>
      <button class="btn btn-sm" onClick=${() => { insertEnd(`\n\n## Action items\n\n${res.items.filter((_, i) => picked[i]).map((it) => `- [ ] ${it.title}${it.due ? ` (due ${it.due})` : ''}`).join('\n')}\n`); onDismiss(); }}>Insert checklist</button>` : null;
  } else if (action === 'events') {
    body = res.events.length ? res.events.map((ev) => html`<div class="row" style="padding:4px 0"><span class="grow" style="font-size:13px">${ev.title}<br /><span class="faint">${fmtDate(ev.date, { weekday: true })}${ev.start_time ? `, ${ev.start_time}${ev.end_time ? `–${ev.end_time}` : ''}` : ''}</span></span>
        <button class="btn btn-sm" onClick=${() => openEventEditor({ date: ev.date, time: ev.start_time || undefined, all_day: ev.all_day, title: ev.title })}>Add</button></div>`)
      : html`<p class="muted">No dated items found.</p>`;
  } else if (action === 'meta') {
    body = html`<div class="stack"><div><span class="label">Title</span><div style="font-weight:600">${res.title}</div></div>
      <div><span class="label">Tags</span><div class="row row-wrap" style="gap:4px">${res.tags.map((t) => html`<span class="chip">#${t}</span>`)}</div></div></div>`;
    foot = html`<button class="btn btn-sm btn-primary" onClick=${async () => { await put(`/api/notes/${id}`, { title: res.title, tags: [...new Set([...(note.tags || []), ...res.tags])] }); reload(); onDismiss(); toast('Applied'); }}>Apply both</button>
      <button class="btn btn-sm" onClick=${async () => { await put(`/api/notes/${id}`, { title: res.title }); reload(); toast('Title applied'); }}>Title only</button>
      <button class="btn btn-sm" onClick=${async () => { await put(`/api/notes/${id}`, { tags: [...new Set([...(note.tags || []), ...res.tags])] }); reload(); toast('Tags added'); }}>Tags only</button>`;
  } else if (action === 'cleanup') {
    body = html`<${Markdown} text=${res.markdown} />`;
    foot = html`<button class="btn btn-sm btn-primary" onClick=${() => { ed.setMarkdown(res.markdown, true); toast('Note replaced. The previous version is in Version history.', { action: { label: 'Undo', run: () => ed.chain().undo().run() } }); onDismiss(); }}>Replace note</button>
      <button class="btn btn-sm" onClick=${() => { insertEnd(`\n\n---\n\n${res.markdown}`); onDismiss(); }}>Add below</button>`;
  }
  return html`<div class="ai-result"><div class="ai-result-head"><span style="font-weight:650;color:var(--ink)">${ACTION_TITLES[action]}${r.style ? ` (${r.style})` : ''}</span>
      <button class="btn btn-ghost btn-sm" onClick=${onDismiss} aria-label="Dismiss"><${Icon} name="x" /></button></div>
    <div class="ai-result-body">${body}${stamp}</div>
    ${foot ? html`<div class="ai-result-foot">${foot}</div>` : null}</div>`;
}

function Info({ note, id, ai }) {
  const [related, setRelated] = useState(null);
  useEffect(() => {
    if (!ai?.ok || note.locked) { setRelated([]); return; }
    get(`/api/ai/related/${id}`).then(setRelated).catch(() => setRelated([]));
  }, [id]);
  const removeAttachment = async (a) => {
    if (!(await confirmDialog({ title: 'Delete attachment?', message: `${a.filename} will be deleted from this computer.`, confirm: 'Delete', danger: true }))) return;
    try { await del(`/api/attachments/${a.id}`); emitChange({ entity: 'notes', id }); } catch (err) { toastError(err); }
  };
  return html`
    <div><div class="section-title">Linked here <span class="count">${note.backlinks?.length || 0}</span></div>
      ${note.backlinks?.length ? html`<div class="link-list">${note.backlinks.map((b) => html`<a href=${`#/notes/${b.id}`}><${Icon} name="corner-down-left" />${b.title || 'Untitled'}</a>`)}</div>`
        : html`<p class="muted" style="font-size:13px;margin-top:4px">Type <span class="kbd">[[</span> in any note to link to this one.</p>`}</div>
    ${ai?.ok ? html`<div><div class="section-title">Related notes</div>
      ${related === null ? html`<${Spinner} />` : related.length ? html`<div class="link-list">${related.map((r) => html`<a href=${`#/notes/${r.id}`}><${Icon} name="file-text" />${r.title || 'Untitled'}</a>`)}</div>`
        : html`<p class="muted" style="font-size:13px;margin-top:4px">Nothing similar yet${ai.picked?.embed ? '' : '. Install an embedding model to find related notes'}.</p>`}</div>` : null}
    <div><div class="section-title">Attachments <span class="count">${note.attachments?.length || 0}</span></div>
      ${note.attachments?.length ? html`<div class="link-list">${note.attachments.map((a) => html`<div class="row" style="border-bottom:1px solid var(--rule)">
        <a href=${`/api/attachments/${a.id}?download=true`} class="grow" style="border:0"><${Icon} name=${a.role === 'original' ? 'file-up' : (a.mime || '').startsWith('image/') ? 'file-image' : 'file'} />
          <span class="grow">${a.filename}<span class="faint" style="font-size:11px;display:block">${a.role === 'original' ? 'Original file, ' : ''}${fmtBytes(a.size)}</span></span></a>
        <button class="btn btn-ghost btn-icon btn-sm" onClick=${() => removeAttachment(a)} aria-label=${`Delete ${a.filename}`}><${Icon} name="trash-2" /></button></div>`)}</div>`
        : html`<p class="muted" style="font-size:13px;margin-top:4px">Drop files into the note to attach them.</p>`}</div>
    ${note.tasks?.length ? html`<div><div class="section-title">Tasks from this note</div><div class="link-list">${note.tasks.map((t) => html`<a href="#/tasks/open"><${Icon} name=${t.done ? 'circle-check' : 'square'} />${t.title}</a>`)}</div></div>` : null}
    ${note.events?.length ? html`<div><div class="section-title">Events</div><div class="link-list">${note.events.map((e) => html`<a href=${`#/calendar/${e.start.slice(0, 10)}`}><${Icon} name="calendar" />${e.title}<span class="faint" style="margin-left:auto">${fmtDate(e.start.slice(0, 10))}</span></a>`)}</div></div>` : null}
    <div><div class="section-title">Why this date</div><${WhyDate} note=${note} />
      <button class="btn btn-ghost btn-sm" style="margin-top:4px" onClick=${() => openDateDialog(note)}><${Icon} name="calendar" />Change</button></div>
    ${note.mentions?.length ? html`<div><div class="section-title">Dates it mentions <span class="count">${note.mentions.length}</span></div>
      <div class="link-list">${note.mentions.slice(0, 14).map((m) => html`<a href=${`#/calendar/${m.date}`} title=${m.snippet}>
        <${Icon} name=${m.kind === 'deadline' ? 'flag' : 'calendar'} /><span class="grow"><strong>${fmtDate(m.date)}</strong>${m.date_end ? `–${fmtDate(m.date_end)}` : ''}
        <span class="faint" style="display:block;font-size:11.5px;line-height:1.35">${m.snippet.slice(0, 120)}</span></span></a>`)}</div></div>` : null}
    <div class="faint" style="font-size:12px;line-height:1.6">Created ${fmtDate(new Date(note.created_at), { year: true })}<br />Last edited ${fmtAgo(note.updated_at)}</div>
  `;
}

// ---------------------------------------------------------------------------
// Version history
// ---------------------------------------------------------------------------
function HistoryModal({ id, onClose, onRestored }) {
  const [list, setList] = useState(null);
  const [sel, setSel] = useState(null);
  const [rev, setRev] = useState(null);
  useEffect(() => { get(`/api/notes/${id}/revisions`).then((l) => { setList(l); if (l.length) setSel(l[0].id); }).catch(toastError); }, []);
  useEffect(() => { if (sel) get(`/api/notes/${id}/revisions/${sel}`).then(setRev).catch(toastError); }, [sel]);
  const restore = async () => {
    if (!(await confirmDialog({ title: 'Restore this version?', message: 'The current text is saved in history first, so you can switch back.', confirm: 'Restore version' }))) return;
    try { await post(`/api/notes/${id}/revisions/${sel}/restore`); toast('Version restored'); onRestored(); } catch (err) { toastError(err); }
  };
  return html`<${Modal} title="Version history" wide=${true} onClose=${onClose} footer=${html`<button class="btn" onClick=${onClose}>Close</button>
      <button class="btn btn-primary" disabled=${!sel} onClick=${restore}>Restore this version</button>`}>
    ${!list ? html`<${Spinner} />` : !list.length ? html`<${Empty} title="No earlier versions yet."><span>Daybook keeps a version every 10 minutes while you edit, and before big changes.</span></${Empty}>`
      : html`<div style="display:grid;grid-template-columns:200px 1fr;gap:16px">
        <div class="link-list" style="max-height:60dvh;overflow:auto">${list.map((r) => html`<button style=${r.id === sel ? 'color:var(--blue);font-weight:650' : ''} onClick=${() => setSel(r.id)}>
          <${Icon} name="history" /><span>${fmtDate(new Date(r.created_at), { weekday: true })}<span class="faint" style="display:block;font-size:11px">${new Date(r.created_at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}, ${r.words} words</span></span></button>`)}</div>
        <div style="max-height:60dvh;overflow:auto;border-left:1px solid var(--rule);padding-left:16px">${rev ? html`<h3 class="serif" style="font-size:20px;margin-bottom:10px">${rev.title || 'Untitled'}</h3><${Markdown} text=${rev.body} />` : html`<${Spinner} />`}</div>
      </div>`}
  </${Modal}>`;
}


// ---------------------------------------------------------------------------
// Library files: toolbar, banner, meeting card, mockup preview, compare
// ---------------------------------------------------------------------------
// What the reader shows for a note that came from a file: its slides, pages, sheet or picture.
const READ_EXT = { pptx: 'slides', pptm: 'slides', pdf: 'PDF', xlsx: 'spreadsheet', xlsm: 'spreadsheet', csv: 'spreadsheet', tsv: 'spreadsheet',
  png: 'picture', jpg: 'picture', jpeg: 'picture', gif: 'picture', webp: 'picture' };
export function readable(note) {
  const name = note.lib_path || (note.attachments || []).find((a) => a.role === 'original')?.filename || '';
  const ext = (name.split('.').pop() || '').toLowerCase();
  return name ? READ_EXT[ext] || null : null;
}

function FileBar({ note, narrow, onAi, onMore, onPanel, panelOpen }) {
  const local = useIsLocal();
  const lib = note.lib || {};
  const app = { document: 'Word', deck: 'PowerPoint', sheet: 'Excel', pdf: 'Preview', mockup: 'your browser', email: 'Mail', image: 'Preview' }[note.lib_kind] || 'its app';
  const open = async (what) => { try { await post(`/api/library/${what}/${note.id}`); } catch (err) { toastError(err); } };
  return html`<div class="editor-toolbar file-bar" role="toolbar" aria-label="File">
    <span class="file-kind"><${Icon} name=${kindOf(note).icon} />${narrow ? '' : lib.kind_label}</span>
    ${local ? html`<button class="tb tb-text" type="button" onClick=${() => open('open')} title=${`Open the original in ${app}`}><${Icon} name="square-arrow-out-up-right" />${narrow ? '' : `Open in ${app}`}</button>
      <button class="tb tb-text" type="button" onClick=${() => open('reveal')} title="Show in Finder"><${Icon} name="folder-open" />${narrow ? '' : 'Show in Finder'}</button>` : null}
    <a class="tb tb-text" href=${`${lib.url}&download=true`} title="Download the original"><${Icon} name="download" />${narrow ? '' : 'Download'}</a>
    <span class="tb-sep"></span>
    <a class="tb tb-text tb-read" href=${`#/read/${note.id}`} title=${readable(note) ? `Read the ${readable(note)} as it looks, with highlights` : 'Read and highlight'}><${Icon} name="book-open" />${narrow ? '' : (note.lib_kind === 'deck' ? 'Slides' : 'Read')}</a>
    <button class="tb tb-text tb-ai" type="button" onClick=${onAi} title="Local AI assist"><${Icon} name="sparkles" />${narrow ? '' : 'Assist'}</button>
    <span style="flex:1"></span>
    ${narrow ? null : html`<button class="tb" type="button" title=${panelOpen ? 'Hide side panel' : 'Show side panel'} aria-pressed=${panelOpen ? 'true' : 'false'} onClick=${onPanel}><${Icon} name="panel-left" /></button>`}
    <button class="tb" type="button" title="More" onClick=${onMore}><${Icon} name="ellipsis" /></button>
  </div>`;
}

function FileBanner({ note, reload }) {
  const ai = useStore((s) => s.ai);
  const [busy, setBusy] = useState(false);
  const lib = note.lib;
  const rel = note.related || {};
  const draft = async () => {
    setBusy(true);
    try {
      const n = await post(`/api/library/transcript-notes/${note.id}`, {});
      toast(`Meeting notes drafted by your local AI and saved to ${n.lib_path || 'your notes'}`);
      emitChange({ entity: 'notes' });
      navigate(`#/notes/${n.id}`);
    } catch (err) { toastError(err); }
    setBusy(false);
  };
  return html`<div class="file-banner">
    <div class="path"><${Icon} name="folder" size="sm" /><span class="mono">${lib.library}/${lib.path}</span>
      ${lib.status === 'error' || lib.error ? html`<span class="red-text" title=${lib.error || ''}> · couldn't fully read</span>` : null}</div>
    ${lib.doc_title && lib.doc_title !== note.title ? html`<div class="faint" style="font-size:12px">Inside the file: “${lib.doc_title}”${lib.slides ? ` · ${lib.slides} slides` : ''}</div>`
      : lib.slides ? html`<div class="faint" style="font-size:12px">${lib.slides} slides</div>` : null}
    ${(rel.recordings || []).length ? html`<div class="rel-line"><${Icon} name="audio-lines" size="sm" />Recording: ${rel.recordings.map((r) => html`<a href=${`#/notes/${r.id}`}>${r.title}</a>`)}</div>` : null}
    ${(rel.notes_for_recording || []).length ? html`<div class="rel-line"><${Icon} name="file-text" size="sm" />Notes from this recording: ${rel.notes_for_recording.map((r) => html`<a href=${`#/notes/${r.id}`}>${r.title}</a>`)}</div>` : null}
    ${note.lib_kind === 'transcript' && ai?.ok && !(rel.notes_for_recording || []).length ? html`<div class="rel-line">
      <button class="btn btn-sm" onClick=${draft} disabled=${busy}>${busy ? html`<span class="spinner"></span>` : html`<${Icon} name="sparkles" />`}Draft meeting notes from this recording</button>
      <span class="faint" style="font-size:12px">Runs on ${ai.picked?.smart}; saved as a Markdown note next to the recording.</span></div>` : null}
    ${(rel.family || []).length ? html`<${Family} note=${note} />` : null}
  </div>`;
}

function Family({ note }) {
  const [diff, setDiff] = useState(null);
  const fam = note.related.family;
  const versions = note.related.family_kind === 'versions';
  const prev = [...fam].filter((f) => (f.linked_date || '') <= (note.linked_date || '') && f.id !== note.id).pop();
  return html`<div class="rel-line family"><${Icon} name=${versions ? 'git-compare' : 'history'} size="sm" />
    <span>${versions ? 'Other versions' : 'Same series'}:</span>
    ${fam.slice(-6).map((f) => html`<a href=${`#/notes/${f.id}`}>${f.version || (f.linked_date ? fmtDate(f.linked_date) : f.title)}</a>`)}
    ${versions && prev ? html`<button class="btn btn-ghost btn-sm" onClick=${() => setDiff({ a: prev.id, b: note.id })}><${Icon} name="git-compare" />Compare with ${prev.version || fmtDate(prev.linked_date)}</button>` : null}
    ${diff ? html`<${CompareModal} a=${diff.a} b=${diff.b} onClose=${() => setDiff(null)} />` : null}
  </div>`;
}

function CompareModal({ a, b, onClose }) {
  const ai = useStore((s) => s.ai);
  const [d, setD] = useState(null);
  const [sum, setSum] = useState(null);
  const [busy, setBusy] = useState(false);
  const [onlyChanges, setOnlyChanges] = useState(true);
  useEffect(() => { get(`/api/library/diff${qs({ a, b })}`).then(setD).catch(toastError); }, [a, b]);
  const summarize = async () => { setBusy(true); try { setSum(await post('/api/library/diff/summary', { a, b })); } catch (err) { toastError(err); } setBusy(false); };
  return html`<${Modal} title="What changed" wide=${true} onClose=${onClose} footer=${html`<button class="btn" onClick=${onClose}>Close</button>`}>
    ${!d ? html`<${Spinner} />` : html`<div class="stack">
      <div class="row row-wrap" style="gap:10px;font-size:13px">
        <span><span class="diff-del-key"></span>${d.a.title}${d.a.date ? ` (${fmtDate(d.a.date)})` : ''}</span>
        <${Icon} name="arrow-right-left" size="sm" />
        <span><span class="diff-add-key"></span>${d.b.title}${d.b.date ? ` (${fmtDate(d.b.date)})` : ''}</span>
        <span class="grow"></span><span class="faint">+${d.added} −${d.removed}</span>
        <label class="row" style="gap:6px"><input type="checkbox" class="check" checked=${onlyChanges} onChange=${(e) => setOnlyChanges(e.currentTarget.checked)} />Only changes</label>
        ${ai?.ok ? html`<button class="btn btn-sm" onClick=${summarize} disabled=${busy}>${busy ? html`<span class="spinner"></span>` : html`<${Icon} name="sparkles" />`}Summarize changes</button>` : null}
      </div>
      ${sum ? html`<div class="ai-result"><div class="ai-result-body"><${Markdown} text=${sum.markdown} />${sum.model ? html`<p class="ai-stamp">${sum.model}, ${(sum.ms / 1000).toFixed(1)}s</p>` : null}</div></div>` : null}
      <div class="diff">${d.lines.filter((l) => !onlyChanges || l.op !== 'same').map((l) => html`<div class=${`dl dl-${l.op}`}>${l.op === 'add' ? '+ ' : l.op === 'del' ? '− ' : '  '}${l.text}</div>`)}
        ${!d.added && !d.removed ? html`<p class="muted">The text is the same.</p>` : null}</div>
    </div>`}
  </${Modal}>`;
}

function MeetingCard({ note }) {
  const f = note.facts;
  const [open, setOpen] = useState(() => { try { return localStorage.getItem('daybook_meeting_card') !== '0'; } catch { return true; } });
  const toggle = () => { setOpen(!open); try { localStorage.setItem('daybook_meeting_card', open ? '0' : '1'); } catch { /* ignore */ } };
  const tracked = new Set((note.tasks || []).map((t) => t.title.trim().toLowerCase()));
  const track = async (i) => {
    try { await post('/api/library/followups/track', { note_id: note.id, index: i }); toast('Added to Tasks'); emitChange({ entity: 'notes', id: note.id }); emitChange({ entity: 'tasks' }); }
    catch (err) { toastError(err); }
  };
  const open_actions = (f.actions || []).filter((a) => !a.done);
  return html`<section class="meeting-card">
    <button class="meeting-head" onClick=${toggle} aria-expanded=${String(open)}>
      <${Icon} name="clipboard-list" /><strong>At a glance</strong>
      <span class="faint">${[f.people?.length ? `${f.people.length} people` : '', f.decisions?.length ? `${f.decisions.length} decisions` : '', open_actions.length ? `${open_actions.length} action items` : ''].filter(Boolean).join(' · ')}</span>
      <span class="grow"></span><${Icon} name=${open ? 'chevron-up' : 'chevron-down'} size="sm" /></button>
    ${open ? html`<div class="meeting-body">
      ${f.people?.length ? html`<div class="people-line">${f.people.map((p) => html`<a class="chip" href=${`#/library/timeline?person=${encodeURIComponent(p)}`}>${p}</a>`)}</div>` : null}
      ${f.decisions?.length ? html`<div><div class="section-title">Decisions</div><ul class="tight">${f.decisions.slice(0, 8).map((d) => html`<li>${d}</li>`)}</ul>
        ${f.decisions.length > 8 ? html`<a class="faint" style="font-size:12px" href="#/library/decisions">${f.decisions.length - 8} more in the decisions log</a>` : null}</div>` : null}
      ${f.actions?.length ? html`<div><div class="section-title">Action items</div>
        ${f.actions.slice(0, 12).map((a, i) => html`<div class=${`fu-row compact ${a.done ? 'done' : ''}`}>
          <span class="grow">${a.text}${a.owner ? html` <span class="chip">${a.owner}</span>` : null}${a.due ? html` <span class="faint">due ${fmtDate(a.due)}</span>` : null}</span>
          ${tracked.has(a.text.trim().toLowerCase()) ? html`<span class="faint" style="font-size:12px"><${Icon} name="check" size="sm" />In Tasks</span>`
            : a.done ? null : html`<button class="btn btn-ghost btn-sm" onClick=${() => track(i)}><${Icon} name="plus" />Track</button>`}</div>`)}</div>` : null}
      ${f.questions?.length ? html`<div><div class="section-title">Open questions</div><ul class="tight">${f.questions.slice(0, 6).map((q) => html`<li>${q}</li>`)}</ul></div>` : null}
    </div>` : null}
  </section>`;
}

function MockupPreview({ note }) {
  const [big, setBig] = useState(false);
  const src = note.lib.url;
  return html`<section class="mockup-preview">
    <div class="row" style="padding:6px 10px;border-bottom:1px solid var(--rule)"><${Icon} name="app-window" size="sm" /><strong style="font-size:12.5px">Live preview</strong>
      <span class="faint" style="font-size:12px">Sandboxed: scripts run, but it can't reach the internet or Daybook.</span><span class="grow"></span>
      <button class="btn btn-ghost btn-sm" onClick=${() => setBig(true)}><${Icon} name="maximize-2" />Full screen</button></div>
    <iframe src=${src} sandbox="allow-scripts allow-forms allow-modals allow-downloads" title=${`Preview of ${note.title}`} loading="lazy"></iframe>
    ${big ? html`<div class="mockup-full" role="dialog" aria-label="Mockup preview">
      <div class="row" style="padding:8px 12px;background:var(--desk);color:var(--desk-ink)"><strong>${note.title}</strong><span class="grow"></span>
        <button class="btn btn-sm" onClick=${() => setBig(false)}><${Icon} name="x" />Close</button></div>
      <iframe src=${src} sandbox="allow-scripts allow-forms allow-modals allow-downloads" title=${`Preview of ${note.title}`}></iframe></div>` : null}
  </section>`;
}
