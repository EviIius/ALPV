// Meeting prep: open follow-ups, decisions, open questions, last time, people and the
// decks to have open, gathered from your own notes for one calendar event.
import { html, useEffect, useState } from '/vendor/preact.js';
import { get, post } from '../lib/api.js';
import { emitChange, setState, toast, toastError, useStore } from '../lib/store.js';
import { navigate } from '../lib/router.js';
import { fmtDate } from '../lib/dates.js';
import { Icon, Markdown, Modal, Spinner } from '../lib/ui.js';
import { KIND } from '../lib/library.js';

export function openPrep(ev) {
  // ev: an occurrence ({ event_id, start, title }) or an event ({ id, start, title })
  setState({ prep: { event_id: ev.event_id || ev.id, start: ev.start, title: ev.title } });
}

export function PrepHost() {
  const prep = useStore((s) => s.prep);
  return prep ? html`<${PrepModal} key=${`${prep.event_id}${prep.start}`} req=${prep} onClose=${() => setState({ prep: null })} />` : null;
}

function when(start, allDay) {
  const d = fmtDate(start.slice(0, 10));
  if (allDay || start.length <= 10) return d;
  const [h, m] = start.slice(11, 16).split(':').map(Number);
  return `${d}, ${(h % 12) || 12}:${String(m).padStart(2, '0')} ${h < 12 ? 'AM' : 'PM'}`;
}

function PrepModal({ req, onClose }) {
  const ai = useStore((s) => s.ai);
  const [p, setP] = useState(null);
  const [brief, setBrief] = useState(null);
  const [busy, setBusy] = useState(false);
  const load = () => get(`/api/events/${req.event_id}/prep${req.start ? `?start=${encodeURIComponent(req.start)}` : ''}`).then(setP).catch((e) => { toastError(e); onClose(); });
  useEffect(() => { load(); }, []);
  const go = (href) => { onClose(); navigate(href); };
  const track = async (a) => {
    try {
      await post('/api/library/followups/track', { note_id: a.note_id, index: a.index });
      toast('Added to Tasks under Follow-ups');
      emitChange({ entity: 'tasks' });
      load();
    } catch (e) { toastError(e); }
  };
  const askAi = async () => {
    setBusy(true);
    try { setBrief((await post('/api/ai/prep-brief', { event_id: req.event_id, start: req.start })).markdown); } catch (e) { toastError(e); }
    setBusy(false);
  };
  const title = p ? p.event.title : req.title || 'Meeting';
  const empty = p && !p.last && !p.actions.length && !p.decisions.length && !p.questions.length && !p.documents.length && !p.notes.length;
  return html`<${Modal} title=${`Prep: ${title}`} onClose=${onClose} wide=${true} cls="prep-modal">
    ${!p ? html`<${Spinner} label="Gathering your notes…" />` : html`<div class="prep">
      <div class="prep-when muted"><${Icon} name="calendar-clock" size="sm" />${when(p.event.start, p.event.all_day)}${p.event.location ? ` · ${p.event.location}` : ''}</div>
      ${p.people.length ? html`<div class="prep-people">${p.people.map((n) => html`<a class="chip" href=${`#/library/timeline?person=${encodeURIComponent(n)}`} onClick=${(e) => { e.preventDefault(); go(`#/library/timeline?person=${encodeURIComponent(n)}`); }}><${Icon} name="user-round" size="sm" />${n}</a>`)}</div>` : null}
      ${empty ? html`<p class="muted">Nothing in your notes connects to this meeting yet. Add names or a topic to the event's title or description, and Daybook will find related notes, follow-ups and decks.</p>` : null}
      ${ai?.ok ? html`<div class="prep-ai">${brief ? html`<${Markdown} text=${brief} />` : html`<button class="btn btn-sm" type="button" disabled=${busy} onClick=${askAi}><${Icon} name="sparkles" />${busy ? 'Writing a brief…' : 'Brief me with local AI'}</button>`}</div>` : null}
      ${p.last ? html`<section><h3>Last time</h3>
        <a class="prep-last" href=${`#/notes/${p.last.id}`} onClick=${(e) => { e.preventDefault(); go(`#/notes/${p.last.id}`); }}>
          <strong>${p.last.title}</strong><span class="faint"> · ${p.last.date ? fmtDate(p.last.date) : ''}</span>
          <span class="sum">${p.last.summary}</span></a></section>` : null}
      ${p.actions.length ? html`<section><h3>Open follow-ups <span class="faint">${p.actions.length}</span></h3>
        <ul class="prep-list">${p.actions.map((a) => html`<li class=${a.attendee ? 'mine' : ''}>
          <span class="t">${a.text}</span>
          <span class="m faint">${a.owner ? html`<strong>${a.owner}</strong> · ` : ''}${a.due ? `due ${fmtDate(a.due)} · ` : ''}<a href=${`#/notes/${a.note_id}`} onClick=${(e) => { e.preventDefault(); go(`#/notes/${a.note_id}`); }}>${a.title}</a></span>
          ${a.tracked ? html`<span class="tag">In Tasks</span>` : html`<button class="btn btn-sm btn-quiet" type="button" onClick=${() => track(a)}><${Icon} name="list-checks" />Track</button>`}
        </li>`)}</ul></section>` : null}
      ${p.decisions.length ? html`<section><h3>Decided so far</h3><ul class="prep-list">${p.decisions.map((d) => html`<li><span class="t">${d.text}</span>
        <span class="m faint">${d.date ? `${fmtDate(d.date)} · ` : ''}<a href=${`#/notes/${d.note_id}`} onClick=${(e) => { e.preventDefault(); go(`#/notes/${d.note_id}`); }}>${d.title}</a></span></li>`)}</ul></section>` : null}
      ${p.questions.length ? html`<section><h3>Still open</h3><ul class="prep-list">${p.questions.map((d) => html`<li><span class="t">${d.text}</span>
        <span class="m faint"><a href=${`#/notes/${d.note_id}`} onClick=${(e) => { e.preventDefault(); go(`#/notes/${d.note_id}`); }}>${d.title}</a></span></li>`)}</ul></section>` : null}
      ${p.documents.length ? html`<section><h3>Have these open</h3><div class="prep-docs">${p.documents.map((d) => html`<a class="prep-doc" href=${`#/read/${d.id}`} onClick=${(e) => { e.preventDefault(); go(`#/read/${d.id}`); }}>
        <${Icon} name=${(KIND[d.kind] || {}).icon || 'file'} /><span>${d.title}</span>${d.date ? html`<span class="faint">${fmtDate(d.date)}</span>` : null}</a>`)}</div></section>` : null}
      ${p.tasks.length ? html`<section><h3>Your open tasks</h3><ul class="prep-list">${p.tasks.map((t) => html`<li><span class="t">${t.title}</span><span class="m faint">${t.due_date ? `due ${fmtDate(t.due_date)}` : 'no date'}</span></li>`)}</ul></section>` : null}
      ${p.notes.length ? html`<section><h3>Related notes</h3><div class="prep-docs">${p.notes.map((n) => html`<a class="prep-doc" href=${`#/notes/${n.id}`} onClick=${(e) => { e.preventDefault(); go(`#/notes/${n.id}`); }}>
        <${Icon} name=${(KIND[n.kind] || {}).icon || 'file-text'} /><span>${n.title}</span>${n.date ? html`<span class="faint">${fmtDate(n.date)}</span>` : null}</a>`)}</div></section>` : null}
    </div>`}
  <//>`;
}
