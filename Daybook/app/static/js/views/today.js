// Today: the date, your schedule, what's due, and your notes.
import { html, useEffect, useState } from '/vendor/preact.js';
import { get, post, put } from '../lib/api.js';
import { toastError, useChanges, useStore } from '../lib/store.js';
import { navigate } from '../lib/router.js';
import {
  MONTHS, WEEKDAYS, dayOfYear, daysInYear, fmtAgo, fmtDate, fmtDayRelative, fmtRange, fmtTime, isoWeek, parseYmd, today,
} from '../lib/dates.js';
import { Empty, Icon, Markdown, Spinner, useTick } from '../lib/ui.js';
import { Capture } from '../components/capture.js';
import { openEventEditor } from './calendar.js';
import { openPrep } from '../components/prep.js';

// A small "Prep" control inside an agenda row (the row itself opens the event).
function PrepPill({ e }) {
  if (e.task_id) return html`<span class="prep-pill task" title="Time set aside for a task"><${Icon} name="square-check" size="sm" />Task</span>`;
  const open = (ev) => { ev.stopPropagation(); ev.preventDefault(); openPrep(e); };
  return html`<span class="prep-pill" role="button" tabindex="0" title="Prep: follow-ups, decisions and decks for this meeting"
    onClick=${open} onKeyDown=${(ev) => { if (ev.key === 'Enter' || ev.key === ' ') open(ev); }}><${Icon} name="clipboard-list" size="sm" />Prep</span>`;
}
import { TaskRow } from './tasks.js';
import { dateText, kindOf } from '../lib/library.js';

export function TodayView() {
  const [data, setData] = useState(null);
  const [brief, setBrief] = useState(null);
  const [briefBusy, setBriefBusy] = useState(false);
  const ai = useStore((s) => s.ai);
  const settings = useStore((s) => s.settings);
  useTick(60000);

  const load = async () => {
    try { setData(await get(`/api/today?day=${today()}`)); } catch (err) { toastError(err); }
  };
  useEffect(() => { load(); }, []);
  useChanges(['notes', 'events', 'tasks'], () => load());

  const d = parseYmd(today());
  const name = settings?.profile?.name;
  const nowHm = (() => { const n = new Date(); return `${String(n.getHours()).padStart(2, '0')}:${String(n.getMinutes()).padStart(2, '0')}`; })();

  const openDaily = async () => {
    try { const n = await get(`/api/daily/${today()}`); navigate(`#/notes/${n.id}`); } catch (err) { toastError(err); }
  };
  const briefing = async () => {
    setBriefBusy(true);
    try { setBrief(await post('/api/ai/briefing', { date: today() })); } catch (err) { toastError(err); }
    setBriefBusy(false);
  };
  const toggleTask = async (t) => {
    try { await put(`/api/tasks/${t.id}`, { done: !t.done }); load(); } catch (err) { toastError(err); }
  };

  const events = data?.events || [];
  const timed = events.filter((e) => !e.all_day);
  const allDay = events.filter((e) => e.all_day);
  let nowShown = false;
  const hour = new Date().getHours();
  const greeting = hour < 5 ? 'Late night' : hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening';

  return html`<div class="view">
    <section class="today-hero" aria-label="Today">
      <div class="today-date">
        <div class="today-weekday">${greeting}${name ? `, ${name}` : ''}. It's ${WEEKDAYS[d.getDay()]}.</div>
        <h1 class="today-day">${MONTHS[d.getMonth()]} ${d.getDate()}</h1>
      </div>
      <div class="today-meta">
        <div class="cell"><span class="label">Sheet</span><span class="value">${dayOfYear(d)} of ${daysInYear(d.getFullYear())}</span></div>
        <div class="cell"><span class="label">Week</span><span class="value">${isoWeek(d)}</span></div>
        <div class="cell"><span class="label">Due today</span><span class=${`value ${data?.tasks?.overdue?.length ? 'red' : ''}`}>${data ? (data.tasks.today.length + data.tasks.overdue.length) : '–'}</span></div>
      </div>
      <div class="today-capture"><${Capture} /></div>
    </section>

    ${!data ? html`<${Spinner} label="Loading your day" />` : html`<div class="today-grid">
      <div>
        <section class="panel">
          <div class="panel-head"><h2>Schedule</h2>
            <div class="row"><button class="btn btn-sm" onClick=${() => openEventEditor({ date: today() })}><${Icon} name="plus" />Event</button>
            <a class="btn btn-ghost btn-sm" href=${`#/calendar/${today()}?view=day`}>Open day</a></div></div>
          ${allDay.map((e) => html`<button class="agenda-row" onClick=${() => openEventEditor({ occurrence: e })}>
            <span class="agenda-time">All day</span><span class="agenda-bar" style=${`background:${e.color}`}></span>
            <span><span class="agenda-title">${e.title}</span>${e.location ? html`<span class="agenda-sub">${e.location}</span>` : null}</span></button>`)}
          ${timed.map((e) => {
            const past = e.end.slice(11, 16) <= nowHm;
            const marker = !nowShown && e.start.slice(11, 16) > nowHm;
            if (marker) nowShown = true;
            return html`${marker ? html`<div class="now-marker">Now, ${fmtTime(nowHm)}</div>` : null}
              <button class=${`agenda-row ${past ? 'past' : ''}`} onClick=${() => openEventEditor({ occurrence: e })}>
                <span class="agenda-time">${fmtTime(e.start.slice(11, 16))}</span>
                <span class="agenda-bar" style=${`background:${e.color}`}></span>
                <span class="grow"><span class="agenda-title">${e.title}</span>
                  <span class="agenda-sub">${fmtRange(e)}${e.location ? `, ${e.location}` : ''}${e.recurring ? ', repeats' : ''}</span></span>
                ${past ? null : html`<${PrepPill} e=${e} />`}
              </button>`;
          })}
          ${!events.length ? html`<${Empty} title="Nothing scheduled today.">
            <span>Type an event above, like "Design review 3pm", or <a href="#/calendar">open the calendar</a>.</span></${Empty}>` : null}
          ${timed.length && !nowShown ? html`<div class="now-marker">Now, ${fmtTime(nowHm)}</div>` : null}
        </section>

        <section class="panel">
          <div class="panel-head"><h2>Coming up</h2><a class="btn btn-ghost btn-sm" href="#/calendar?view=agenda">Agenda</a></div>
          ${data.upcoming.length ? data.upcoming.map((e) => html`<button class="agenda-row" onClick=${() => openEventEditor({ occurrence: e })}>
              <span class="agenda-time">${fmtDayRelative(e.start.slice(0, 10))}</span>
              <span class="agenda-bar" style=${`background:${e.color}`}></span>
              <span class="grow"><span class="agenda-title">${e.title}</span><span class="agenda-sub">${fmtRange(e)}</span></span>${e.all_day ? null : html`<${PrepPill} e=${e} />`}</button>`)
            : html`<p class="muted" style="padding:8px 0">Nothing in the next six days.</p>`}
        </section>
      </div>

      <div>
        <section class="panel">
          <div class="panel-head"><h2>Due</h2><a class="btn btn-ghost btn-sm" href="#/tasks">All tasks</a></div>
          ${[...data.tasks.overdue, ...data.tasks.today].map((t) => html`<${TaskRow} task=${t} onToggle=${() => toggleTask(t)} />`)}
          ${!data.tasks.overdue.length && !data.tasks.today.length ? html`<p class="muted" style="padding:8px 0">Nothing due today.</p>` : null}
        </section>

        ${ai?.ok ? html`<section class="panel">
          <div class="panel-head"><h2>Briefing</h2>
            <button class="btn btn-sm" onClick=${briefing} disabled=${briefBusy}>${briefBusy ? html`<span class="spinner"></span>` : html`<${Icon} name="sparkles" />`}${brief ? 'Refresh' : 'Brief me'}</button></div>
          ${brief ? html`<div class="briefing"><${Markdown} text=${brief.markdown} cls="prose prose-sm" />
            <p class="ai-stamp" style="margin-top:8px">${brief.model}, ${(brief.ms / 1000).toFixed(1)}s, on your hardware</p></div>`
            : html`<p class="muted" style="padding:6px 0">Your local model can summarize today's schedule, what's due, and what you've been writing.</p>`}
        </section>` : null}

        <section class="panel">
          <div class="panel-head"><h2>Notes</h2>
            <button class="btn btn-sm" onClick=${openDaily}><${Icon} name="notebook-pen" />${data.daily_note ? "Today's page" : 'Start today’s page'}</button></div>
          ${data.linked_notes.filter((n) => n.kind !== 'daily').map((n) => html`<a class="agenda-row" href=${`#/notes/${n.id}`}>
            <span class="agenda-time">${n.date_time ? fmtTime(n.date_time) : 'For today'}</span><span class="agenda-bar" style="background:var(--violet)"></span>
            <span><span class="agenda-title"><${Icon} name=${kindOf(n).icon} size="sm" cls="faint" /> ${n.title || 'Untitled'}</span>
              ${n.date_end && n.date_end !== n.linked_date ? html`<span class="agenda-sub">${dateText(n, { time: false })}</span>` : null}</span></a>`)}
          ${(data.mentions || []).map((m) => html`<a class="agenda-row" href=${`#/notes/${m.note_id}`}>
            <span class="agenda-time">${m.kind === 'deadline' ? 'Due today' : 'Mentioned'}</span><span class="agenda-bar" style=${`background:${m.kind === 'deadline' ? 'var(--red)' : 'var(--amber)'}`}></span>
            <span><span class="agenda-title">${m.title}</span><span class="agenda-sub serif">${m.snippet.slice(0, 140)}</span></span></a>`)}
          ${data.needs_date ? html`<a class="agenda-row" href="#/library/review"><span class="agenda-time">Library</span><span class="agenda-bar" style="background:var(--red)"></span>
            <span><span class="agenda-title">${data.needs_date} file${data.needs_date === 1 ? '' : 's'} need${data.needs_date === 1 ? 's' : ''} a date</span><span class="agenda-sub">Daybook wasn't sure which day they belong to.</span></span></a>` : null}
          ${data.recent_notes.map((n) => html`<a class="agenda-row" href=${`#/notes/${n.id}`}>
            <span class="agenda-time">${fmtAgo(n.updated_at)}</span><span class="agenda-bar" style="background:var(--rule-strong)"></span>
            <span><span class="agenda-title">${n.locked ? html`<${Icon} name="lock" size="sm" cls="faint" /> ` : ''}${n.title || 'Untitled'}</span>
              ${n.snippet ? html`<span class="agenda-sub serif">${n.snippet.slice(0, 110)}</span>` : null}</span></a>`)}
          ${!data.recent_notes.length ? html`<${Empty} title="No notes yet."><span>Write one, or <a href="#/import">import a Word or PDF file</a>.</span></${Empty}>` : null}
        </section>

        ${data.look_back?.length ? html`<section class="panel">
          <div class="panel-head"><h2>Look back</h2></div>
          ${data.look_back.map((g) => html`<div class="lookback">
            <a class="lb-head" href=${`#/calendar/${g.date}?view=day`}><span>${g.label}</span><span class="faint">${fmtDate(g.date, { weekday: true })}</span></a>
            ${g.notes.map((n) => html`<a class="agenda-row" href=${`#/notes/${n.id}`}>
              <span class="agenda-time">${n.date_time ? fmtTime(n.date_time) : ''}</span><span class="agenda-bar" style="background:var(--rule-strong)"></span>
              <span><span class="agenda-title"><${Icon} name=${kindOf(n).icon} size="sm" cls="faint" /> ${n.title || 'Untitled'}</span>
                ${n.summary || n.snippet ? html`<span class="agenda-sub serif">${(n.summary || n.snippet).replace(/[#*_>`]/g, '').slice(0, 120)}</span>` : null}</span></a>`)}
            ${g.more ? html`<a class="lb-more" href=${`#/calendar/${g.date}?view=day`}>${g.more} more that day</a>` : null}
          </div>`)}
        </section>` : null}
      </div>
    </div>`}
  </div>`;
}

