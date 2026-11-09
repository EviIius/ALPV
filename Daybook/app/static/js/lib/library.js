// Library helpers shared by views: file kinds, date chips, the date picker and "why this date".
import { html, useEffect, useState } from '/vendor/preact.js';
import { post } from './api.js';
import { emitChange, setState, toast, toastError, useStore } from './store.js';
import { MON_SHORT, WD_SHORT, fmtDate, fmtTime, parseYmd } from './dates.js';
import { Field, Icon, Modal, openMenu } from './ui.js';
import { navigate } from './router.js';

export const KIND = {
  markdown: { icon: 'notebook-pen', label: 'Note' },
  document: { icon: 'file-text', label: 'Word' },
  pdf: { icon: 'file', label: 'PDF' },
  deck: { icon: 'presentation', label: 'Deck' },
  sheet: { icon: 'file-spreadsheet', label: 'Sheet' },
  transcript: { icon: 'audio-lines', label: 'Recording' },
  text: { icon: 'file-text', label: 'Text' },
  mockup: { icon: 'app-window', label: 'Mockup' },
  email: { icon: 'mail', label: 'Email' },
  image: { icon: 'file-image', label: 'Image' },
  code: { icon: 'file-code', label: 'Code' },
};
export function kindOf(n) {
  if (n.lib_kind && KIND[n.lib_kind]) return KIND[n.lib_kind];
  if (n.kind === 'import') return { icon: 'file-up', label: 'Imported' };
  if (n.kind === 'daily') return { icon: 'sun', label: 'Daily' };
  return { icon: 'notebook-pen', label: 'Note' };
}

export const SOURCE = {
  manual: 'You picked this date', you: 'You picked this date', claude: 'Claude picked this date',
  ai: 'Your local AI picked this date', frontmatter: "From the file's front matter", filename: 'From the file name',
  title: 'From the title', 'content-label': 'From a date line in the document', 'content-head': 'From the top of the document',
  body: 'Repeated through the document', email: 'When the email was sent', metadata: "From the document's properties",
  git: 'When the file was added to the repo', mtime: 'When the file last changed', 'new-file': 'When the file appeared',
  created: 'The day you created it', daily: 'Daily note', none: 'Marked as having no date',
};

const shortDay = (s) => { const d = parseYmd(s); return `${MON_SHORT[d.getMonth()]} ${d.getDate()}`; };

// "Oct 23", "Oct 23, 2:30 PM", "Nov 17–18"
export function dateText(n, { time = true, weekday = false } = {}) {
  if (!n.linked_date) return '';
  let s = fmtDate(n.linked_date, { weekday });
  if (n.date_end && n.date_end !== n.linked_date) {
    const a = parseYmd(n.linked_date);
    const b = parseYmd(n.date_end);
    s += a.getMonth() === b.getMonth() ? `–${b.getDate()}` : ` – ${shortDay(n.date_end)}`;
  }
  if (time && n.date_time) s += `, ${fmtTime(n.date_time)}`;
  return s;
}

export function DateChip({ n, compact = false }) {
  const st = n.date_status;
  if (n.linked_date) {
    return html`<span class=${`date-chip ${st === 'review' ? 'is-review' : ''}`} title=${SOURCE[n.date_source] || ''}>
      <${Icon} name="calendar" size="sm" />${dateText(n, { time: !compact })}</span>`;
  }
  if (st === 'suggested') return html`<span class="date-chip is-suggested" title="Suggested; needs a yes"><${Icon} name="calendar-search" size="sm" />Needs a date</span>`;
  if (st === 'none' && n.lib_path) return html`<span class="date-chip is-none"><${Icon} name="calendar-x" size="sm" />No date found</span>`;
  return null;
}

export async function setNoteDate(noteId, payload, msg) {
  try {
    const res = await post(`/api/notes/${noteId}/date`, payload);
    emitChange({ entity: 'notes', id: noteId });
    if (msg !== false) toast(msg || (payload.none ? 'Marked as having no date' : payload.clear ? 'Back to automatic dating' : `Filed on ${fmtDate(payload.date)}`));
    return res;
  } catch (err) { toastError(err); return null; }
}

export async function acceptSuggestion(noteId, which = 'rules') {
  try {
    await post(`/api/library/dates/${noteId}`, { accept: which === 'ai' ? 'ai' : true });
    emitChange({ entity: 'notes', id: noteId });
    toast('Date saved');
  } catch (err) { toastError(err); }
}

// ---------------------------------------------------------------------------
// Date dialog (pick a date, an optional end and time; shows the clues)
// ---------------------------------------------------------------------------
export function openDateDialog(note) { setState({ dateDialog: { note } }); }

export function DateDialogHost() {
  const d = useStore((s) => s.dateDialog);
  if (!d) return null;
  return html`<${DateDialog} note=${d.note} onClose=${() => setState({ dateDialog: null })} />`;
}

function DateDialog({ note, onClose }) {
  const info = note.date_info || {};
  const sug = info.suggestion || {};
  const [day, setDay] = useState(note.linked_date || sug.date || '');
  const [end, setEnd] = useState(note.date_end || '');
  const [time, setTime] = useState(note.date_time || '');
  const [multi, setMulti] = useState(!!note.date_end);
  const save = async (e) => {
    e && e.preventDefault();
    if (!day) return;
    const res = await setNoteDate(note.id, { date: day, end: multi && end > day ? end : null, time: time || null });
    if (res) onClose();
  };
  const cands = (info.candidates || []).filter((c, i, a) => a.findIndex((x) => x.date === c.date && x.source === c.source) === i);
  return html`<${Modal} title="Which day is this?" onClose=${onClose} footer=${html`
      <button class="btn" onClick=${onClose}>Cancel</button>
      <button class="btn btn-primary" onClick=${save} disabled=${!day}>Save date</button>`}>
    <form class="stack" onSubmit=${save}>
      <p class="muted" style="font-size:13px;line-height:1.5">${note.title || 'Untitled'}${note.lib_path ? html`<br /><span class="mono faint">${note.lib_path}</span>` : null}</p>
      <div class="row row-wrap">
        <${Field} label="Date"><input class="input" type="date" value=${day} onInput=${(e) => setDay(e.currentTarget.value)} autofocus /></${Field}>
        <${Field} label="Time (optional)"><input class="input" type="time" value=${time} onInput=${(e) => setTime(e.currentTarget.value)} /></${Field}>
      </div>
      <label class="row" style="gap:8px;font-size:13px"><input type="checkbox" class="check" checked=${multi} onChange=${(e) => setMulti(e.currentTarget.checked)} />Spans several days</label>
      ${multi ? html`<${Field} label="Last day"><input class="input" type="date" min=${day} value=${end} onInput=${(e) => setEnd(e.currentTarget.value)} /></${Field}>` : null}
      ${cands.length ? html`<div><div class="section-title">Clues Daybook found</div>
        <div class="clue-list">${cands.map((c) => html`<button type="button" class=${`clue ${c.date === day ? 'on' : ''}`} onClick=${() => { setDay(c.date); if (c.end) { setMulti(true); setEnd(c.end); } if (c.time) setTime(c.time); }}>
          <strong>${fmtDate(c.date, { weekday: true, year: true })}${c.end ? ` – ${shortDay(c.end)}` : ''}</strong>
          <span>${SOURCE[c.source] || c.source}${c.evidence ? html`: <span class="faint">${c.evidence}</span>` : ''}</span>
          <span class="score">${Math.round(c.score * 100)}%</span></button>`)}</div></div>` : null}
      ${note.lib_path ? html`<p class="faint" style="font-size:12px;line-height:1.5">Saved in <span class="mono">.daybook/dates.json</span> inside your library, so it travels with the files.</p>` : null}
    </form>
  </${Modal}>`;
}

export function WhyDate({ note }) {
  const info = note.date_info || {};
  const cands = info.candidates || [];
  return html`<div class="why-date">
    <p><strong>${note.linked_date ? dateText(note, { weekday: true }) : 'No date yet'}</strong>
      ${note.date_source ? html` — ${SOURCE[note.date_source] || note.date_source}` : ''}
      ${note.date_confidence != null && note.date_status !== 'manual' ? html` <span class="faint">(${Math.round(note.date_confidence * 100)}% sure)</span>` : ''}</p>
    ${info.conflict ? html`<p class="red-text">Some clues point to a different day. Worth a check.</p>` : null}
    ${cands.length ? html`<ul class="clue-plain">${cands.map((c) => html`<li><span class="mono">${c.date}</span> ${SOURCE[c.source] || c.source}
      <span class="faint">${c.evidence ? `— ${c.evidence}` : ''} (${Math.round(c.score * 100)}%)</span></li>`)}</ul>` : null}
    ${info.ai ? html`<p class="ai-stamp">Local AI (${info.ai.model}): ${info.ai.date ? fmtDate(info.ai.date, { year: true }) : 'no date'}${info.ai.reason ? ` — ${info.ai.reason}` : ''}${info.ai.verified ? '' : ' (not confirmed by the text)'}</p>` : null}
  </div>`;
}

export function dateMenu(anchor, note) {
  const sug = note.date_info?.suggestion;
  const ai = note.date_info?.ai;
  openMenu(anchor, [
    { header: note.linked_date ? `${dateText(note, { weekday: true })}` : 'No date yet' },
    note.date_source ? { header: SOURCE[note.date_source] || note.date_source } : null,
    { sep: true },
    { label: note.linked_date ? 'Change date…' : 'Pick a date…', icon: 'calendar', onSelect: () => openDateDialog(note) },
    !note.linked_date && sug?.date ? { label: `Use ${fmtDate(sug.date)} (suggested)`, icon: 'check', onSelect: () => acceptSuggestion(note.id) } : null,
    !note.linked_date && ai?.date ? { label: `Use ${fmtDate(ai.date)} (local AI)`, icon: 'sparkles', onSelect: () => acceptSuggestion(note.id, 'ai') } : null,
    note.linked_date ? { label: 'Show on calendar', icon: 'calendar-days', onSelect: () => navigate(`#/calendar/${note.linked_date}`) } : null,
    note.date_status !== 'manual' || note.date_source !== 'none' ? { label: 'This has no date', icon: 'calendar-x', onSelect: () => setNoteDate(note.id, { none: true }) } : null,
    ['manual', 'you', 'claude', 'ai', 'none'].includes(note.date_source) || note.date_status === 'manual'
      ? { label: 'Back to automatic', icon: 'rotate-ccw', onSelect: () => setNoteDate(note.id, { clear: true }) } : null,
  ], { align: 'start' });
}

// Wide-screen hint for actions that open files on the Mac itself.
export function useIsLocal() {
  const [local] = useState(() => ['localhost', '127.0.0.1', '[::1]'].includes(location.hostname));
  return local;
}

export function weekKey(s) {
  const d = parseYmd(s);
  const day = (d.getDay() + 6) % 7; // Monday start
  d.setDate(d.getDate() - day);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
export const weekdayShort = (s) => WD_SHORT[parseYmd(s).getDay()];

export function useLibraryStatus() {
  const status = useStore((s) => s.library);
  return status;
}
export { useEffect };
