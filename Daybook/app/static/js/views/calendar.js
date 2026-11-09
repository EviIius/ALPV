// Calendar: month, week, day and agenda views, plus the event editor.
import { openPrep } from '../components/prep.js';
import { html, useEffect, useLayoutEffect, useMemo, useRef, useState } from '/vendor/preact.js';
import { del, downloadUrl, get, post, put, qs, upload } from '../lib/api.js';
import { emitChange, getState, setState, toast, toastError, useChanges, useStore } from '../lib/store.js';
import { navigate } from '../lib/router.js';
import {
  MONTHS, MON_SHORT, WD_SHORT, WEEKDAYS, addDays, addMonths, fmtDate, fmtHour, fmtRange, fmtTime, fromMinutes, minutes,
  monthGrid, parseLocal, parseYmd, startOfWeek, today, ymd,
} from '../lib/dates.js';
import { Empty, Field, Icon, Modal, PencilCircle, Seg, Spinner, Switch, chooseDialog, confirmDialog, openMenu, useMedia, useTick } from '../lib/ui.js';
import { dateText, kindOf } from '../lib/library.js';
import { openTaskEditor } from './tasks.js';

const HOUR_PX = 48;
const VIEWS = [{ value: 'month', label: 'Month' }, { value: 'week', label: 'Week' }, { value: 'day', label: 'Day' }, { value: 'agenda', label: 'Agenda' }];

export function openEventEditor(opts) { setState({ eventEditor: opts || {} }); }

function savedView() { try { return localStorage.getItem('daybook_cal_view') || 'month'; } catch { return 'month'; } }

function rangeFor(view, cursor) {
  if (view === 'month') {
    const days = monthGrid(cursor.getFullYear(), cursor.getMonth());
    return [days[0], days[days.length - 1]];
  }
  if (view === 'week') { const s = startOfWeek(cursor); return [s, addDays(s, 6)]; }
  if (view === 'day') return [cursor, cursor];
  return [cursor, addDays(cursor, 30)];
}

function bucketByDay(items, first, last) {
  const map = {};
  for (let d = new Date(first); d <= last; d = addDays(d, 1)) map[ymd(d)] = [];
  for (const ev of items) {
    let s = parseYmd(ev.start.slice(0, 10));
    let e = parseYmd(ev.end.slice(0, 10));
    if (!ev.all_day && ev.end.slice(11, 16) === '00:00' && e > s) e = addDays(e, -1);
    if (s < first) s = new Date(first);
    for (let d = s; d <= e && d <= last; d = addDays(d, 1)) { const k = ymd(d); if (map[k]) map[k].push(ev); }
  }
  for (const k of Object.keys(map)) {
    map[k].sort((a, b) => (!!a.isNote - !!b.isNote) || (b.all_day - a.all_day) || a.start.localeCompare(b.start)
      || (a.date_time || '').localeCompare(b.date_time || '') || a.title.localeCompare(b.title));
  }
  return map;
}

export function CalendarView({ route }) {
  const cursorStr = route.parts[1] && /^\d{4}-\d{2}-\d{2}$/.test(route.parts[1]) ? route.parts[1] : today();
  const cursor = parseYmd(cursorStr);
  const view = route.params.get('view') || savedView();
  const calendars = useStore((s) => s.calendars);
  const [items, setItems] = useState(null);
  const [tasks, setTasks] = useState([]);
  const [libNotes, setLibNotes] = useState({ items: [], mentions: {} });
  const settings = useStore((s) => s.settings);
  const showMentions = settings?.library?.show_mentions !== false;
  const narrow = useMedia('(max-width: 760px)');
  const [first, last] = rangeFor(view, cursor);
  const key = `${ymd(first)}_${ymd(last)}`;

  const load = async () => {
    try {
      const [evs, tk, ln] = await Promise.all([
        get(`/api/events${qs({ start: ymd(first), end: ymd(last) })}`),
        get(`/api/tasks${qs({ view: 'open', due_from: ymd(first), due_to: ymd(last) })}`),
        get(`/api/library/calendar${qs({ start: ymd(first), end: ymd(last) })}`).catch(() => ({ items: [], mentions: {} })),
      ]);
      setItems(evs);
      setTasks(tk.items);
      setLibNotes(ln);
    } catch (err) { toastError(err); }
  };
  useEffect(() => { load(); }, [key]);
  useChanges(['events', 'tasks', 'calendars', 'notes'], () => load(), [key]);

  const go = (date, v = view) => navigate(`#/calendar/${ymd(date)}${v !== savedView() || route.params.get('view') ? `?view=${v}` : ''}`);
  const setView = (v) => { try { localStorage.setItem('daybook_cal_view', v); } catch { /* ignore */ } navigate(`#/calendar/${cursorStr}?view=${v}`); };
  const step = (dir) => {
    if (view === 'month') go(addMonths(cursor, dir));
    else if (view === 'week') go(addDays(cursor, 7 * dir));
    else if (view === 'day') go(addDays(cursor, dir));
    else go(addDays(cursor, 30 * dir));
  };

  const title = view === 'month' ? html`${MONTHS[cursor.getMonth()]} <span class="yr">${cursor.getFullYear()}</span>`
    : view === 'week' ? `${fmtDate(ymd(first))} – ${fmtDate(ymd(last))}`
      : view === 'day' ? `${WEEKDAYS[cursor.getDay()]}, ${MON_SHORT[cursor.getMonth()]} ${cursor.getDate()}`
        : `From ${fmtDate(cursorStr)}`;

  const more = (e) => openMenu(e.currentTarget, [
    { label: 'Import an .ics calendar file', icon: 'upload', onSelect: importIcs },
    { label: 'Export calendar (.ics)', icon: 'download', onSelect: () => downloadUrl('/api/calendar.ics') },
    { sep: true },
    { header: 'Show calendars' },
    ...calendars.map((c) => ({ label: `${c.visible ? 'Hide' : 'Show'} ${c.name}`, icon: c.visible ? 'eye-off' : 'eye', onSelect: () => toggleCal(c) })),
    { sep: true },
    { label: 'Calendar settings', icon: 'settings', onSelect: () => navigate('#/settings/calendar') },
  ], { align: 'end' });

  const allItems = items || [];
  const taskItems = tasks.map((t) => ({ ...t, isTask: true, start: t.due_date, end: t.due_date, all_day: true, color: 'var(--graphite)' }));
  const noteItems = libNotes.items.map((n) => ({ ...n, isNote: true, start: n.linked_date, end: n.date_end || n.linked_date, all_day: true, color: n.color || 'var(--violet)' }));
  const byDay = useMemo(() => bucketByDay([...allItems, ...taskItems, ...noteItems], first, last), [items, tasks, libNotes]);
  const mentions = showMentions ? libNotes.mentions : {};

  return html`<div class=${`view ${view === 'month' || view === 'week' ? 'view-wide' : ''}`}>
    <div class="cal-head">
      <h1 class="cal-title">${title}</h1>
      <div class="row">
        <button class="btn" onClick=${() => go(new Date())}>Today</button>
        <button class="btn btn-icon" onClick=${() => step(-1)} aria-label="Previous"><${Icon} name="chevron-left" /></button>
        <button class="btn btn-icon" onClick=${() => step(1)} aria-label="Next"><${Icon} name="chevron-right" /></button>
      </div>
      <${Seg} value=${view} options=${narrow ? VIEWS.filter((v) => v.value !== 'week') : VIEWS} onChange=${setView} label="Calendar view" />
      <button class="btn btn-primary" onClick=${() => openEventEditor({ date: cursorStr })}><${Icon} name="plus" />Event</button>
      <button class="btn btn-icon" onClick=${more} aria-label="More calendar options"><${Icon} name="ellipsis" /></button>
    </div>
    ${items === null ? html`<${Spinner} label="Loading calendar" />` : null}
    ${items !== null && view === 'month' ? html`<div class="cal-layout">
        <${MonthGrid} cursor=${cursor} selected=${cursorStr} byDay=${byDay} narrow=${narrow} onSelect=${(d) => go(d)} mentions=${mentions} />
        <${DayPanel} date=${cursorStr} items=${byDay[cursorStr] || []} mentions=${mentions[cursorStr]} />
      </div>` : null}
    ${items !== null && (view === 'week' || view === 'day') ? html`<${TimeGrid} days=${view === 'week' ? [...Array(7)].map((_, i) => addDays(first, i)) : [cursor]} byDay=${byDay} onPickDay=${(d) => go(d, 'day')} />` : null}
    ${items !== null && view === 'agenda' ? html`<${Agenda} first=${first} last=${last} byDay=${byDay} />` : null}
    ${items !== null && view === 'day' ? html`<${DayNotes} date=${cursorStr} items=${byDay[cursorStr] || []} mentions=${mentions[cursorStr]} />` : null}
  </div>`;
}

async function toggleCal(c) {
  try { await put(`/api/calendars/${c.id}`, { ...c, visible: !c.visible }); setState({ calendars: await get('/api/calendars') }); emitChange({ entity: 'events' }); }
  catch (err) { toastError(err); }
}

function importIcs() {
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = '.ics,text/calendar';
  input.onchange = async () => {
    const f = input.files[0];
    if (!f) return;
    const fd = new FormData();
    fd.append('file', f);
    try {
      const res = await upload('/api/events/import', fd);
      toast(`Added ${res.created} events${res.updated ? `, updated ${res.updated}` : ''}${res.skipped ? `, skipped ${res.skipped}` : ''}.`);
      emitChange({ entity: 'events' });
    } catch (err) { toastError(err); }
  };
  input.click();
}

function Chip({ ev, compact }) {
  if (ev.isNote) {
    return html`<span class="ev-chip note-chip" style=${`--c:${ev.color}`} title=${`${ev.title}${ev.notebook ? ` — ${ev.notebook}` : ''}`}>
      <${Icon} name=${kindOf(ev).icon} size="sm" /><span class="t">${ev.title}</span></span>`;
  }
  if (ev.isTask) {
    return html`<span class="ev-chip task" title=${ev.title}><span class="box"></span><span class="t">${ev.title}</span></span>`;
  }
  return html`<span class="ev-chip" style=${`--c:${ev.color}`} title=${`${ev.title} (${fmtRange(ev)})`}>
    ${!ev.all_day && !compact ? html`<span class="time">${fmtTime(ev.start.slice(11, 16)).replace(/ (AM|PM)/, (m) => m.trim()[0].toLowerCase())}</span>` : null}
    ${ev.task_id ? html`<${Icon} name="square-check" size="sm" />` : null}<span class="t">${ev.title}</span></span>`;
}

function MonthGrid({ cursor, selected, byDay, narrow, onSelect, mentions = {} }) {
  const days = monthGrid(cursor.getFullYear(), cursor.getMonth());
  const ws = days[0].getDay();
  const t = today();
  const max = 3;
  return html`<div class="month" role="grid" aria-label="Month">
    <div class="month-dow" role="row">${[...Array(7)].map((_, i) => html`<span role="columnheader">${WD_SHORT[(ws + i) % 7]}</span>`)}</div>
    <div class="month-grid">
      ${days.map((d) => {
        const k = ymd(d);
        const list = byDay[k] || [];
        const out = d.getMonth() !== cursor.getMonth();
        const weekend = d.getDay() === 0 || d.getDay() === 6;
        return html`<div role="gridcell" tabindex="0" class=${`month-cell ${out ? 'out' : ''} ${weekend ? 'weekend' : ''} ${k === selected ? 'selected' : ''}`}
            aria-label=${`${WEEKDAYS[d.getDay()]} ${MONTHS[d.getMonth()]} ${d.getDate()}, ${list.length} items`}
            onClick=${() => onSelect(d)} onDblClick=${() => openEventEditor({ date: k })}
            onKeyDown=${(e) => { if (e.key === 'Enter') onSelect(d); }}>
          <span class=${`mday ${k === t ? 'is-today' : ''}`}>${d.getDate()}${k === t ? html`<${PencilCircle} />` : null}</span>
          ${mentions[k] ? html`<span class=${`mention-mark ${mentions[k].deadlines ? 'has-deadline' : ''} ${mentions[k].notes < 2 && !mentions[k].deadlines ? 'single' : ''}`} title=${`Mentioned in ${mentions[k].notes} note${mentions[k].notes === 1 ? '' : 's'}${mentions[k].deadlines ? ' (deadline)' : ''}`}>
            <${Icon} name=${mentions[k].deadlines ? 'flag' : 'milestone'} size="sm" />${mentions[k].notes}</span>` : null}
          ${list.slice(0, max).map((ev) => html`<${Chip} ev=${ev} />`)}
          ${list.length > max ? html`<span class="month-more">+${list.length - max} more</span>` : null}
          ${narrow && list.length ? html`<span class="month-dots">${list.slice(0, 4).map((ev) => html`<span class=${`dot ${ev.isNote ? 'dot-note' : ''}`} style=${`background:${ev.isTask ? 'var(--graphite)' : ev.color}`}></span>`)}</span>` : null}
        </div>`;
      })}
    </div>
  </div>`;
}

function noteRow(ev) {
  const k = kindOf(ev);
  return html`<a class="agenda-row" href=${`#/notes/${ev.id}`} style="grid-template-columns:64px 4px 1fr">
    <span class="agenda-time">${ev.date_time ? fmtTime(ev.date_time) : ev.date_end && ev.date_end !== ev.linked_date ? 'Multi-day' : ''}</span>
    <span class="agenda-bar" style=${`background:${ev.color}`}></span>
    <span><span class="agenda-title"><${Icon} name=${k.icon} size="sm" cls="faint" /> ${ev.title}</span>
      <span class="agenda-sub">${[k.label, ev.notebook, ev.people?.length ? ev.people.slice(0, 3).join(', ') : ''].filter(Boolean).join(' · ')}${ev.date_status === 'review' ? ' · check date' : ''}</span></span></a>`;
}

function Mentioned({ date, count }) {
  const [rows, setRows] = useState(null);
  const [open, setOpen] = useState(false);
  useEffect(() => { setRows(null); setOpen(false); }, [date]);
  if (!count) return null;
  const toggle = async () => {
    setOpen(!open);
    if (!rows) { try { setRows(await get(`/api/library/mentions${qs({ day: date })}`)); } catch (err) { toastError(err); } }
  };
  return html`<div class="mentioned">
    <button class="btn btn-ghost btn-sm" onClick=${toggle} aria-expanded=${String(open)}><${Icon} name=${count.deadlines ? 'flag' : 'milestone'} />
      Mentioned in ${count.notes} note${count.notes === 1 ? '' : 's'}${count.deadlines ? ' (deadline)' : ''}<${Icon} name=${open ? 'chevron-up' : 'chevron-down'} size="sm" /></button>
    ${open ? (!rows ? html`<${Spinner} />` : html`<div class="link-list">${rows.map((m) => html`<a href=${`#/notes/${m.note_id}`}>
      <${Icon} name=${m.kind === 'deadline' ? 'flag' : 'file-text'} /><span class="grow"><strong style="font-weight:600">${m.title}</strong>
      <span class="faint" style="display:block;font-size:11.5px;line-height:1.4">${m.snippet}</span></span></a>`)}</div>`) : null}
  </div>`;
}

function DayNotes({ date, items, mentions }) {
  const notesHere = items.filter((ev) => ev.isNote);
  if (!notesHere.length && !mentions) return null;
  return html`<section class="panel" style="margin-top:16px">
    <div class="panel-head"><h2>Notes and files</h2></div>
    ${notesHere.map(noteRow)}
    ${!notesHere.length ? html`<p class="muted" style="padding:4px 0">Nothing filed on this day.</p>` : null}
    <${Mentioned} date=${date} count=${mentions} />
  </section>`;
}

function DayPanel({ date, items, mentions }) {
  const d = parseYmd(date);
  const evs = items.filter((ev) => !ev.isNote);
  const notesHere = items.filter((ev) => ev.isNote);
  const openDaily = async () => {
    try { const n = await get(`/api/daily/${date}`); navigate(`#/notes/${n.id}`); } catch (err) { toastError(err); }
  };
  return html`<aside class="day-panel">
    <div class="row row-between" style="margin-bottom:10px">
      <div><span class="faint" style="font-size:12px;font-weight:600">${WEEKDAYS[d.getDay()]}</span><h3>${MONTHS[d.getMonth()]} ${d.getDate()}</h3></div>
      <button class="btn btn-sm btn-primary" onClick=${() => openEventEditor({ date })}><${Icon} name="plus" />Add</button>
    </div>
    ${evs.length ? evs.map((ev) => ev.isTask
      ? html`<a class="agenda-row" href="#/tasks" style="grid-template-columns:64px 4px 1fr"><span class="agenda-time">Due</span><span class="agenda-bar" style="background:var(--graphite)"></span><span class="agenda-title">${ev.title}</span></a>`
      : html`<button class="agenda-row" style="grid-template-columns:64px 4px 1fr" onClick=${() => openEventEditor({ occurrence: ev })}>
          <span class="agenda-time">${ev.all_day ? 'All day' : fmtTime(ev.start.slice(11, 16))}</span>
          <span class="agenda-bar" style=${`background:${ev.color}`}></span>
          <span><span class="agenda-title">${ev.title}</span>${ev.location ? html`<span class="agenda-sub">${ev.location}</span>` : null}</span></button>`)
      : html`<p class="muted" style="padding:6px 0 10px">Nothing planned. Double-click a day to add an event.</p>`}
    ${notesHere.length ? html`<div class="section-title" style="margin-top:14px">Notes and files <span class="count">${notesHere.length}</span></div>${notesHere.map(noteRow)}` : null}
    <${Mentioned} date=${date} count=${mentions} />
    <button class="btn btn-ghost btn-sm" style="margin-top:10px" onClick=${openDaily}><${Icon} name="notebook-pen" />Daily note for this day</button>
  </aside>`;
}

// Lay out overlapping timed events side by side.
function layoutDay(events, dayKey) {
  const segs = events.filter((e) => !e.all_day && !e.isTask).map((e) => {
    const sd = e.start.slice(0, 10);
    const ed = e.end.slice(0, 10);
    const s = sd < dayKey ? 0 : minutes(e.start.slice(11, 16));
    let en = ed > dayKey ? 1440 : minutes(e.end.slice(11, 16));
    if (en <= s) en = s + 30;
    return { ev: e, s, e: Math.max(en, s + 20) };
  }).sort((a, b) => a.s - b.s || b.e - a.e);
  const out = [];
  let cluster = [];
  let clusterEnd = -1;
  const flush = () => {
    const cols = [];
    for (const seg of cluster) {
      let i = cols.findIndex((end) => end <= seg.s);
      if (i === -1) { i = cols.length; cols.push(seg.e); } else cols[i] = seg.e;
      seg.col = i;
    }
    for (const seg of cluster) { seg.cols = cols.length; out.push(seg); }
    cluster = [];
  };
  for (const seg of segs) {
    if (seg.s >= clusterEnd && cluster.length) flush();
    cluster.push(seg);
    clusterEnd = Math.max(clusterEnd, seg.e);
  }
  if (cluster.length) flush();
  return out;
}

function TimeGrid({ days, byDay, onPickDay }) {
  const body = useRef(null);
  const settings = useStore((s) => s.settings);
  useTick(60000);
  const t = today();
  const cols = `56px repeat(${days.length}, minmax(0, 1fr))`;
  useLayoutEffect(() => {
    const start = Number(settings?.calendar?.day_start ?? 7);
    const now = new Date();
    const target = days.some((d) => ymd(d) === t) ? Math.max(0, now.getHours() - 2) : start;
    if (body.current) body.current.scrollTop = target * HOUR_PX;
  }, [days.length, ymd(days[0])]);
  const nowMin = new Date().getHours() * 60 + new Date().getMinutes();

  const clickSlot = (e, d) => {
    if (e.target !== e.currentTarget) return;
    const y = e.offsetY;
    const mins = Math.floor((y / HOUR_PX) * 60 / 30) * 30;
    openEventEditor({ date: ymd(d), time: fromMinutes(mins) });
  };

  return html`<div class="tgrid" style=${`--hour:${HOUR_PX}px`}>
    <div class="tgrid-head" style=${`grid-template-columns:${cols}`}>
      <span></span>
      ${days.map((d) => html`<div class=${`dh ${ymd(d) === t ? 'is-today' : ''}`} onClick=${() => onPickDay(d)}>${WD_SHORT[d.getDay()]}<span class="n">${d.getDate()}</span></div>`)}
    </div>
    <div class="tgrid-allday" style=${`grid-template-columns:${cols}`}>
      <span class="lbl">All day</span>
      ${days.map((d) => html`<div class="ad">${(byDay[ymd(d)] || []).filter((e) => e.all_day || e.isTask).map((ev) => html`
        <button style="all:unset;cursor:pointer;display:block;min-width:0" onClick=${() => (ev.isNote ? navigate(`#/notes/${ev.id}`) : ev.isTask ? navigate('#/tasks') : openEventEditor({ occurrence: ev }))}><${Chip} ev=${ev} compact=${true} /></button>`)}</div>`)}
    </div>
    <div class="tgrid-body" ref=${body}>
      <div class="tgrid-cols" style=${`grid-template-columns:${cols}`}>
        <div class="tg-hourcol">${[...Array(24)].map((_, h) => html`<div class="h">${h ? fmtHour(h) : ''}</div>`)}</div>
        ${days.map((d) => {
          const k = ymd(d);
          const segs = layoutDay(byDay[k] || [], k);
          return html`<div class=${`tg-day ${k === t ? 'is-today' : ''}`} style=${`height:${24 * HOUR_PX}px`} onClick=${(e) => clickSlot(e, d)}>
            ${segs.map((s) => html`<button class="tg-event" style=${`--c:${s.ev.color};top:${(s.s / 60) * HOUR_PX}px;height:${Math.max(22, ((s.e - s.s) / 60) * HOUR_PX - 2)}px;left:calc(${(100 / s.cols) * s.col}% + 2px);width:calc(${100 / s.cols}% - 4px);right:auto`}
                onClick=${(e) => { e.stopPropagation(); openEventEditor({ occurrence: s.ev }); }}>
              <span class="t">${s.ev.task_id ? html`<${Icon} name="square-check" size="sm" /> ` : null}${s.ev.title}</span><span class="s">${fmtRange(s.ev)}${s.ev.location ? `, ${s.ev.location}` : ''}</span></button>`)}
            ${k === t ? html`<div class="tg-now" style=${`top:${(nowMin / 60) * HOUR_PX}px`}></div>` : null}
          </div>`;
        })}
      </div>
    </div>
  </div>`;
}

function Agenda({ first, last, byDay }) {
  const t = today();
  const days = [];
  for (let d = new Date(first); d <= last; d = addDays(d, 1)) if ((byDay[ymd(d)] || []).length) days.push(d);
  if (!days.length) return html`<${Empty} title="Nothing in the next 30 days."><span>Add an event with the button above.</span></${Empty}>`;
  return html`<div>${days.map((d) => {
    const k = ymd(d);
    return html`<div class=${`agenda-day ${k === t ? 'is-today' : ''}`}>
      <div class="d"><div class="n">${d.getDate()}</div><div class="w">${WD_SHORT[d.getDay()]}, ${MON_SHORT[d.getMonth()]}</div></div>
      <div>${byDay[k].map((ev) => ev.isNote ? noteRow(ev) : ev.isTask
        ? html`<a class="agenda-row" href="#/tasks"><span class="agenda-time">Due</span><span class="agenda-bar" style="background:var(--graphite)"></span><span class="agenda-title">${ev.title}</span></a>`
        : html`<button class="agenda-row" onClick=${() => openEventEditor({ occurrence: ev })}>
          <span class="agenda-time">${ev.all_day ? 'All day' : fmtTime(ev.start.slice(11, 16))}</span>
          <span class="agenda-bar" style=${`background:${ev.color}`}></span>
          <span><span class="agenda-title">${ev.title}</span><span class="agenda-sub">${fmtRange(ev)}${ev.location ? `, ${ev.location}` : ''}${ev.calendar ? `, ${ev.calendar}` : ''}</span></span></button>`)}</div>
    </div>`;
  })}</div>`;
}

// ---------------------------------------------------------------------------
// Event editor
// ---------------------------------------------------------------------------
const REMINDER_OPTS = [[0, 'At start'], [5, '5 min'], [10, '10 min'], [15, '15 min'], [30, '30 min'], [60, '1 hour'], [1440, '1 day']];
const DURATIONS = [[15, '15m'], [30, '30m'], [45, '45m'], [60, '1h'], [90, '1.5h'], [120, '2h']];
const BYDAY = ['SU', 'MO', 'TU', 'WE', 'TH', 'FR', 'SA'];

function parseRule(rule) {
  const r = { freq: 'none', interval: 1, days: [], end: 'never', until: '', count: 10, custom: false };
  if (!rule) return r;
  const parts = Object.fromEntries(rule.split(';').map((p) => p.split('=')));
  r.freq = (parts.FREQ || 'WEEKLY').toLowerCase();
  r.interval = Number(parts.INTERVAL || 1);
  r.days = parts.BYDAY ? parts.BYDAY.split(',') : [];
  if (parts.UNTIL) { r.end = 'until'; r.until = `${parts.UNTIL.slice(0, 4)}-${parts.UNTIL.slice(4, 6)}-${parts.UNTIL.slice(6, 8)}`; }
  if (parts.COUNT) { r.end = 'count'; r.count = Number(parts.COUNT); }
  const known = new Set(['FREQ', 'INTERVAL', 'BYDAY', 'UNTIL', 'COUNT', 'WKST']);
  r.custom = Object.keys(parts).some((k) => !known.has(k));
  r.raw = rule;
  return r;
}
function buildRule(r) {
  if (r.freq === 'none') return null;
  if (r.custom && r.raw) return r.raw;
  const parts = [`FREQ=${r.freq.toUpperCase()}`];
  if (r.interval > 1) parts.push(`INTERVAL=${r.interval}`);
  if (r.freq === 'weekly' && r.days.length) parts.push(`BYDAY=${r.days.join(',')}`);
  if (r.end === 'until' && r.until) parts.push(`UNTIL=${r.until.replace(/-/g, '')}T235959`);
  if (r.end === 'count') parts.push(`COUNT=${Math.max(1, r.count)}`);
  return parts.join(';');
}

export function EventEditorHost() {
  const opts = useStore((s) => s.eventEditor);
  if (!opts) return null;
  return html`<${EventEditor} opts=${opts} onClose=${() => setState({ eventEditor: null })} />`;
}

function EventEditor({ opts, onClose }) {
  const calendars = useStore((s) => s.calendars);
  const settings = useStore((s) => s.settings);
  const occ = opts.occurrence || null;
  const [master, setMaster] = useState(null);
  const [form, setForm] = useState(null);
  const [rule, setRule] = useState(parseRule(null));
  const [busy, setBusy] = useState(false);
  const isNew = !occ && !opts.event;

  useEffect(() => {
    (async () => {
      if (occ) {
        try {
          const ev = await get(`/api/events/${occ.event_id}`);
          setMaster(ev);
          setForm({
            title: ev.title, description: ev.description, location: ev.location, calendar_id: ev.calendar_id,
            all_day: occ.all_day, startDate: occ.start.slice(0, 10), endDate: occ.end.slice(0, 10),
            startTime: occ.all_day ? '09:00' : occ.start.slice(11, 16), endTime: occ.all_day ? '10:00' : occ.end.slice(11, 16),
            reminders: ev.reminders, note_id: ev.note_id,
          });
          setRule(parseRule(ev.rrule));
        } catch (err) { toastError(err); onClose(); }
        return;
      }
      const date = opts.date || today();
      const now = new Date();
      const start = opts.time || `${String((now.getHours() + 1) % 24).padStart(2, '0')}:00`;
      const dur = Number(settings?.calendar?.default_duration || 60);
      setForm({
        title: opts.title || '', description: '', location: '', calendar_id: calendars[0]?.id || null,
        all_day: !!opts.all_day, startDate: date, endDate: date, startTime: start, endTime: fromMinutes(minutes(start) + dur),
        reminders: [Number(settings?.notifications?.default_reminder ?? 15)], note_id: null,
      });
    })();
  }, []);

  if (!form) return html`<${Modal} title="Event" onClose=${onClose}><${Spinner} /></${Modal}>`;
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));
  const duration = (() => {
    const s = parseLocal(`${form.startDate}T${form.startTime}`);
    const e = parseLocal(`${form.endDate}T${form.endTime}`);
    return Math.round((e - s) / 60000);
  })();
  const setDuration = (m) => {
    const s = parseLocal(`${form.startDate}T${form.startTime}`);
    const e = new Date(s.getTime() + m * 60000);
    set({ endDate: ymd(e), endTime: `${String(e.getHours()).padStart(2, '0')}:${String(e.getMinutes()).padStart(2, '0')}` });
  };
  const onStartTime = (v) => {
    const d = duration > 0 ? duration : 60;
    const s = parseLocal(`${form.startDate}T${v}`);
    const e = new Date(s.getTime() + d * 60000);
    set({ startTime: v, endDate: ymd(e), endTime: `${String(e.getHours()).padStart(2, '0')}:${String(e.getMinutes()).padStart(2, '0')}` });
  };
  const onStartDate = (v) => {
    const shift = (parseYmd(v) - parseYmd(form.startDate)) / 86400000;
    set({ startDate: v, endDate: ymd(addDays(parseYmd(form.endDate), shift)) });
  };

  const payload = () => ({
    title: form.title.trim(), description: form.description, location: form.location, calendar_id: form.calendar_id,
    all_day: form.all_day,
    start: form.all_day ? form.startDate : `${form.startDate}T${form.startTime}`,
    end: form.all_day ? form.endDate : `${form.endDate}T${form.endTime}`,
    reminders: form.reminders, note_id: form.note_id, rrule: buildRule(rule),
  });

  const save = async (e) => {
    e && e.preventDefault();
    if (!form.title.trim()) { toast('Add a title first.', { error: true }); return; }
    setBusy(true);
    try {
      if (isNew) {
        await post('/api/events', payload());
        toast('Event added');
      } else {
        let scope = 'all';
        if (master.rrule) {
          scope = await chooseDialog({ title: 'Change a repeating event', message: 'Which events should change?', choices: [
            { label: 'Only this event', value: 'this' }, { label: 'This and following events', value: 'following' }, { label: 'All events in the series', value: 'all', primary: true }] });
          if (!scope) { setBusy(false); return; }
        }
        const body = payload();
        if (scope === 'all' && master.rrule) {
          // Keep the series' original first date; apply only the time/length change.
          const shiftDays = (parseYmd(form.startDate) - parseYmd(occ.start.slice(0, 10))) / 86400000;
          const baseStart = parseYmd(master.start.slice(0, 10));
          const newStartDate = ymd(addDays(baseStart, shiftDays));
          const spanDays = (parseYmd(form.endDate) - parseYmd(form.startDate)) / 86400000;
          const newEndDate = ymd(addDays(parseYmd(newStartDate), spanDays));
          body.start = form.all_day ? newStartDate : `${newStartDate}T${form.startTime}`;
          body.end = form.all_day ? newEndDate : `${newEndDate}T${form.endTime}`;
        }
        await put(`/api/events/${master.id}${qs({ scope, occurrence: occ.occurrence || undefined })}`, body);
        toast('Event saved');
      }
      emitChange({ entity: 'events' });
      onClose();
    } catch (err) { toastError(err); setBusy(false); }
  };

  const remove = async () => {
    let scope = 'all';
    if (master.rrule) {
      scope = await chooseDialog({ title: 'Delete a repeating event', message: 'Which events should be deleted?', choices: [
        { label: 'Only this event', value: 'this' }, { label: 'This and following events', value: 'following' }, { label: 'All events in the series', value: 'all', danger: true }] });
      if (!scope) return;
    } else if (!(await confirmDialog({ title: 'Delete this event?', message: `"${master.title}" will be removed from your calendar.`, confirm: 'Delete event', danger: true }))) return;
    try {
      await del(`/api/events/${master.id}${qs({ scope, occurrence: occ.occurrence || undefined })}`);
      toast('Event deleted');
      emitChange({ entity: 'events' });
      onClose();
    } catch (err) { toastError(err); }
  };

  const meetingNotes = async () => {
    try {
      if (form.note_id) { onClose(); navigate(`#/notes/${form.note_id}`); return; }
      const note = await post('/api/notes', { template_id: 'meeting', title: form.title || 'Meeting notes', linked_date: form.startDate });
      if (!isNew) await put(`/api/events/${master.id}`, { note_id: note.id });
      onClose();
      navigate(`#/notes/${note.id}`);
    } catch (err) { toastError(err); }
  };

  const toggleReminder = (m) => set({ reminders: form.reminders.includes(m) ? form.reminders.filter((x) => x !== m) : [...form.reminders, m].sort((a, b) => a - b) });
  const startDow = BYDAY[parseYmd(form.startDate).getDay()];

  return html`<${Modal} title=${isNew ? 'New event' : 'Event'} onClose=${onClose} footer=${html`
      ${!isNew ? html`<button class="btn btn-danger left" onClick=${remove}><${Icon} name="trash-2" />Delete</button>` : null}
      ${!isNew && !master?.task_id ? html`<button class="btn" type="button" onClick=${() => { onClose(); openPrep({ event_id: master?.id || occ?.event_id || opts.event?.id, start: occ?.start || master?.start, title: form.title }); }}><${Icon} name="clipboard-list" />Prep</button>` : null}
      <button class="btn" onClick=${meetingNotes}><${Icon} name="notebook-pen" />${form.note_id ? 'Open notes' : 'Meeting notes'}</button>
      <button class="btn" onClick=${onClose}>Cancel</button>
      <button class="btn btn-primary" onClick=${save} disabled=${busy}>${isNew ? 'Add event' : 'Save'}</button>`}>
    <form class="stack" onSubmit=${save}>
      ${master?.task_id ? html`<${TaskLink} id=${master.task_id} onClose=${onClose} />` : null}
      <input class="input" style="font-size:18px;font-weight:600;min-height:44px" placeholder="Title" value=${form.title} onInput=${(e) => set({ title: e.currentTarget.value })} autofocus aria-label="Title" />
      <div class="row row-between"><${Switch} checked=${form.all_day} onChange=${(v) => set({ all_day: v })} label="All day" />
        ${master?.rrule ? html`<span class="chip"><${Icon} name="repeat" size="sm" />Repeating</span>` : null}</div>
      <div class="grid-2">
        <${Field} label="Starts"><div class="row"><input class="input" type="date" value=${form.startDate} onInput=${(e) => onStartDate(e.currentTarget.value)} required />
          ${!form.all_day ? html`<input class="input" type="time" style="max-width:130px" value=${form.startTime} onInput=${(e) => onStartTime(e.currentTarget.value)} />` : null}</div></${Field}>
        <${Field} label="Ends"><div class="row"><input class="input" type="date" value=${form.endDate} min=${form.startDate} onInput=${(e) => set({ endDate: e.currentTarget.value })} />
          ${!form.all_day ? html`<input class="input" type="time" style="max-width:130px" value=${form.endTime} onInput=${(e) => set({ endTime: e.currentTarget.value })} />` : null}</div></${Field}>
      </div>
      ${!form.all_day ? html`<div class="dur-chips" role="group" aria-label="Length">${DURATIONS.map(([m, l]) => html`
        <button type="button" class=${`chip chip-btn ${duration === m ? 'chip-on' : ''}`} onClick=${() => setDuration(m)}>${l}</button>`)}</div>` : null}

      <${Field} label="Repeat">
        <select class="select" value=${rule.custom ? 'custom' : rule.freq === 'weekly' && rule.days.join(',') === 'MO,TU,WE,TH,FR' ? 'weekdays' : rule.freq === 'weekly' && rule.interval === 2 ? 'biweekly' : rule.freq}
          onChange=${(e) => {
            const v = e.currentTarget.value;
            if (v === 'weekdays') setRule({ ...rule, custom: false, freq: 'weekly', interval: 1, days: ['MO', 'TU', 'WE', 'TH', 'FR'] });
            else if (v === 'biweekly') setRule({ ...rule, custom: false, freq: 'weekly', interval: 2, days: [startDow] });
            else if (v === 'weekly') setRule({ ...rule, custom: false, freq: 'weekly', interval: 1, days: [startDow] });
            else if (v === 'custom') setRule({ ...rule, custom: false });
            else setRule({ ...rule, custom: false, freq: v, interval: 1, days: [] });
          }}>
          <option value="none">Doesn't repeat</option><option value="daily">Every day</option><option value="weekdays">Every weekday</option>
          <option value="weekly">Every week</option><option value="biweekly">Every 2 weeks</option><option value="monthly">Every month</option>
          <option value="yearly">Every year</option>${rule.custom ? html`<option value="custom">Custom rule (kept as is)</option>` : null}
        </select>
      </${Field}>
      ${rule.freq === 'weekly' && !rule.custom ? html`<div class="weekday-picks" role="group" aria-label="Repeat on">${BYDAY.map((d, i) => html`
        <button type="button" aria-pressed=${String(rule.days.includes(d))} onClick=${() => setRule({ ...rule, days: rule.days.includes(d) ? rule.days.filter((x) => x !== d) : [...rule.days, d].sort((a, b) => BYDAY.indexOf(a) - BYDAY.indexOf(b)) })}>${WD_SHORT[i].slice(0, 2)}</button>`)}</div>` : null}
      ${rule.freq !== 'none' && !rule.custom ? html`<div class="row row-wrap">
        <span class="label">Ends</span>
        <${Seg} value=${rule.end} onChange=${(v) => setRule({ ...rule, end: v })} options=${[{ value: 'never', label: 'Never' }, { value: 'until', label: 'On a date' }, { value: 'count', label: 'After' }]} />
        ${rule.end === 'until' ? html`<input class="input input-sm" type="date" style="max-width:170px" value=${rule.until} onInput=${(e) => setRule({ ...rule, until: e.currentTarget.value })} />` : null}
        ${rule.end === 'count' ? html`<input class="input input-sm" type="number" min="1" max="999" style="max-width:90px" value=${rule.count} onInput=${(e) => setRule({ ...rule, count: Number(e.currentTarget.value) })} /><span class="muted">times</span>` : null}
      </div>` : null}

      <${Field} label="Calendar"><div class="cal-pick">${calendars.map((c) => html`
        <button type="button" class=${`chip chip-btn ${form.calendar_id === c.id ? 'chip-on' : ''}`} onClick=${() => set({ calendar_id: c.id })}>
          <span class="swatch" style=${`background:${c.color}`}></span>${c.name}</button>`)}</div></${Field}>
      <${Field} label="Location or link"><input class="input" value=${form.location} placeholder="Room, address or meeting link" onInput=${(e) => set({ location: e.currentTarget.value })} /></${Field}>
      <${Field} label="Reminders" hint=${form.all_day ? 'For all-day events, reminders count back from 9:00 AM.' : ''}><div class="dur-chips">${REMINDER_OPTS.map(([m, l]) => html`
        <button type="button" class=${`chip chip-btn ${form.reminders.includes(m) ? 'chip-on' : ''}`} onClick=${() => toggleReminder(m)}>${l}</button>`)}</div></${Field}>
      <${Field} label="Notes"><textarea class="textarea" value=${form.description} placeholder="Agenda, dial-in details…" onInput=${(e) => set({ description: e.currentTarget.value })}></textarea></${Field}>
    </form>
  </${Modal}>`;
}

// An event that sets time aside for a task: open it, or tick it off from here.
function TaskLink({ id, onClose }) {
  const [task, setTask] = useState(null);
  useEffect(() => { get(`/api/tasks/${id}`).then(setTask).catch(() => setTask(null)); }, [id]);
  if (!task) return null;
  const done = async () => {
    try { await put(`/api/tasks/${id}`, { done: !task.done }); setTask({ ...task, done: !task.done }); emitChange({ entity: 'tasks' }); toast(task.done ? 'Task reopened' : `Done: ${task.title}`); }
    catch (err) { toastError(err); }
  };
  return html`<div class="task-link"><${Icon} name="square-check" />
    <span class="grow">Time set aside for <button type="button" class="linkish" onClick=${() => { onClose(); openTaskEditor(task); }}>${task.title}</button>${task.done ? ' (done)' : ''}</span>
    <button type="button" class="btn btn-sm" onClick=${done}>${task.done ? 'Reopen' : 'Mark done'}</button></div>`;
}
