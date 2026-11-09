// Quick capture: type naturally, Daybook works out if it's an event, task or note.
import { html, useEffect, useRef, useState } from '/vendor/preact.js';
import { get, post } from '../lib/api.js';
import { emitChange, getState, toast, toastError, useStore } from '../lib/store.js';
import { navigate } from '../lib/router.js';
import { fmtDayRelative, fmtTime } from '../lib/dates.js';
import { Icon, Modal, Seg, openMenu, useDebounced } from '../lib/ui.js';
import { openEventEditor } from '../views/calendar.js';
import { openTaskEditor } from '../views/tasks.js';

const RRULE_TEXT = (r) => {
  if (!r) return '';
  if (r.includes('BYDAY=MO,TU,WE,TH,FR') && !r.includes('INTERVAL')) return 'Every weekday';
  const every = r.includes('INTERVAL=2') ? 'Every other' : 'Every';
  if (r.startsWith('FREQ=DAILY')) return `${every} day`;
  if (r.startsWith('FREQ=WEEKLY')) {
    const m = r.match(/BYDAY=([A-Z,]+)/);
    const names = { MO: 'Mon', TU: 'Tue', WE: 'Wed', TH: 'Thu', FR: 'Fri', SA: 'Sat', SU: 'Sun' };
    return m ? `${every} ${m[1].split(',').map((d) => names[d]).join(', ')}` : `${every} week`;
  }
  if (r.startsWith('FREQ=MONTHLY')) return `${every} month`;
  if (r.startsWith('FREQ=YEARLY')) return `${every} year`;
  return 'Repeats';
};

export function Capture({ autofocus = false, onDone, placeholder }) {
  const [text, setText] = useState('');
  const [parsed, setParsed] = useState(null);
  const [type, setType] = useState(null);
  const [busy, setBusy] = useState(false);
  const [aiBusy, setAiBusy] = useState(false);
  const ai = useStore((s) => s.ai);
  const input = useRef(null);
  const reqId = useRef(0);

  const parse = useDebounced(async (value) => {
    const id = ++reqId.current;
    if (!value.trim()) { setParsed(null); return; }
    try {
      const res = await post('/api/quick/parse', { text: value });
      if (id === reqId.current) { setParsed(res); setType(null); }
    } catch { /* ignore */ }
  }, 160);

  useEffect(() => { if (autofocus && input.current) input.current.focus(); }, []);

  const kind = type || parsed?.type || 'task';

  const askAi = async () => {
    setAiBusy(true);
    try {
      const res = await post('/api/ai/quick', { text });
      setParsed({ ...parsed, ...res, tags: parsed?.tags || [] });
      setType(null);
      toast(`Interpreted by ${res.model} in ${(res.ms / 1000).toFixed(1)}s`);
    } catch (err) { toastError(err); }
    setAiBusy(false);
  };

  const create = async () => {
    if (!text.trim() || busy) return;
    setBusy(true);
    try {
      const p = parsed || (await post('/api/quick/parse', { text }));
      const payload = { ...p, type: kind };
      if (kind === 'note') { payload.title = p.title; payload.body = ''; }
      const res = await post('/api/quick/create', payload);
      const item = res.item;
      const where = res.type === 'event' ? `#/calendar/${(item.start || '').slice(0, 10)}` : res.type === 'task' ? '#/tasks' : `#/notes/${item.id}`;
      toast(`${res.type === 'event' ? 'Event added' : res.type === 'task' ? 'Task added' : 'Note created'}: ${item.title}`, {
        action: { label: 'Open', run: () => (res.type === 'task' ? openTaskEditor(item) : navigate(where)) },
      });
      emitChange({ entity: res.type === 'event' ? 'events' : res.type === 'task' ? 'tasks' : 'notes' });
      setText('');
      setParsed(null);
      setType(null);
      if (res.type === 'note') navigate(where);
      if (onDone) onDone(res);
    } catch (err) { toastError(err); }
    setBusy(false);
  };

  const chips = [];
  if (parsed && text.trim()) {
    if (kind !== 'note' && parsed.date) chips.push(fmtDayRelative(parsed.date));
    if (kind === 'event') chips.push(parsed.all_day || !parsed.start_time ? 'All day' : `${fmtTime(parsed.start_time)} – ${fmtTime(parsed.end_time)}`);
    if (kind === 'task' && parsed.start_time) chips.push(fmtTime(parsed.start_time));
    if (kind === 'event' && parsed.rrule) chips.push(RRULE_TEXT(parsed.rrule));
    if (parsed.location) chips.push(parsed.location);
    if (kind === 'task' && parsed.priority && parsed.priority !== 'none') chips.push(`${parsed.priority} priority`);
    for (const t of parsed.tags || []) chips.push(`#${t}`);
  }

  return html`<div>
    <form class="capture" onSubmit=${(e) => { e.preventDefault(); create(); }}>
      <${Icon} name="plus" cls="faint" />
      <input ref=${input} value=${text} placeholder=${placeholder || 'Add anything: "Lunch with Kelly tomorrow 12:30" or "Send cost deck Friday !high"'}
        aria-label="Quick capture" onInput=${(e) => { setText(e.currentTarget.value); parse(e.currentTarget.value); }} />
      ${ai?.ok && text.trim() ? html`<button type="button" class="btn btn-ghost btn-sm" onClick=${askAi} disabled=${aiBusy} title="Let your local AI interpret this">
        ${aiBusy ? html`<span class="spinner"></span>` : html`<${Icon} name="sparkles" />`}<span>Interpret</span></button>` : null}
      <button class="btn btn-primary btn-sm" disabled=${!text.trim() || busy}>Add</button>
    </form>
    ${parsed && text.trim() ? html`<div class="capture-preview">
      <${Seg} value=${kind} onChange=${setType} label="Capture as" options=${[{ value: 'event', label: 'Event' }, { value: 'task', label: 'Task' }, { value: 'note', label: 'Note' }]} />
      <strong>${parsed.title}</strong>
      ${chips.map((c) => html`<span class="chip">${c}</span>`)}
    </div>` : null}
  </div>`;
}

async function fromTemplate(anchor, onClose) {
  try {
    const list = await get('/api/templates');
    openMenu(anchor, [{ header: 'Start from a template' }, ...list.map((t) => ({
      label: t.name, icon: t.builtin ? 'file-text' : 'star',
      onSelect: async () => {
        try {
          const note = await post('/api/notes', { template_id: t.id });
          onClose();
          navigate(`#/notes/${note.id}`);
        } catch (err) { toastError(err); }
      },
    }))]);
  } catch (err) { toastError(err); }
}

export function CaptureModal({ onClose }) {
  const newNote = async () => {
    try {
      const note = await post('/api/notes', { notebook_id: new URLSearchParams(location.hash.split('?')[1] || '').get('notebook') || null });
      onClose();
      navigate(`#/notes/${note.id}`);
    } catch (err) { toastError(err); }
  };
  const daily = async () => {
    try {
      const { today } = await import('../lib/dates.js');
      const note = await get(`/api/daily/${today()}`);
      onClose();
      navigate(`#/notes/${note.id}`);
    } catch (err) { toastError(err); }
  };
  return html`<${Modal} title="Add to Daybook" onClose=${onClose}>
    <div class="stack-lg">
      <${Capture} autofocus=${true} onDone=${onClose} />
      <div class="grid-2">
        <button class="btn" onClick=${newNote}><${Icon} name="file-plus" />Blank note</button>
        <button class="btn" onClick=${(e) => fromTemplate(e.currentTarget, onClose)}><${Icon} name="file-text" />From a template</button>
        <button class="btn" onClick=${() => { onClose(); openEventEditor({}); }}><${Icon} name="calendar-plus" />Event with details</button>
        <button class="btn" onClick=${() => { onClose(); openTaskEditor({}); }}><${Icon} name="square-check" />Task with details</button>
        <button class="btn" onClick=${daily}><${Icon} name="notebook-pen" />Today's daily note</button>
        <button class="btn" onClick=${() => { onClose(); navigate('#/import'); }}><${Icon} name="file-up" />Import documents</button>
      </div>
      <p class="faint" style="font-size:12px">Shortcuts: <span class="kbd">N</span> new, <span class="kbd">/</span> search, <span class="kbd">G</span> then <span class="kbd">C</span> calendar.</p>
    </div>
  </${Modal}>`;
}

export { RRULE_TEXT };
