// Tasks: quick capture with plain language, due dates, priorities, lists.
import { html, useEffect, useState } from '/vendor/preact.js';
import { del, get, post, put, qs } from '../lib/api.js';
import { emitChange, setState, toast, toastError, useChanges, useStore } from '../lib/store.js';
import { navigate } from '../lib/router.js';
import { fmtDayRelative, fmtTime, today } from '../lib/dates.js';
import { Empty, Field, Icon, Modal, Seg, Spinner, confirmDialog } from '../lib/ui.js';

export function openTaskEditor(task) { setState({ taskEditor: task || {} }); }

const PRIO_LABEL = ['None', 'Low', 'Medium', 'High'];

export function TaskRow({ task, onToggle, showList = true }) {
  const overdue = !task.done && task.due_date && task.due_date < today();
  return html`<div class=${`task-row ${task.done ? 'done' : ''}`}>
    <input type="checkbox" class=${`check prio-${task.priority}`} checked=${task.done} onChange=${onToggle} aria-label=${`Mark "${task.title}" ${task.done ? 'not done' : 'done'}`} />
    <div class="grow" onClick=${() => openTaskEditor(task)} role="button" tabindex="0" onKeyDown=${(e) => { if (e.key === 'Enter') openTaskEditor(task); }}>
      <div class="task-title">${task.priority > 0 ? html`<span class=${`prio-flag p${task.priority}`}>${'!'.repeat(task.priority)} </span>` : null}${task.title}</div>
      ${task.due_date || task.list || task.note_id || task.details || task.blocked_at ? html`<div class="task-meta">
        ${task.due_date ? html`<span class=${overdue ? 'overdue' : ''}>${overdue ? 'Overdue, ' : ''}${fmtDayRelative(task.due_date)}${task.due_time ? `, ${fmtTime(task.due_time)}` : ''}</span>` : null}
        ${task.remind !== null && task.remind !== undefined ? html`<${Icon} name="bell" size="sm" />` : null}
        ${task.blocked_at && !task.done ? html`<span class="row" style="gap:3px" title="Time set aside on your calendar"><${Icon} name="calendar-check" size="sm" />${fmtDayRelative(task.blocked_at.slice(0, 10))}, ${fmtTime(task.blocked_at.slice(11, 16))}</span>` : null}
        ${showList && task.list ? html`<span class="chip">${task.list}</span>` : null}
        ${task.note_id ? html`<a href=${`#/notes/${task.note_id}`} onClick=${(e) => e.stopPropagation()} class="row" style="gap:3px"><${Icon} name="file-text" size="sm" />Note</a>` : null}
        ${task.details ? html`<${Icon} name="pilcrow" size="sm" />` : null}
      </div>` : null}
    </div>
  </div>`;
}

const VIEWS = [
  { key: 'today', label: 'Today', icon: 'sun' },
  { key: 'upcoming', label: 'Upcoming', icon: 'calendar-range' },
  { key: 'someday', label: 'No date', icon: 'inbox' },
  { key: 'open', label: 'All open', icon: 'list-todo' },
  { key: 'done', label: 'Done', icon: 'circle-check' },
];

export function TasksView({ route }) {
  const view = route.parts[1] || 'today';
  const list = route.params.get('list');
  const [data, setData] = useState(null);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);

  const load = async () => {
    try { setData(await get(`/api/tasks${qs({ view: list ? 'open' : view, list })}`)); } catch (err) { toastError(err); }
  };
  useEffect(() => { load(); }, [view, list]);
  useChanges(['tasks'], () => load(), [view, list]);

  const add = async (e) => {
    e.preventDefault();
    if (!text.trim()) return;
    setBusy(true);
    try {
      const p = await post('/api/quick/parse', { text: `task: ${text}` });
      await post('/api/quick/create', { ...p, type: 'task', tags: list ? [list.toLowerCase()] : p.tags, date: p.date || (view === 'today' ? today() : null) });
      setText('');
      emitChange({ entity: 'tasks' });
    } catch (err) { toastError(err); }
    setBusy(false);
  };
  const toggle = async (t) => {
    try {
      await put(`/api/tasks/${t.id}`, { done: !t.done });
      if (!t.done) toast(`Done: ${t.title}`, { action: { label: 'Undo', run: async () => { await put(`/api/tasks/${t.id}`, { done: false }); emitChange({ entity: 'tasks' }); } } });
      emitChange({ entity: 'tasks' });
    } catch (err) { toastError(err); }
  };
  const clearDone = async () => {
    if (!(await confirmDialog({ title: 'Clear completed tasks?', message: 'Completed tasks will be deleted permanently.', confirm: 'Clear completed', danger: true }))) return;
    try { await post('/api/tasks/clear-done'); emitChange({ entity: 'tasks' }); } catch (err) { toastError(err); }
  };

  const counts = data?.counts || {};
  const items = data?.items || [];
  const overdue = view === 'today' && !list ? items.filter((t) => t.due_date && t.due_date < today()) : [];
  const rest = view === 'today' && !list ? items.filter((t) => !(t.due_date && t.due_date < today())) : items;
  const countFor = { today: counts.today + counts.overdue, upcoming: counts.upcoming, someday: counts.someday, open: counts.open, done: counts.done };
  const heading = list || VIEWS.find((v) => v.key === view)?.label || 'Tasks';

  return html`<div class="view">
    <div class="view-head"><div><h1 class="view-title">${heading}</h1>
      <p class="view-sub">${list ? `List: ${list}` : 'Type a task in plain words: "Renew passport in 2 weeks !high #personal"'}</p></div>
      <div class="row"><button class="btn btn-primary" onClick=${() => openTaskEditor({ due_date: view === 'today' ? today() : null, list })}><${Icon} name="plus" />Task</button>
        ${view === 'done' ? html`<button class="btn btn-danger" onClick=${clearDone}>Clear completed</button>` : null}</div>
    </div>
    <div class="tasks-layout">
      <nav class="tasks-nav" aria-label="Task views">
        ${VIEWS.map((v) => html`<button aria-current=${String(!list && view === v.key)} onClick=${() => navigate(`#/tasks/${v.key}`)}>
          <${Icon} name=${v.icon} /><span>${v.label}</span><span class=${`n ${v.key === 'today' && counts.overdue ? 'red' : ''}`}>${countFor[v.key] || ''}</span></button>`)}
        ${(data?.lists || []).length ? html`<div class="label" style="padding:12px 10px 4px">Lists</div>` : null}
        ${(data?.lists || []).map((l) => html`<button aria-current=${String(list === l.name)} onClick=${() => navigate(`#/tasks/open?list=${encodeURIComponent(l.name)}`)}>
          <${Icon} name="list" /><span>${l.name}</span><span class="n">${l.open || ''}</span></button>`)}
      </nav>
      <div>
        ${view !== 'done' ? html`<form class="capture" onSubmit=${add} style="margin-bottom:12px">
          <${Icon} name="plus" cls="faint" />
          <input value=${text} onInput=${(e) => setText(e.currentTarget.value)} placeholder=${view === 'today' ? 'Add a task for today' : 'Add a task'} aria-label="New task" />
          <button class="btn btn-primary btn-sm" disabled=${!text.trim() || busy}>Add</button></form>` : null}
        ${!data ? html`<${Spinner} />` : null}
        ${overdue.length ? html`<div class="section-title red" style="margin:8px 0 2px">Overdue</div>${overdue.map((t) => html`<${TaskRow} task=${t} onToggle=${() => toggle(t)} />`)}
          <div class="section-title" style="margin:16px 0 2px">Today</div>` : null}
        ${rest.map((t) => html`<${TaskRow} task=${t} onToggle=${() => toggle(t)} showList=${!list} />`)}
        ${data && !items.length ? html`<${Empty} title=${view === 'done' ? 'Nothing completed yet.' : 'All clear.'}>
          <span>${view === 'today' ? 'Nothing is due today. Enjoy it, or plan ahead in Upcoming.' : 'Add a task above.'}</span></${Empty}>` : null}
      </div>
    </div>
  </div>`;
}

export function TaskEditorHost() {
  const task = useStore((s) => s.taskEditor);
  if (!task) return null;
  return html`<${TaskEditor} initial=${task} onClose=${() => setState({ taskEditor: null })} />`;
}

function TaskEditor({ initial, onClose }) {
  const isNew = !initial.id;
  const [f, setF] = useState({
    title: initial.title || '', details: initial.details || '', due_date: initial.due_date || '', due_time: initial.due_time || '',
    priority: initial.priority || 0, list: initial.list || '', remind: initial.remind ?? null, note_id: initial.note_id || null, done: !!initial.done,
  });
  const [busy, setBusy] = useState(false);
  const set = (p) => setF((x) => ({ ...x, ...p }));
  const save = async (e) => {
    e && e.preventDefault();
    if (!f.title.trim()) { toast('Add a title first.', { error: true }); return; }
    setBusy(true);
    const body = { ...f, due_date: f.due_date || null, due_time: f.due_date && f.due_time ? f.due_time : null, remind: f.due_date ? f.remind : null };
    try {
      if (isNew) await post('/api/tasks', body); else await put(`/api/tasks/${initial.id}`, body);
      emitChange({ entity: 'tasks' });
      onClose();
    } catch (err) { toastError(err); setBusy(false); }
  };
  const remove = async () => {
    try { await del(`/api/tasks/${initial.id}`); emitChange({ entity: 'tasks' }); toast('Task deleted'); onClose(); } catch (err) { toastError(err); }
  };
  return html`<${Modal} title=${isNew ? 'New task' : 'Task'} onClose=${onClose} footer=${html`
      ${!isNew ? html`<button class="btn btn-danger left" onClick=${remove}><${Icon} name="trash-2" />Delete</button>` : null}
      <button class="btn" onClick=${onClose}>Cancel</button>
      <button class="btn btn-primary" onClick=${save} disabled=${busy}>${isNew ? 'Add task' : 'Save'}</button>`}>
    <form class="stack" onSubmit=${save}>
      <div class="row"><input type="checkbox" class="check" checked=${f.done} onChange=${(e) => set({ done: e.currentTarget.checked })} aria-label="Done" />
        <input class="input" style="font-size:17px;font-weight:600" placeholder="What needs doing?" value=${f.title} onInput=${(e) => set({ title: e.currentTarget.value })} autofocus aria-label="Title" /></div>
      <div class="grid-2">
        <${Field} label="Due date"><input class="input" type="date" value=${f.due_date} onInput=${(e) => set({ due_date: e.currentTarget.value })} /></${Field}>
        <${Field} label="Time (optional)"><input class="input" type="time" value=${f.due_time} disabled=${!f.due_date} onInput=${(e) => set({ due_time: e.currentTarget.value })} /></${Field}>
      </div>
      <div class="row row-wrap" style="gap:6px">
        ${[['Today', 0], ['Tomorrow', 1], ['Next week', 7]].map(([l, n]) => html`<button type="button" class="chip chip-btn" onClick=${() => { const d = new Date(); d.setDate(d.getDate() + n); set({ due_date: `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}` }); }}>${l}</button>`)}
        <button type="button" class="chip chip-btn" onClick=${() => set({ due_date: '', due_time: '' })}>No date</button>
      </div>
      <${Field} label="Priority"><${Seg} value=${f.priority} onChange=${(v) => set({ priority: v })} options=${PRIO_LABEL.map((l, i) => ({ value: i, label: l }))} /></${Field}>
      <div class="grid-2">
        <${Field} label="List"><input class="input" value=${f.list} placeholder="e.g. Work, Errands" onInput=${(e) => set({ list: e.currentTarget.value })} /></${Field}>
        <${Field} label="Remind me"><select class="select" value=${f.remind === null ? 'none' : String(f.remind)} disabled=${!f.due_date} onChange=${(e) => set({ remind: e.currentTarget.value === 'none' ? null : Number(e.currentTarget.value) })}>
          <option value="none">No reminder</option><option value="0">When due</option><option value="15">15 minutes before</option><option value="60">1 hour before</option><option value="1440">1 day before</option>
        </select></${Field}>
      </div>
      <${Field} label="Details"><textarea class="textarea" value=${f.details} onInput=${(e) => set({ details: e.currentTarget.value })}></textarea></${Field}>
      ${f.note_id ? html`<a class="btn btn-ghost" href=${`#/notes/${f.note_id}`} onClick=${onClose}><${Icon} name="file-text" />Open the linked note</a>` : null}
      ${!isNew && !f.done ? html`<${BlockTime} task=${initial} onBlocked=${onClose} />` : null}
    </form>
  </${Modal}>`;
}

// Set time aside for a task: Daybook finds open stretches on your calendar for that day.
const LENGTHS = [{ value: 30, label: '30 min' }, { value: 60, label: '1 hour' }, { value: 90, label: '90 min' }, { value: 120, label: '2 hours' }];
function BlockTime({ task, onBlocked }) {
  const [minutes, setMinutes] = useState(60);
  const [day, setDay] = useState('');
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let live = true;
    get(`/api/tasks/${task.id}/time${qs({ day: day || undefined, minutes })}`).then((r) => { if (live) { setData(r); if (!day) setDay(r.day); } }).catch(toastError);
    return () => { live = false; };
  }, [task.id, day, minutes]);
  const block = async (slot) => {
    setBusy(true);
    try {
      const ev = await post(`/api/tasks/${task.id}/time`, { start: slot.start, minutes });
      emitChange({ entity: 'events' }); emitChange({ entity: 'tasks' });
      toast(`Set aside ${fmtDayRelative(ev.start.slice(0, 10))}, ${fmtTime(ev.start.slice(11, 16))} for “${task.title}”.`,
        { action: { label: 'Open', run: () => navigate(`#/calendar/${ev.start.slice(0, 10)}?view=day`) } });
      onBlocked && onBlocked();
    } catch (err) { toastError(err); setBusy(false); }
  };
  const upcoming = (data?.blocks || []).filter((b) => b.end >= new Date().toISOString().slice(0, 16));
  return html`<section class="block-time">
    <div class="label row"><${Icon} name="calendar-clock" size="sm" />Make time for it</div>
    ${upcoming.length ? html`<div class="bt-booked">${upcoming.map((b) => html`<a class="chip" href=${`#/calendar/${b.start.slice(0, 10)}?view=day`} onClick=${onBlocked}>
      <${Icon} name="calendar-check" size="sm" />${fmtDayRelative(b.start.slice(0, 10))}, ${fmtTime(b.start.slice(11, 16))}</a>`)}</div>` : null}
    <div class="row row-wrap" style="gap:8px">
      <input class="input input-sm" type="date" value=${day} min=${today()} onInput=${(e) => setDay(e.currentTarget.value)} aria-label="Day" style="width:auto" />
      <${Seg} value=${minutes} onChange=${setMinutes} label="How long" options=${LENGTHS} />
    </div>
    ${!data ? html`<${Spinner} />` : data.slots.length ? html`<div class="bt-slots">${data.slots.map((sl) => html`<button type="button" class="chip chip-btn" disabled=${busy}
        onClick=${() => block(sl)} title="Add to your calendar">${fmtTime(sl.start.slice(11, 16))}–${fmtTime(sl.end.slice(11, 16))}</button>`)}</div>`
      : html`<p class="muted" style="font-size:13px">No open ${minutes >= 60 ? `${minutes / 60}-hour` : `${minutes}-minute`} stretch between 8 and 6 that day.</p>`}
  </section>`;
}
