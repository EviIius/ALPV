// Daybook — app bootstrap, shell and global wiring.
import { html, render, useEffect, useState } from '/vendor/preact.js';
import { connectStream, get, onUnauthorized, post } from './lib/api.js';
import { emitChange, getState, setState, toast, toastError, useStore } from './lib/store.js';
import { navigate, useRoute } from './lib/router.js';
import { BrandMark, DialogHost, Icon, MenuHost, Toasts, chooseDialog, modKey, openMenu, promptDialog, useMedia } from './lib/ui.js';
import { AuthScreen } from './views/auth.js';
import { TodayView } from './views/today.js';
import { CalendarView, EventEditorHost } from './views/calendar.js';
import { NotesView } from './views/notes.js';
import { TasksView, TaskEditorHost } from './views/tasks.js';
import { ImportView } from './views/import.js';
import { AskView } from './views/ask.js';
import { SettingsView } from './views/settings.js';
import { LibraryView } from './views/library.js';
import { ReaderView } from './views/reader.js';
import { GlossaryView } from './views/glossary.js';
import { HighlightsView } from './views/highlights.js';
import { DateDialogHost } from './lib/library.js';
import { CommandPalette } from './components/palette.js';
import { CaptureModal } from './components/capture.js';
import { PrepHost } from './components/prep.js';
import './lib/viewport.js';
import { UpdateNotice, checkServer } from './components/update.js';
import { TermPeek } from './components/termpeek.js';

// ---------------------------------------------------------------------------
// Theme
// ---------------------------------------------------------------------------
const darkQuery = window.matchMedia('(prefers-color-scheme: dark)');
export function applyTheme(pref) {
  const theme = pref === 'dark' || (pref !== 'light' && darkQuery.matches) ? 'dark' : 'light';
  document.documentElement.dataset.theme = theme;
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = theme === 'dark' ? '#0d1310' : '#1f2d26';
  try { localStorage.setItem('daybook_theme', pref); } catch { /* private mode */ }
}
darkQuery.addEventListener('change', () => applyTheme(getState().settings?.appearance?.theme || 'system'));
try { applyTheme(localStorage.getItem('daybook_theme') || 'system'); } catch { applyTheme('system'); }

// ---------------------------------------------------------------------------
// Shared data
// ---------------------------------------------------------------------------
export async function loadShared() {
  const [settings, calendars, notebooks, tags] = await Promise.all([
    get('/api/settings'), get('/api/calendars'), get('/api/notebooks'), get('/api/tags'),
  ]);
  setState({ settings, calendars, notebooks, tags });
  applyTheme(settings.appearance.theme);
  refreshAi();
  refreshLibrary();
}
let libTimer = null;
export function refreshLibrary(delay = 0) {
  clearTimeout(libTimer);
  libTimer = setTimeout(async () => {
    try { setState({ library: await get('/api/library/status') }); } catch { /* signed out or offline */ }
  }, delay);
}
export async function refreshAi() {
  try { setState({ ai: await get('/api/ai/status') }); } catch { setState({ ai: { ok: false, enabled: false, models: [] } }); }
}
let tagTimer = null;
function refreshLists(entity) {
  if (entity === 'notes' || entity === 'library') refreshLibrary(600);
  if (entity === 'notebooks' || entity === 'notes') {
    clearTimeout(tagTimer);
    tagTimer = setTimeout(async () => {
      try { setState({ notebooks: await get('/api/notebooks'), tags: await get('/api/tags') }); } catch { /* offline */ }
    }, 400);
  }
  if (entity === 'calendars') get('/api/calendars').then((calendars) => setState({ calendars })).catch(() => {});
  if (entity === 'settings') get('/api/settings').then((settings) => { setState({ settings }); applyTheme(settings.appearance.theme); }).catch(() => {});
}

async function checkAuth() {
  try { setState({ auth: await get('/api/auth/status') }); }
  catch (err) { setState({ auth: { error: err.message, authenticated: false, configured: true } }); }
}
onUnauthorized(() => setState((s) => ({ auth: { ...(s.auth || {}), authenticated: false } })));

// ---------------------------------------------------------------------------
// Navigation
// ---------------------------------------------------------------------------
const NAV = [
  { key: 'today', label: 'Today', icon: 'sun', href: '#/today' },
  { key: 'calendar', label: 'Calendar', icon: 'calendar-days', href: '#/calendar' },
  { key: 'notes', label: 'Notes', icon: 'notebook-pen', href: '#/notes' },
  { key: 'library', label: 'Library', icon: 'folder-open', href: '#/library' },
  { key: 'highlights', label: 'Highlights', icon: 'highlighter', href: '#/highlights' },
  { key: 'glossary', label: 'Glossary', icon: 'book-a', href: '#/glossary' },
  { key: 'tasks', label: 'Tasks', icon: 'list-checks', href: '#/tasks' },
  { key: 'import', label: 'Import', icon: 'file-up', href: '#/import' },
  { key: 'ask', label: 'Ask', icon: 'message-square-text', href: '#/ask' },
];
const TITLES = { today: 'Today', calendar: 'Calendar', notes: 'Notes', library: 'Library', tasks: 'Tasks', import: 'Import', ask: 'Ask your notes', settings: 'Settings', read: 'Reader', glossary: 'Glossary', highlights: 'Highlights' };

async function newLibFolder() {
  const name = await promptDialog({ title: 'New folder', label: 'Folder name', placeholder: 'e.g. Workshops', confirm: 'Create folder',
    hint: 'A real folder in your library. Notes you write in it are saved there as Markdown files.' });
  if (!name) return;
  try {
    const nb = await post('/api/library/folders', { name });
    setState({ notebooks: await get('/api/notebooks') });
    navigate(`#/notes?notebook=${nb.id}`);
  } catch (err) { toastError(err); }
}

async function newNotebook() {
  const name = await promptDialog({ title: 'New notebook', label: 'Name', placeholder: 'e.g. Data platform', confirm: 'Create notebook' });
  if (!name) return;
  try {
    const nb = await post('/api/notebooks', { name });
    setState({ notebooks: await get('/api/notebooks') });
    navigate(`#/notes?notebook=${nb.id}`);
  } catch (err) { toastError(err); }
}

function AiLine() {
  const ai = useStore((s) => s.ai);
  if (!ai) return null;
  if (!ai.enabled) return html`<a class="privacy-line" href="#/settings/ai"><${Icon} name="brain" /><span>Local AI is off</span></a>`;
  if (!ai.ok) return html`<a class="privacy-line" href="#/settings/ai" title=${ai.error || ''}><${Icon} name="cloud-off" /><span>Local AI not reachable</span></a>`;
  return html`<a class="privacy-line" href="#/settings/ai"><${Icon} name="cpu" cls="ok" /><span>AI on your hardware: ${ai.picked?.smart || 'no model yet'}</span></a>`;
}

function Sidebar({ route, onSearch, onNew }) {
  const notebooks = useStore((s) => s.notebooks);
  const tags = useStore((s) => s.tags);
  const counts = useStore((s) => s.counts);
  const live = useStore((s) => s.live);
  const library = useStore((s) => s.library);
  const settings = useStore((s) => s.settings);
  const libBooks = notebooks.filter((b) => b.lib_dir !== null && b.lib_dir !== undefined);
  const privBooks = notebooks.filter((b) => b.lib_dir === null || b.lib_dir === undefined);
  const nbParam = route.params.get('notebook');
  const tagParam = route.params.get('tag');
  return html`<nav class="sidebar" aria-label="Main">
    <div class="brand"><${BrandMark} /><span class="brand-name">Daybook</span></div>
    <div class="side-actions">
      <button class="side-search" onClick=${onSearch}><${Icon} name="search" size="sm" /><span>Search</span><span class="kbd">${modKey}K</span></button>
      <button class="side-new" onClick=${onNew} title="New (N)" aria-label="New"><${Icon} name="plus" /></button>
    </div>
    <div class="side-scroll">
      ${NAV.map((n) => html`<a class="nav-item" href=${n.href} aria-current=${route.name === n.key && !nbParam && !tagParam ? 'page' : undefined}>
        <${Icon} name=${n.icon} /><span>${n.label}</span>
        ${n.key === 'tasks' && counts.tasksDue ? html`<span class=${`badge ${counts.tasksOverdue ? 'badge-red' : ''}`}>${counts.tasksDue}</span>` : null}
        ${n.key === 'library' && library?.review ? html`<span class="badge badge-red" title="Files that need a date">${library.review}</span>` : null}
      </a>`)}
      ${libBooks.length ? html`<div class="nav-group">
        <div class="nav-group-head"><span title=${library?.root || ''}>${library?.name || 'Library'}</span><button onClick=${newLibFolder} title="New folder" aria-label="New folder in your library"><${Icon} name="folder-plus" size="sm" /></button></div>
        ${libBooks.map((b) => html`<a class="nav-item nav-sub" href=${`#/notes?notebook=${b.id}&sort=date`} aria-current=${nbParam === b.id ? 'page' : undefined}>
          <${Icon} name="folder" size="sm" /><span>${b.lib_dir ? b.name : 'Top level'}</span><span class="badge">${b.count || ''}</span></a>`)}
      </div>` : null}
      <div class="nav-group">
        <div class="nav-group-head"><span>${libBooks.length ? 'Private notebooks' : 'Notebooks'}</span><button onClick=${newNotebook} title="New notebook" aria-label="New notebook"><${Icon} name="plus" size="sm" /></button></div>
        ${privBooks.map((b) => html`<a class="nav-item nav-sub" href=${`#/notes?notebook=${b.id}`} aria-current=${nbParam === b.id ? 'page' : undefined}>
          <span class="swatch" style=${`background:${b.color || '#93a89b'}`}></span><span>${b.name}</span><span class="badge">${b.count || ''}</span></a>`)}
      </div>
      ${(settings?.search?.saved || []).length ? html`<div class="nav-group">
        <div class="nav-group-head"><span>Saved searches</span></div>
        ${settings.search.saved.map((sv) => html`<a class="nav-item nav-sub" href=${`#/notes?q=${encodeURIComponent(sv.q)}`} title=${sv.q}
          aria-current=${route.name === 'notes' && route.params.get('q') === sv.q ? 'page' : undefined}>
          <${Icon} name="filter" size="sm" /><span>${sv.name}</span></a>`)}
      </div>` : null}
      ${tags.length ? html`<div class="nav-group">
        <div class="nav-group-head"><span>Tags</span></div>
        ${tags.slice(0, 10).map((t) => html`<a class="nav-item nav-sub" href=${`#/notes?tag=${encodeURIComponent(t.tag)}`} aria-current=${tagParam === t.tag ? 'page' : undefined}>
          <${Icon} name="hash" size="sm" /><span>${t.tag}</span><span class="badge">${t.count}</span></a>`)}
      </div>` : null}
    </div>
    <div class="side-foot">
      <a class="privacy-line" href="#/settings/privacy"><${Icon} name="shield-check" cls="ok" /><span>Private: stored on this computer${live ? '' : ' (reconnecting…)'}</span></a>
      <${AiLine} />
      <a class="nav-item" href="#/settings" aria-current=${route.name === 'settings' ? 'page' : undefined}><${Icon} name="settings" /><span>Settings</span></a>
    </div>
  </nav>`;
}

function TopBar({ route, onSearch, onNew }) {
  return html`<header class="topbar">
    <${BrandMark} />
    <span class="topbar-title">${TITLES[route.name] || 'Daybook'}</span>
    <button class="tb-icon" onClick=${onSearch} aria-label="Search"><${Icon} name="search" /></button>
    <button class="tb-icon" onClick=${onNew} aria-label="New"><${Icon} name="plus" /></button>
  </header>`;
}

function TabBar({ route }) {
  const tabs = NAV.filter((n) => ['today', 'calendar', 'notes', 'tasks'].includes(n.key));
  const more = (e) => openMenu(e.currentTarget, [
    { label: 'Library', icon: 'folder-open', onSelect: () => navigate('#/library') },
    { label: 'Highlights', icon: 'highlighter', onSelect: () => navigate('#/highlights') },
    { label: 'Glossary', icon: 'book-a', onSelect: () => navigate('#/glossary') },
    { label: 'Import documents', icon: 'file-up', onSelect: () => navigate('#/import') },
    { label: 'Ask your notes', icon: 'message-square-text', onSelect: () => navigate('#/ask') },
    { sep: true },
    ...getState().notebooks.map((b) => ({ label: b.name, icon: 'notebook', onSelect: () => navigate(`#/notes?notebook=${b.id}`) })),
    { sep: true },
    { label: 'Settings', icon: 'settings', onSelect: () => navigate('#/settings') },
    { label: 'Privacy check-up', icon: 'shield-check', onSelect: () => navigate('#/settings/privacy') },
  ], { align: 'end' });
  const moreActive = ['library', 'highlights', 'glossary', 'read', 'import', 'ask', 'settings'].includes(route.name);
  return html`<nav class="tabbar" aria-label="Main">
    ${tabs.map((n) => html`<a href=${n.href} aria-current=${route.name === n.key ? 'page' : undefined}><${Icon} name=${n.icon} /><span>${n.label}</span></a>`)}
    <button onClick=${more} aria-current=${moreActive ? 'page' : undefined}><${Icon} name="ellipsis" /><span>More</span></button>
  </nav>`;
}

// ---------------------------------------------------------------------------
// Legacy data migration (from the previous Calendar-Notes-Discord app)
// ---------------------------------------------------------------------------
async function offerLegacyImport() {
  let notes, events;
  try {
    if (localStorage.getItem('daybook_legacy_handled')) return;
    notes = JSON.parse(localStorage.getItem('calnotes_notes') || 'null');
    events = JSON.parse(localStorage.getItem('calnotes_events') || 'null');
  } catch { return; }
  if (!notes && !events) return;
  const n = (notes || []).filter((x) => !/^note-[123]$/.test(x.id)).length;
  const e = (events || []).filter((x) => !/^evt-[123]$/.test(x.id)).length;
  if (!n && !e) { localStorage.setItem('daybook_legacy_handled', '1'); return; }
  const choice = await chooseDialog({
    title: 'Bring over your old notes?',
    message: `This browser still has ${n} note${n === 1 ? '' : 's'} and ${e} event${e === 1 ? '' : 's'} from the previous version of the app. Import them into Daybook? The sample notes it came with are skipped.`,
    choices: [{ label: 'Import them', value: 'import', primary: true }, { label: "Don't ask again", value: 'never' }],
  });
  if (choice === 'import') {
    try {
      const res = await post('/api/restore/legacy', { notes: notes || [], events: events || [] });
      toast(`Imported ${res.notes} notes and ${res.events} events.`);
      localStorage.setItem('daybook_legacy_handled', '1');
      emitChange({ entity: 'notes' });
      emitChange({ entity: 'events' });
    } catch (err) { toastError(err); }
  } else if (choice === 'never') {
    localStorage.setItem('daybook_legacy_handled', '1');
  }
}

// ---------------------------------------------------------------------------
// Shell
// ---------------------------------------------------------------------------
function notifyBrowser(evt) {
  if (!('Notification' in window) || Notification.permission !== 'granted' || !document.hidden) return;
  try {
    const n = new Notification(evt.title, { body: evt.body, tag: evt.ref || evt.title, icon: '/icons/icon-192.png' });
    n.onclick = () => { window.focus(); if (evt.ref) navigate(evt.ref); n.close(); };
  } catch { /* some browsers require a service worker */ }
}

async function refreshCounts() {
  try {
    const res = await get('/api/tasks?view=today');
    setState({ counts: { tasksDue: res.counts.today + res.counts.overdue, tasksOverdue: res.counts.overdue } });
  } catch { /* ignore */ }
}

function Shell() {
  const route = useRoute();
  const settings = useStore((s) => s.settings);
  const [palette, setPalette] = useState(false);
  const [capture, setCapture] = useState(false);
  const narrow = useMedia('(max-width: 899px)');

  useEffect(() => {
    loadShared().catch(toastError);
    refreshCounts();
    const stop = connectStream((evt) => {
      if (evt.type === 'changed') {
        emitChange(evt);
        refreshLists(evt.entity);
        if (evt.entity === 'tasks') refreshCounts();
      } else if (evt.type === 'notify') {
        toast(`${evt.title}: ${evt.body}`, { timeout: 9000, action: evt.ref ? { label: 'Open', run: () => navigate(evt.ref) } : null });
        notifyBrowser(evt);
      } else if (evt.type === 'library') {
        setState((st) => ({ library: { ...(st.library || {}), ...evt.state } }));
        if (!evt.state.scanning) refreshLibrary(300);
      } else if (evt.type === 'import') {
        emitChange({ entity: 'imports', job: evt.job });
        if (evt.job.status === 'done' && evt.job.note_id) emitChange({ entity: 'notes', id: evt.job.note_id });
      }
    }, (live) => { setState({ live }); if (live) checkServer(); });
    const timer = setInterval(refreshCounts, 5 * 60 * 1000);
    setTimeout(offerLegacyImport, 800);
    return () => { stop(); clearInterval(timer); };
  }, []);

  useEffect(() => {
    const onKey = (e) => {
      const typing = /INPUT|TEXTAREA|SELECT/.test(e.target.tagName) || e.target.isContentEditable;
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); setPalette(true); return; }
      if (typing || e.metaKey || e.ctrlKey || e.altKey || getState().dialog || getState().menu) return;
      if (e.key === '/') { e.preventDefault(); setPalette(true); }
      else if (e.key === 'n') { e.preventDefault(); setCapture(true); }
      else if (e.key === 'g') {
        const handler = (ev) => {
          const map = { t: '#/today', c: '#/calendar', n: '#/notes', l: '#/library', k: '#/tasks', i: '#/import', a: '#/ask', s: '#/settings' };
          if (map[ev.key]) navigate(map[ev.key]);
          window.removeEventListener('keydown', handler, true);
        };
        window.addEventListener('keydown', handler, true);
        setTimeout(() => window.removeEventListener('keydown', handler, true), 1200);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  useEffect(() => { document.title = `${TITLES[route.name] || 'Daybook'} — Daybook`; }, [route.name]);

  if (!settings) return html`<div class="auth paper-grid"><span class="spinner"></span></div>`;
  let view;
  switch (route.name) {
    case 'calendar': view = html`<${CalendarView} route=${route} />`; break;
    case 'notes': view = html`<${NotesView} route=${route} />`; break;
    case 'library': view = html`<${LibraryView} route=${route} />`; break;
    case 'read': view = html`<${ReaderView} route=${route} />`; break;
    case 'glossary': view = html`<${GlossaryView} route=${route} />`; break;
    case 'highlights': view = html`<${HighlightsView} route=${route} />`; break;
    case 'tasks': view = html`<${TasksView} route=${route} />`; break;
    case 'import': view = html`<${ImportView} />`; break;
    case 'ask': view = html`<${AskView} />`; break;
    case 'settings': view = html`<${SettingsView} route=${route} />`; break;
    default: view = html`<${TodayView} />`;
  }
  const fullBleed = route.name === 'notes' || route.name === 'read';
  return html`<div class="app">
    <${Sidebar} route=${route} onSearch=${() => setPalette(true)} onNew=${() => setCapture(true)} />
    ${narrow ? html`<${TopBar} route=${route} onSearch=${() => setPalette(true)} onNew=${() => setCapture(true)} />` : null}
    <main class=${`main ${fullBleed ? '' : 'paper-grid'}`} id="main">${view}</main>
    ${narrow ? html`<${TabBar} route=${route} />` : null}
    ${palette ? html`<${CommandPalette} onClose=${() => setPalette(false)} onCapture=${() => { setPalette(false); setCapture(true); }} />` : null}
    ${capture ? html`<${CaptureModal} onClose=${() => setCapture(false)} />` : null}
    <${EventEditorHost} />
    <${TaskEditorHost} />
    <${PrepHost} />
    <${TermPeek} />
    <${UpdateNotice} />
  </div>`;
}

function App() {
  const auth = useStore((s) => s.auth);
  useEffect(() => { checkAuth(); }, []);
  let body;
  if (!auth) body = html`<div class="auth paper-grid"><span class="spinner"></span></div>`;
  else if (!auth.authenticated) body = html`<${AuthScreen} auth=${auth} onDone=${checkAuth} />`;
  else body = html`<${Shell} />`;
  return html`${body}<${MenuHost} /><${DialogHost} /><${DateDialogHost} /><${Toasts} />`;
}

render(html`<${App} />`, document.getElementById('root'));

// iPhone/iPad: Daybook scrolls inside its panes and never scrolls the page itself,
// but the on-screen keyboard can leave the page pushed up or sideways after it
// closes. Put it back once nothing is being typed into and the page isn't zoomed.
(() => {
  const vv = window.visualViewport;
  const touch = window.matchMedia('(hover: none) and (pointer: coarse)').matches;
  if (!touch) return;
  const typing = () => {
    const a = document.activeElement;
    return a && (/INPUT|TEXTAREA|SELECT/.test(a.tagName) || a.isContentEditable);
  };
  let timer = null;
  const settle = () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      if (typing()) return;
      if (vv && vv.scale > 1.01) return; // the user is pinch-zooming on purpose
      if (window.scrollX || window.scrollY) window.scrollTo(0, 0);
    }, 120);
  };
  document.addEventListener('focusout', settle);
  vv?.addEventListener('resize', settle);
  window.addEventListener('orientationchange', settle);
  window.addEventListener('scroll', settle, { passive: true });
})();

if ('serviceWorker' in navigator && (location.protocol === 'https:' || location.hostname === 'localhost' || location.hostname === '127.0.0.1')) {
  // When newer pages arrive while the app is open, offer a reload instead of waiting for the next launch.
  const hadWorker = !!navigator.serviceWorker.controller;
  navigator.serviceWorker.addEventListener('controllerchange', () => { if (hadWorker) setState({ pagesUpdated: true }); });
  navigator.serviceWorker.register('/sw.js').catch(() => {});
}
