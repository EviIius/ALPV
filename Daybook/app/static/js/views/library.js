// Library: your ALPV folder, read in place — timeline, dates to confirm, decisions,
// follow-ups, people, mockups and status.
import { html, useEffect, useMemo, useState } from '/vendor/preact.js';
import { get, post, qs } from '../lib/api.js';
import { emitChange, toast, toastError, useChanges, useStore } from '../lib/store.js';
import { navigate } from '../lib/router.js';
import { MON_SHORT, MONTHS, addDays, fmtAgo, fmtDate, fmtTime, parseYmd, today, ymd } from '../lib/dates.js';
import { Empty, Icon, Markdown, Modal, Seg, Spinner, useDebounced, useMedia } from '../lib/ui.js';
import { KIND, SOURCE, acceptSuggestion, dateText, kindOf, openDateDialog, setNoteDate, weekKey, weekdayShort } from '../lib/library.js';

const TABS = [
  { key: 'timeline', label: 'Timeline', icon: 'calendar-range' },
  { key: 'review', label: 'Needs a date', icon: 'calendar-search' },
  { key: 'decisions', label: 'Decisions', icon: 'gavel' },
  { key: 'followups', label: 'Follow-ups', icon: 'list-todo' },
  { key: 'people', label: 'People', icon: 'users' },
  { key: 'mockups', label: 'Mockups', icon: 'app-window' },
  { key: 'status', label: 'Folder & git', icon: 'folder-git-2' },
];

export function LibraryView({ route }) {
  const tab = route.parts[1] || 'timeline';
  const status = useStore((s) => s.library);
  const narrow = useMedia('(max-width: 760px)');
  if (status && !status.configured) return html`<${NotConnected} status=${status} />`;
  const T = { timeline: Timeline, review: Review, decisions: Decisions, followups: Followups, people: People, mockups: Mockups, status: Status }[tab] || Timeline;
  return html`<div class="view view-wide library">
    <div class="view-head lib-head">
      <div>
        <h1 class="view-title"><${Icon} name="folder-open" />${status?.name || 'Library'}</h1>
        <p class="view-sub">${status ? html`<span class="mono">${status.root}</span> · ${status.files || 0} files · ${status.dated || 0} on the calendar
          ${status.scanning ? html` · <span class="status status-busy">Reading files${status.progress ? ` ${status.progress.done}/${status.progress.total}` : ''}</span>` : ''}` : html`<${Spinner} />`}</p>
      </div>
      <div class="row">
        <button class="btn" onClick=${rescan}><${Icon} name="refresh-cw" />Rescan</button>
        ${['localhost', '127.0.0.1'].includes(location.hostname) ? html`<button class="btn" onClick=${() => post('/api/library/reveal-folder').catch(toastError)}><${Icon} name="folder-open" />Open folder</button>` : null}
      </div>
    </div>
    <nav class="lib-tabs" aria-label="Library sections">
      ${TABS.map((t) => html`<a href=${`#/library/${t.key}`} aria-current=${t.key === tab ? 'page' : undefined}>
        <${Icon} name=${t.icon} />${narrow && t.key !== tab ? '' : t.label}
        ${t.key === 'review' && status?.review ? html`<span class="badge badge-red">${status.review}</span>` : null}</a>`)}
    </nav>
    <${T} route=${route} status=${status} />
  </div>`;
}

async function rescan() {
  toast('Reading your folder again…');
  try { const r = await post('/api/library/rescan', { wait: true }); toast(`Done. ${r.status?.files ?? ''} files, ${r.status?.review ?? 0} need a date.`); emitChange({ entity: 'notes' }); }
  catch (err) { toastError(err); }
}

function NotConnected({ status }) {
  return html`<div class="view"><div class="panel" style="max-width:640px">
    <div class="panel-head"><h2>Connect a folder</h2></div>
    <p class="muted" style="line-height:1.6">Daybook can read a folder of your own files (Word documents, decks, PDFs, transcripts, mockups, Markdown) in place,
      put each one on the right day of the calendar, and keep Markdown notes in sync both ways. Nothing is copied or uploaded.</p>
    ${status?.enabled === false ? html`<p class="muted">The library is turned off.</p>` : null}
    <a class="btn btn-primary" href="#/settings/library" style="margin-top:12px"><${Icon} name="folder-plus" />Choose a folder</a>
  </div></div>`;
}

function Row({ n, showDay = false }) {
  const k = kindOf(n);
  return html`<a class="tl-row" href=${`#/notes/${n.id}`}>
    <span class="tl-time">${showDay ? `${weekdayShort(n.linked_date)} ${parseYmd(n.linked_date).getDate()}` : n.date_time ? fmtTime(n.date_time) : ''}</span>
    <span class="tl-kind" style=${n.color ? `--c:${n.color}` : ''} title=${k.label}><${Icon} name=${k.icon} /></span>
    <span class="tl-main"><span class="tl-title">${n.title || 'Untitled'}${n.date_end && n.date_end !== n.linked_date ? html` <span class="faint">(${dateText(n, { time: false })})</span>` : null}</span>
      <span class="tl-sub">${n.notebook ? html`<span class="row" style="gap:4px"><span class="swatch" style=${`background:${n.color || 'var(--faint)'}`}></span>${n.notebook}</span>` : null}
        ${n.people?.length ? html`<span>${n.people.slice(0, 4).join(', ')}${n.people.length > 4 ? ` +${n.people.length - 4}` : ''}</span>` : null}
        ${n.decisions ? html`<span><${Icon} name="gavel" size="sm" />${n.decisions}</span>` : null}
        ${n.actions ? html`<span><${Icon} name="list-todo" size="sm" />${n.actions}</span>` : null}
        ${n.date_status === 'review' ? html`<span class="red-text">check date</span>` : null}</span></span>
  </a>`;
}

// ---------------------------------------------------------------------------
// Timeline
// ---------------------------------------------------------------------------
function Timeline({ route }) {
  const notebooks = useStore((s) => s.notebooks);
  const ai = useStore((s) => s.ai);
  const p = route.params;
  const folder = p.get('folder') || '';
  const kind = p.get('kind') || '';
  const person = p.get('person') || '';
  const [months, setMonths] = useState(3);
  const [data, setData] = useState(null);
  const [review, setReview] = useState(null);
  const end = ymd(addDays(new Date(), 90));
  const start = ymd(addDays(new Date(), -31 * months));
  const load = async () => {
    try { setData(await get(`/api/library/timeline${qs({ start, end, folder, kind, person })}`)); } catch (err) { toastError(err); }
  };
  useEffect(() => { load(); }, [folder, kind, person, months]);
  useChanges(['notes', 'library'], () => load(), [folder, kind, person, months]);
  const setParam = (k, v) => { const q = new URLSearchParams(p); if (v) q.set(k, v); else q.delete(k); navigate(`#/library/timeline${q.toString() ? `?${q}` : ''}`, { replace: true }); };
  const t = today();
  const groups = useMemo(() => {
    if (!data) return [];
    const upcoming = data.items.filter((n) => n.linked_date > t);
    const past = data.items.filter((n) => n.linked_date <= t).reverse();
    const weeks = [];
    for (const n of past) {
      const wk = weekKey(n.linked_date);
      let g = weeks[weeks.length - 1];
      if (!g || g.key !== wk) { g = { key: wk, days: [] }; weeks.push(g); }
      let dg = g.days[g.days.length - 1];
      if (!dg || dg.day !== n.linked_date) { dg = { day: n.linked_date, items: [] }; g.days.push(dg); }
      dg.items.push(n);
    }
    for (const w of weeks) for (const d of w.days) d.items.sort((a, b) => (a.date_time || '99').localeCompare(b.date_time || '99'));
    return { upcoming, weeks };
  }, [data]);
  const libBooks = notebooks.filter((b) => b.lib_dir !== null && b.lib_dir !== undefined);
  return html`<div>
    <div class="row row-wrap lib-filters">
      <select class="select select-sm" value=${folder} onChange=${(e) => setParam('folder', e.currentTarget.value)} aria-label="Folder">
        <option value="">All folders</option>${libBooks.map((b) => html`<option value=${b.id}>${b.name}</option>`)}</select>
      <select class="select select-sm" value=${kind} onChange=${(e) => setParam('kind', e.currentTarget.value)} aria-label="Kind">
        <option value="">Everything</option>${Object.entries(KIND).map(([k, v]) => html`<option value=${k}>${v.label}${k === 'markdown' ? 's' : ''}</option>`)}</select>
      ${person ? html`<span class="chip chip-on"><${Icon} name="user-round" size="sm" />${person}<button onClick=${() => setParam('person', '')} aria-label="Clear person"><${Icon} name="x" size="sm" /></button></span>` : null}
      <span class="grow"></span>
      ${data ? html`<span class="faint" style="font-size:12px">${data.items.length} item${data.items.length === 1 ? '' : 's'}</span>` : null}
    </div>
    ${!data ? html`<${Spinner} label="Loading" />` : !data.items.length ? html`<${Empty} title="Nothing dated in this range." icon="calendar-range">
        <span>Files show up here once they have a date. <a href="#/library/review">See what needs a date</a>.</span></${Empty}>` : html`
      ${groups.upcoming.length ? html`<section class="tl-week">
        <div class="tl-week-head"><h3>Coming up</h3></div>
        ${groups.upcoming.map((n) => html`<${Row} n=${n} showDay=${true} />`)}
      </section>` : null}
      ${groups.weeks.map((w) => {
        const d0 = parseYmd(w.key);
        const count = w.days.reduce((a, d) => a + d.items.length, 0);
        return html`<section class="tl-week">
          <div class="tl-week-head"><h3>Week of ${MON_SHORT[d0.getMonth()]} ${d0.getDate()}</h3><span class="faint">${count} item${count === 1 ? '' : 's'}</span>
            <span class="grow"></span>
            ${ai?.ok ? html`<button class="btn btn-ghost btn-sm" onClick=${() => setReview(w.key)}><${Icon} name="sparkles" />Week in review</button>` : null}</div>
          ${w.days.map((d) => html`<div class="tl-day">
            <a class="tl-date" href=${`#/calendar/${d.day}?view=day`}><span class="n">${parseYmd(d.day).getDate()}</span><span class="w">${weekdayShort(d.day)}</span></a>
            <div>${d.items.map((n) => html`<${Row} n=${n} />`)}</div>
          </div>`)}
        </section>`;
      })}
      <div class="row" style="justify-content:center;margin:18px 0"><button class="btn" onClick=${() => setMonths(months + 3)}>Show earlier</button></div>`}
    ${review ? html`<${WeekReview} start=${review} onClose=${() => setReview(null)} />` : null}
  </div>`;
}

function WeekReview({ start, onClose }) {
  const [res, setRes] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => { post('/api/library/week-review', { start }).then(setRes).catch((e) => setErr(e.message)); }, [start]);
  const save = async () => {
    try {
      const n = await post('/api/notes', { title: `Week in review — ${res.label.replace('the week of ', '')}`, body: res.markdown });
      await setNoteDate(n.id, { date: res.end }, false);
      onClose();
      navigate(`#/notes/${n.id}`);
    } catch (e) { toastError(e); }
  };
  return html`<${Modal} title=${res ? `Week in review` : 'Reading the week…'} wide=${true} onClose=${onClose} footer=${html`
      <button class="btn" onClick=${onClose}>Close</button>
      <button class="btn btn-primary" disabled=${!res} onClick=${save}>Save as a note</button>`}>
    ${err ? html`<div class="banner banner-error"><${Icon} name="circle-alert" /><div>${err}</div></div>`
      : !res ? html`<${Spinner} label="Your local model is reading this week's notes" />`
        : html`<p class="faint" style="margin-bottom:8px">${res.label}</p><${Markdown} text=${res.markdown} cls="prose" />
          <p class="ai-stamp" style="margin-top:10px">${res.model}, ${(res.ms / 1000).toFixed(1)}s, on your hardware</p>`}
  </${Modal}>`;
}

// ---------------------------------------------------------------------------
// Needs a date
// ---------------------------------------------------------------------------
function Review({ status }) {
  const ai = useStore((s) => s.ai);
  const [items, setItems] = useState(null);
  const [busy, setBusy] = useState(null);
  const load = async () => { try { setItems(await get('/api/library/review')); } catch (err) { toastError(err); } };
  useEffect(() => { load(); }, []);
  useChanges(['notes', 'library'], () => load(), []);
  const askAi = async (ids) => {
    setBusy(ids ? ids[0] : 'all');
    try {
      const res = await post('/api/library/dates/ai', ids ? { ids, apply: false } : { limit: 12, apply: false });
      const errors = res.filter((r) => r.error);
      if (errors.length) toastError(new Error(errors[0].error));
      else toast(`Local AI looked at ${res.length} file${res.length === 1 ? '' : 's'}.`);
      load();
    } catch (err) { toastError(err); }
    setBusy(null);
  };
  if (!items) return html`<${Spinner} label="Loading" />`;
  if (!items.length) return html`<${Empty} title="Everything has a date." icon="badge-check"><span>New files are dated as they arrive. Anything Daybook isn't sure about shows up here.</span></${Empty}>`;
  return html`<div class="stack">
    <div class="banner"><${Icon} name="info" /><div class="grow">Daybook couldn't be sure which day these belong to. Pick one, accept the suggestion, or let your local AI read them.
      Choices are saved in <span class="mono">.daybook/dates.json</span> in your folder, so Claude and other tools see them too.</div>
      ${ai?.ok ? html`<button class="btn btn-sm" onClick=${() => askAi(null)} disabled=${!!busy}>${busy === 'all' ? html`<span class="spinner"></span>` : html`<${Icon} name="sparkles" />`}Ask local AI about all</button>` : null}</div>
    ${items.map((it) => {
      const k = KIND[it.lib_kind] || { icon: 'file-up', label: 'Imported' };
      const sug = it.suggestion;
      const aiPick = it.ai;
      return html`<div class="review-card">
        <div class="row" style="align-items:flex-start">
          <span class="tl-kind"><${Icon} name=${k.icon} /></span>
          <div class="grow" style="min-width:0">
            <a class="review-title" href=${`#/notes/${it.id}`}>${it.title || 'Untitled'}</a>
            <div class="mono faint" style="font-size:11.5px;word-break:break-all">${it.lib_path || ''}</div>
            ${it.snippet ? html`<p class="review-snippet">${it.snippet}</p>` : null}
          </div>
        </div>
        <div class="review-picks">
          ${it.status === 'review' && it.linked_date ? html`<span class="pick"><strong>${fmtDate(it.linked_date, { weekday: true, year: true })}</strong>
              <span class="faint">${SOURCE[it.source] || ''}, but other clues disagree</span>
              <button class="btn btn-sm btn-primary" onClick=${() => setNoteDate(it.id, { date: it.linked_date })}>Keep</button></span>` : null}
          ${sug?.date ? html`<span class="pick"><strong>${fmtDate(sug.date, { weekday: true, year: true })}</strong>
              <span class="faint">${SOURCE[sug.source] || sug.source} · ${Math.round((sug.confidence || 0) * 100)}%</span>
              <button class="btn btn-sm btn-primary" onClick=${() => acceptSuggestion(it.id)}>Use this</button></span>` : null}
          ${aiPick ? html`<span class="pick pick-ai"><${Icon} name="sparkles" size="sm" /><strong>${aiPick.date ? fmtDate(aiPick.date, { weekday: true, year: true }) : 'No date'}</strong>
              <span class="faint">${aiPick.reason || ''}${aiPick.verified === false && aiPick.date ? ' (not found in the text)' : ''}</span>
              ${aiPick.date ? html`<button class="btn btn-sm" onClick=${() => acceptSuggestion(it.id, 'ai')}>Use AI's pick</button>` : null}</span>` : null}
          <span class="row" style="gap:6px;margin-left:auto">
            <button class="btn btn-sm" onClick=${() => openDateDialog({ ...it, date_info: { candidates: it.candidates, suggestion: it.suggestion, ai: it.ai } })}><${Icon} name="calendar" />Pick…</button>
            ${ai?.ok ? html`<button class="btn btn-sm btn-ghost" disabled=${!!busy} onClick=${() => askAi([it.id])}>${busy === it.id ? html`<span class="spinner"></span>` : html`<${Icon} name="sparkles" />`}Ask AI</button>` : null}
            <button class="btn btn-sm btn-ghost" onClick=${() => setNoteDate(it.id, { none: true })}><${Icon} name="calendar-x" />No date</button>
          </span>
        </div>
      </div>`;
    })}
  </div>`;
}

// ---------------------------------------------------------------------------
// Decisions
// ---------------------------------------------------------------------------
function Decisions() {
  const [q, setQ] = useState('');
  const [rows, setRows] = useState(null);
  const load = async (query = q) => { try { setRows(await get(`/api/library/decisions${qs({ q: query.trim() || undefined })}`)); } catch (err) { toastError(err); } };
  const search = useDebounced((v) => load(v), 200);
  useEffect(() => { load(); }, []);
  useChanges(['notes'], () => load(), [q]);
  const grouped = useMemo(() => {
    const out = [];
    for (const r of rows || []) {
      let g = out[out.length - 1];
      if (!g || g.note_id !== r.note_id) { g = { note_id: r.note_id, title: r.title, date: r.date, notebook: r.notebook, items: [] }; out.push(g); }
      g.items.push(r.text);
    }
    return out;
  }, [rows]);
  return html`<div class="stack">
    <div class="capture" style="max-width:520px"><${Icon} name="search" cls="faint" />
      <input value=${q} placeholder="Search decisions (e.g. owner list, cutover, storage)" aria-label="Search decisions" onInput=${(e) => { setQ(e.currentTarget.value); search(e.currentTarget.value); }} /></div>
    ${!rows ? html`<${Spinner} />` : !grouped.length ? html`<${Empty} title=${q ? 'No decisions match.' : 'No decisions found yet.'} icon="gavel">
        <span>Daybook reads "Decisions" sections in meeting notes. Write one in any note and it shows up here.</span></${Empty}>`
      : grouped.map((g) => html`<div class="decision-group">
          <a class="decision-head" href=${`#/notes/${g.note_id}`}><span class="mono">${g.date ? fmtDate(g.date, { year: 'auto' }) : 'Undated'}</span><strong>${g.title}</strong>${g.notebook ? html`<span class="faint">${g.notebook}</span>` : null}</a>
          <ul>${g.items.map((t) => html`<li>${t}</li>`)}</ul></div>`)}
  </div>`;
}

// ---------------------------------------------------------------------------
// Follow-ups (action items in notes)
// ---------------------------------------------------------------------------
function Followups() {
  const settings = useStore((s) => s.settings);
  const me = (settings?.profile?.name || '').trim();
  const [owner, setOwner] = useState(() => { try { return localStorage.getItem('daybook_fu_owner') ?? (me || ''); } catch { return me || ''; } });
  const [show, setShow] = useState('open');
  const [rows, setRows] = useState(null);
  const [people, setPeople] = useState([]);
  const load = async () => { try { setRows(await get(`/api/library/followups${qs({ owner: owner || undefined, show })}`)); } catch (err) { toastError(err); } };
  useEffect(() => { load(); try { localStorage.setItem('daybook_fu_owner', owner); } catch { /* ignore */ } }, [owner, show]);
  useEffect(() => { get('/api/library/people').then((p) => setPeople(p.filter((x) => x.open_actions).slice(0, 30))).catch(() => {}); }, []);
  useChanges(['notes', 'tasks'], () => load(), [owner, show]);
  const track = async (r) => {
    try { await post('/api/library/followups/track', { note_id: r.note_id, index: r.index }); toast('Added to Tasks (Follow-ups list)'); emitChange({ entity: 'tasks' }); load(); }
    catch (err) { toastError(err); }
  };
  return html`<div class="stack">
    <div class="row row-wrap lib-filters">
      <select class="select select-sm" value=${owner} onChange=${(e) => setOwner(e.currentTarget.value)} aria-label="Owner">
        <option value="">Everyone</option>
        ${me ? html`<option value=${me}>Mine (${me})</option>` : null}
        ${people.filter((p) => p.name !== me).map((p) => html`<option value=${p.name}>${p.name} (${p.open_actions})</option>`)}</select>
      <${Seg} value=${show} onChange=${setShow} options=${[{ value: 'open', label: 'Open' }, { value: 'all', label: 'All' }]} label="Show" />
      ${!me ? html`<a class="faint" style="font-size:12px" href="#/settings/appearance">Add your name in Settings to filter "Mine"</a>` : null}
      <span class="grow"></span>${rows ? html`<span class="faint" style="font-size:12px">${rows.length} item${rows.length === 1 ? '' : 's'}</span>` : null}
    </div>
    ${!rows ? html`<${Spinner} />` : !rows.length ? html`<${Empty} title="No follow-ups." icon="list-todo"><span>Action items from your meeting notes appear here.</span></${Empty}>`
      : html`<div class="fu-list">${rows.map((r) => html`<div class=${`fu-row ${r.done ? 'done' : ''}`}>
          <div class="grow" style="min-width:0"><div class="fu-text">${r.text}</div>
            <div class="fu-meta">${r.owner ? html`<span class="chip">${r.owner}</span>` : null}
              ${r.due ? html`<span class=${r.due < today() && !r.done ? 'red-text' : ''}><${Icon} name="clock" size="sm" />due ${fmtDate(r.due)}</span>` : null}
              <a href=${`#/notes/${r.note_id}`}>${r.date ? `${fmtDate(r.date)} · ` : ''}${r.title}</a></div></div>
          ${r.tracked ? html`<span class="faint row" style="gap:4px;font-size:12px"><${Icon} name="check" size="sm" />In Tasks</span>`
            : html`<button class="btn btn-sm" onClick=${() => track(r)}><${Icon} name="plus" />Track</button>`}
        </div>`)}</div>`}
  </div>`;
}

// ---------------------------------------------------------------------------
// People
// ---------------------------------------------------------------------------
function People() {
  const [rows, setRows] = useState(null);
  const [q, setQ] = useState('');
  useEffect(() => { get('/api/library/people').then(setRows).catch(toastError); }, []);
  const list = (rows || []).filter((p) => !q || p.name.toLowerCase().includes(q.toLowerCase()));
  return html`<div class="stack">
    <div class="capture" style="max-width:360px"><${Icon} name="search" cls="faint" /><input value=${q} placeholder="Find someone" aria-label="Find someone" onInput=${(e) => setQ(e.currentTarget.value)} /></div>
    ${!rows ? html`<${Spinner} />` : !list.length ? html`<${Empty} title="No people yet." icon="users"><span>Names come from the attendee lines and action-item owners in your meeting notes.</span></${Empty}>`
      : html`<div class="people-grid">${list.map((p) => html`<a class="person" href=${`#/library/timeline?person=${encodeURIComponent(p.name)}`}>
          <span class="avatar">${p.name.split(/\s+/).map((w) => w[0]).slice(0, 2).join('')}</span>
          <span class="grow"><strong>${p.name}</strong><span class="faint">${p.meetings} meeting${p.meetings === 1 ? '' : 's'}${p.last ? ` · last ${fmtDate(p.last)}` : ''}</span>
            ${p.open_actions ? html`<span class="faint">${p.open_actions} open action${p.open_actions === 1 ? '' : 's'}</span>` : null}</span></a>`)}</div>`}
    <p class="faint" style="font-size:12px">From attendee and owner fields in meeting notes. Click a name to see their meetings on the timeline.</p>
  </div>`;
}

// ---------------------------------------------------------------------------
// Mockups
// ---------------------------------------------------------------------------
function Mockups() {
  const [rows, setRows] = useState(null);
  useEffect(() => { get('/api/library/mockups').then(setRows).catch(toastError); }, []);
  useChanges(['notes'], () => get('/api/library/mockups').then(setRows).catch(() => {}), []);
  if (!rows) return html`<${Spinner} />`;
  if (!rows.length) return html`<${Empty} title="No mockups or images yet." icon="app-window"><span>HTML mockups and images in your folder show up here.</span></${Empty}>`;
  return html`<div class="mock-grid">${rows.map((m) => html`<a class="mock-card" href=${`#/notes/${m.id}`}>
      <div class="mock-thumb">${m.lib_kind === 'mockup'
        ? html`<iframe src=${`/api/library/file?path=${encodeURIComponent(m.lib_path)}`} sandbox="allow-scripts" loading="lazy" tabindex="-1" title=${m.title}></iframe>`
        : html`<img src=${`/api/library/file?path=${encodeURIComponent(m.lib_path)}`} alt=${m.title} loading="lazy" />`}</div>
      <div class="mock-meta"><strong>${m.title}</strong><span class="faint">${m.notebook || ''}${m.linked_date ? ` · ${fmtDate(m.linked_date)}` : ''}</span></div></a>`)}</div>`;
}

// ---------------------------------------------------------------------------
// Folder & git
// ---------------------------------------------------------------------------
function Status({ status }) {
  const [git, setGit] = useState(null);
  useEffect(() => { get('/api/library/git').then(setGit).catch(() => setGit({ repo: false })); }, [status?.last_scan]);
  if (!status) return html`<${Spinner} />`;
  return html`<div class="lib-status">
    <section class="panel">
      <div class="panel-head"><h2>What's in the folder</h2><span class="faint" style="font-size:12px">Last checked ${fmtAgo(status.last_scan)}</span></div>
      <div class="kind-grid">${Object.entries(status.counts || {}).sort((a, b) => b[1] - a[1]).map(([k, n]) => html`
        <a class="kind-cell" href=${`#/notes?view=library&kind=${k}`}><${Icon} name=${(KIND[k] || {}).icon || 'file'} /><strong>${n}</strong><span>${(KIND[k] || { label: k }).label}</span></a>`)}</div>
      <div class="row row-wrap" style="margin-top:12px;gap:16px;font-size:13px">
        <span><strong>${status.dated}</strong> on the calendar</span>
        <a href="#/library/review"><strong>${status.review}</strong> need a date</a>
        <span><strong>${status.decisions}</strong> dates chosen by you, AI or Claude</span>
      </div>
      ${status.errors?.length ? html`<div class="banner banner-warn" style="margin-top:12px"><${Icon} name="triangle-alert" /><div>
        <strong>${status.errors.length} file${status.errors.length === 1 ? '' : 's'} couldn't be read</strong>
        <ul style="margin:6px 0 0 16px">${status.errors.slice(0, 8).map((e) => html`<li><span class="mono">${e.path}</span>: ${e.error}</li>`)}</ul></div></div>` : null}
    </section>
    <section class="panel">
      <div class="panel-head"><h2><${Icon} name="git-branch" /> Git</h2></div>
      ${!git ? html`<${Spinner} />` : !git.repo ? html`<p class="muted">This folder isn't a git repository.</p>` : git.error ? html`<p class="muted">${git.error}</p>` : html`
        <p style="font-size:14px">On <strong>${git.branch}</strong>${git.upstream ? html` tracking <span class="mono">${git.upstream}</span>` : ''}.
          ${git.ahead ? html` <span class="red-text">${git.ahead} commit${git.ahead === 1 ? '' : 's'} not pushed.</span>` : ''}
          ${git.behind ? html` ${git.behind} to pull.` : ''}</p>
        ${git.last_commit ? html`<p class="faint" style="font-size:12px;margin-top:4px">Last commit ${fmtAgo(Date.parse(git.last_commit.when))}: “${git.last_commit.message}”</p>` : null}
        ${git.changed ? html`<div class="section-title" style="margin-top:12px">${git.changed} change${git.changed === 1 ? '' : 's'} not committed</div>
          <ul class="git-list">${git.changes.slice(0, 40).map((c) => html`<li><span class="mono code">${c.code}</span><span class="mono">${c.path}</span></li>`)}</ul>
          <p class="faint" style="font-size:12px">Commit and push from GitHub Desktop or the terminal when you're ready. Daybook never pushes anything.</p>`
          : html`<p class="muted" style="margin-top:8px">Everything is committed.</p>`}`}
    </section>
    <section class="panel">
      <div class="panel-head"><h2><${Icon} name="sparkles" /> Dating files with Claude or a script</h2></div>
      <p class="muted" style="font-size:13px;line-height:1.6">The date scrubber also runs on its own, without Daybook or its virtual environment. Claude (or you) can review and set dates from a terminal:</p>
      <pre class="code-block">cd "${status.root}"
python3 Daybook/daybook-dates scan            # files that need a date
python3 Daybook/daybook-dates explain FILE    # every clue, with scores
python3 Daybook/daybook-dates set FILE 2026-10-23 --by claude --note "why"</pre>
      <p class="faint" style="font-size:12px">Decisions land in <span class="mono">${status.sidecar}</span>; Daybook picks them up within seconds.</p>
    </section>
  </div>`;
}
