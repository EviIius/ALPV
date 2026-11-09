// Command palette (⌘K / Ctrl+K / "/"): search everything, run commands.
import { html, useEffect, useMemo, useRef, useState } from '/vendor/preact.js';
import { get, post } from '../lib/api.js';
import { getState, toastError } from '../lib/store.js';
import { navigate } from '../lib/router.js';
import { fmtDate, fmtRange, today } from '../lib/dates.js';
import { Icon, useDebounced } from '../lib/ui.js';
import { openEventEditor } from '../views/calendar.js';
import { openTaskEditor } from '../views/tasks.js';

function commands(onCapture) {
  return [
    { label: 'Add something…', icon: 'plus', hint: 'N', run: onCapture },
    { label: 'New note', icon: 'file-plus', run: async () => { const n = await post('/api/notes', {}); navigate(`#/notes/${n.id}`); } },
    { label: 'New event', icon: 'calendar-plus', run: () => openEventEditor({}) },
    { label: 'New task', icon: 'square-check', run: () => openTaskEditor({}) },
    { label: "Open today's daily note", icon: 'notebook-pen', run: async () => { const n = await get(`/api/daily/${today()}`); navigate(`#/notes/${n.id}`); } },
    { label: 'Import documents', icon: 'file-up', run: () => navigate('#/import') },
    { label: 'Ask your notes', icon: 'message-square-text', run: () => navigate('#/ask') },
    { label: 'Go to Today', icon: 'sun', run: () => navigate('#/today') },
    { label: 'Go to Calendar', icon: 'calendar-days', run: () => navigate('#/calendar') },
    { label: 'Go to Notes', icon: 'notebook-pen', run: () => navigate('#/notes') },
    { label: 'Go to Tasks', icon: 'list-checks', run: () => navigate('#/tasks') },
    { label: 'Go to Library', icon: 'folder-open', hint: 'G L', run: () => navigate('#/library') },
    { label: 'Files that need a date', icon: 'calendar-search', run: () => navigate('#/library/review') },
    { label: 'Decisions log', icon: 'gavel', run: () => navigate('#/library/decisions') },
    { label: 'My follow-ups', icon: 'list-todo', run: () => navigate('#/library/followups') },
    { label: 'Privacy check-up', icon: 'shield-check', run: () => navigate('#/settings/privacy') },
    { label: 'Local AI settings', icon: 'cpu', run: () => navigate('#/settings/ai') },
    { label: 'Settings', icon: 'settings', run: () => navigate('#/settings') },
    { label: 'Switch light / dark', icon: 'sun-moon', run: async () => {
      const cur = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
      const { put } = await import('../lib/api.js');
      const { applyTheme } = await import('../main.js');
      applyTheme(cur);
      await put('/api/settings', { appearance: { ...getState().settings.appearance, theme: cur } });
    } },
    { label: 'Sign out of this device', icon: 'log-out', run: async () => { await post('/api/auth/logout'); location.reload(); } },
  ];
}

export function CommandPalette({ onClose, onCapture }) {
  const [q, setQ] = useState('');
  const [results, setResults] = useState({ notes: [], events: [], tasks: [], pages: [], highlights: [] });
  const [sel, setSel] = useState(0);
  const inputRef = useRef(null);
  const listRef = useRef(null);
  const cmds = useMemo(() => commands(onCapture), []);

  const search = useDebounced(async (value) => {
    if (!value.trim()) { setResults({ notes: [], events: [], tasks: [], pages: [], highlights: [] }); return; }
    try { setResults(await get(`/api/search?q=${encodeURIComponent(value)}`)); } catch { /* ignore */ }
  }, 120);

  useEffect(() => { inputRef.current && inputRef.current.focus(); }, []);

  const query = q.trim().toLowerCase();
  const matchedCmds = (query ? cmds.filter((c) => c.label.toLowerCase().includes(query)) : cmds.slice(0, 7));
  const items = [
    ...results.notes.map((n) => ({ group: 'Notes', key: `n${n.id}`, icon: n.locked ? 'lock' : 'file-text', label: n.title || 'Untitled', subHtml: n.snippet_html, sub: n.snippet_html ? null : n.snippet, meta: fmtDate(new Date(n.updated_at)), run: () => navigate(`#/notes/${n.id}`) })),
    ...(results.pages || []).map((p) => ({ group: 'Inside decks and PDFs', key: `p${p.note_id}-${p.page}`, icon: p.kind === 'deck' ? 'presentation' : p.kind === 'sheet' ? 'file-spreadsheet' : 'file',
      label: p.label || `Page ${p.page}`, subHtml: p.snippet_html, meta: p.title, run: () => navigate(`#/read/${p.note_id}?p=${p.page}&q=${encodeURIComponent(q.trim())}`) })),
    ...(results.highlights || []).map((a) => ({ group: 'Highlights', key: `h${a.id}`, icon: 'highlighter', label: a.quote || a.comment,
      sub: a.comment && a.quote ? a.comment : '', meta: a.note_title, run: () => navigate(`#/read/${a.note_id}${a.page ? `?p=${a.page}` : ''}`) })),
    ...results.events.map((e) => ({ group: 'Events', key: `e${e.id}`, icon: 'calendar', label: e.title, sub: fmtRange(e), meta: fmtDate(e.start.slice(0, 10)), run: () => navigate(`#/calendar/${e.start.slice(0, 10)}`) })),
    ...results.tasks.map((t) => ({ group: 'Tasks', key: `t${t.id}`, icon: t.done ? 'circle-check' : 'square', label: t.title, meta: t.due_date ? fmtDate(t.due_date) : '', run: () => openTaskEditor(t) })),
    ...matchedCmds.map((c, i) => ({ group: 'Commands', key: `c${i}`, icon: c.icon, label: c.label, meta: c.hint || '', run: c.run })),
  ];
  const run = async (it) => {
    onClose();
    try { await it.run(); } catch (err) { toastError(err); }
  };
  const onKey = (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setSel((s) => Math.min(items.length - 1, s + 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setSel((s) => Math.max(0, s - 1)); }
    else if (e.key === 'Enter') { e.preventDefault(); if (items[sel]) run(items[sel]); }
    else if (e.key === 'Escape') { e.preventDefault(); onClose(); }
  };
  useEffect(() => {
    const el = listRef.current && listRef.current.querySelector('[aria-selected="true"]');
    if (el) el.scrollIntoView({ block: 'nearest' });
  }, [sel]);

  let lastGroup = null;
  return html`<div class="backdrop" onMouseDown=${(e) => { if (e.target === e.currentTarget) onClose(); }}>
    <div class="modal palette" role="dialog" aria-modal="true" aria-label="Search">
      <div class="palette-input"><${Icon} name="search" />
        <input ref=${inputRef} autofocus value=${q} placeholder="Search notes, events, tasks, or type a command" aria-label="Search"
          onInput=${(e) => { setQ(e.currentTarget.value); setSel(0); search(e.currentTarget.value); }} onKeyDown=${onKey} />
        <span class="kbd">Esc</span>
      </div>
      <div class="palette-list" ref=${listRef} role="listbox">
        ${items.length === 0 ? html`<div class="palette-group">No matches. Try different words.</div>` : null}
        ${items.map((it, i) => {
          const head = it.group !== lastGroup ? html`<div class="palette-group">${it.group}</div>` : null;
          lastGroup = it.group;
          return html`${head}<button class="palette-item" role="option" aria-selected=${String(i === sel)} onMouseEnter=${() => setSel(i)} onClick=${() => run(it)}>
            <${Icon} name=${it.icon} />
            <span class="grow"><span>${it.label}</span>
              ${it.subHtml ? html`<span class="sub" dangerouslySetInnerHTML=${{ __html: it.subHtml }}></span>` : it.sub ? html`<span class="sub">${it.sub}</span>` : null}
            </span>
            ${it.meta ? html`<span class="meta">${it.meta}</span>` : null}
          </button>`;
        })}
      </div>
    </div>
  </div>`;
}
