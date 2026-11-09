// Ask your notes: questions answered by your local model, with citations.
import { html, useEffect, useRef, useState } from '/vendor/preact.js';
import { post, streamNdjson } from '../lib/api.js';
import { toast, toastError, useStore } from '../lib/store.js';
import { navigate } from '../lib/router.js';
import { renderMarkdown } from '/vendor/md.js';
import { Icon, Seg, Spinner } from '../lib/ui.js';

const EXAMPLES = [
  'What did we decide about SAS storage costs?',
  'Summarize my notes from last week',
  'Which action items are still open from meetings?',
  'What do I know about DataHub domain assignment?',
];

function answerHtml(text, sources) {
  // Turn [1] citations into links to the source notes.
  const html_ = renderMarkdown(text || '');
  return html_.replace(/\[(\d{1,2})\]/g, (m, n) => {
    const s = sources.find((x) => x.n === Number(n));
    return s ? `<a class="cite" href="#/notes/${s.note_id}" title="${s.title.replace(/"/g, '&quot;')}">${n}</a>` : m;
  });
}

export function AskView() {
  const ai = useStore((s) => s.ai);
  const notebooks = useStore((s) => s.notebooks);
  const [mode, setMode] = useState('ask');
  const [q, setQ] = useState('');
  const [scope, setScope] = useState('');
  const [thread, setThread] = useState([]);
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState(null);
  const [useNotes, setUseNotes] = useState(true);
  const box = useRef(null);
  const ctrl = useRef(null);

  useEffect(() => () => ctrl.current && ctrl.current.abort(), []);
  const autosize = () => { const t = box.current; if (t) { t.style.height = 'auto'; t.style.height = `${Math.min(160, t.scrollHeight)}px`; } };

  const ask = async (question) => {
    question = (question || q).trim();
    if (!question || busy) return;
    setQ('');
    setTimeout(autosize, 0);
    setBusy(true);
    const entry = { q: question, text: '', sources: [], done: false };
    setThread((t) => [...t, entry]);
    const history = thread.flatMap((t) => [{ role: 'user', content: t.q }, { role: 'assistant', content: t.text }]);
    ctrl.current = new AbortController();
    try {
      await streamNdjson('/api/ai/ask', { question, history, scope: scope ? { notebook_id: scope } : null }, (evt) => {
        if (evt.type === 'sources') entry.sources = evt.sources;
        if (evt.type === 'delta') entry.text += evt.text;
        if (evt.type === 'done') { entry.done = true; entry.stats = evt; }
        if (evt.type === 'error') { entry.done = true; entry.error = evt.message; }
        setThread((t) => [...t.slice(0, -1), { ...entry }]);
      }, ctrl.current.signal);
    } catch (err) {
      if (err.name !== 'AbortError') { entry.error = err.message; entry.done = true; setThread((t) => [...t.slice(0, -1), { ...entry }]); }
    }
    setBusy(false);
  };

  const saveAsNote = async (entry) => {
    const refs = entry.sources.map((s) => `- [${s.n}] [${s.title}](#/notes/${s.note_id})`).join('\n');
    try {
      const n = await post('/api/notes', { title: entry.q, body: `${entry.text}\n\n## Sources\n\n${refs}\n`, tags: ['answer'] });
      toast('Saved as a note', { action: { label: 'Open', run: () => navigate(`#/notes/${n.id}`) } });
    } catch (err) { toastError(err); }
  };

  const makeDraft = async (e) => {
    e && e.preventDefault();
    if (!q.trim() || busy) return;
    setBusy(true);
    try { setDraft({ prompt: q, ...(await post('/api/ai/draft', { prompt: q, use_notes: useNotes })) }); } catch (err) { toastError(err); }
    setBusy(false);
  };
  const keepDraft = async () => {
    try { const n = await post('/api/notes', { title: draft.title || draft.prompt.slice(0, 80), body: draft.markdown, kind: 'ai' }); navigate(`#/notes/${n.id}`); }
    catch (err) { toastError(err); }
  };

  if (!ai) return html`<div class="view"><${Spinner} /></div>`;
  if (!ai.enabled || !ai.ok) {
    return html`<div class="view ask"><div class="view-head"><div><h1 class="view-title">Ask your notes</h1>
      <p class="view-sub">Answers come from a model running on your own computer, using only your notes.</p></div></div>
      <div class=${`banner ${ai.enabled ? "banner-warn" : ""}`}><${Icon} name=${ai.enabled ? 'cloud-off' : 'brain'} /><div>
        ${ai.enabled ? html`<strong>Daybook can't reach your local AI app.</strong> ${ai.error || ''}` : html`<strong>Local AI is turned off.</strong>`}
        <p style="margin-top:6px">Set it up in a few minutes: install <strong>Ollama</strong> or <strong>LM Studio</strong> on your Mac, download a model, then pick it in settings.</p>
        <a class="btn btn-sm" style="margin-top:10px" href="#/settings/ai">Open Local AI settings</a></div></div></div>`;
  }

  return html`<div class="view ask">
    <div class="view-head"><div><h1 class="view-title">${mode === 'ask' ? 'Ask your notes' : 'Draft a note'}</h1>
      <p class="view-sub">${mode === 'ask' ? `Answered by ${ai.picked?.smart} on your hardware, using ${ai.picked?.embed ? 'meaning and keyword' : 'keyword'} search across your notes.` : 'Describe what you need. Your local model drafts it; you edit and keep it.'}</p></div>
      <${Seg} value=${mode} onChange=${setMode} options=${[{ value: 'ask', label: 'Ask' }, { value: 'draft', label: 'Draft' }]} />
    </div>

    ${mode === 'ask' ? html`
      ${!thread.length ? html`<div class="stack" style="margin-bottom:24px"><span class="label">Try asking</span>
        <div class="row row-wrap">${EXAMPLES.map((ex) => html`<button class="chip chip-btn" onClick=${() => ask(ex)}>${ex}</button>`)}</div></div>` : null}
      <div class="ask-thread">${thread.map((t) => html`<article>
        <h2 class="ask-q">${t.q}</h2>
        ${t.error ? html`<div class="banner banner-error" style="margin-top:10px"><${Icon} name="circle-alert" /><div>${t.error}</div></div>` : null}
        <div class=${`ask-a ${t.done ? '' : 'typing'}`} style="margin-top:10px" dangerouslySetInnerHTML=${{ __html: answerHtml(t.text || (t.done ? '' : 'Reading your notes…'), t.sources) }}></div>
        ${t.done && t.stats ? html`<div class="row row-wrap" style="margin-top:10px">
          <span class="ai-stamp">${t.stats.model}, ${(t.stats.ms / 1000).toFixed(1)}s, ${t.stats.retrieval} search</span>
          <button class="btn btn-ghost btn-sm" onClick=${() => saveAsNote(t)}><${Icon} name="file-plus" />Save as note</button>
          <button class="btn btn-ghost btn-sm" onClick=${() => navigator.clipboard?.writeText(t.text).then(() => toast('Copied'))}><${Icon} name="copy" />Copy</button></div>` : null}
        ${t.sources.length ? html`<details style="margin-top:8px"><summary class="label" style="cursor:pointer">Sources (${t.sources.length})</summary>
          <div class="sources">${t.sources.map((s) => html`<a class="source" href=${`#/notes/${s.note_id}`}><span class="cite">${s.n}</span>
            <span><span class="st">${s.title}</span><span class="sx">${s.text}</span></span></a>`)}</div></details>` : null}
      </article>`)}</div>
      <div class="ask-box">
        <form class="capture" onSubmit=${(e) => { e.preventDefault(); ask(); }}>
          <${Icon} name="message-square-text" cls="faint" />
          <textarea ref=${box} rows="1" value=${q} placeholder="Ask a question about your notes" aria-label="Question"
            onInput=${(e) => { setQ(e.currentTarget.value); autosize(); }}
            onKeyDown=${(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(); } }}></textarea>
          <select class="select input-sm" style="width:auto;max-width:150px" value=${scope} onChange=${(e) => setScope(e.currentTarget.value)} aria-label="Search in">
            <option value="">All notes</option>${notebooks.map((b) => html`<option value=${b.id}>${b.name}</option>`)}</select>
          ${busy ? html`<button type="button" class="btn btn-sm" onClick=${() => ctrl.current && ctrl.current.abort()}>Stop</button>` : html`<button class="btn btn-primary btn-sm" disabled=${!q.trim()}>Ask</button>`}
        </form>
        ${thread.length ? html`<button class="btn btn-ghost btn-sm" style="margin-top:6px" onClick=${() => setThread([])}>Start over</button>` : null}
        <p class="faint" style="font-size:12px;margin-top:6px">Conversations aren't stored. Locked notes are never searched.</p>
      </div>` : html`
      <form class="stack" onSubmit=${makeDraft}>
        <textarea class="textarea" style="min-height:110px" value=${q} onInput=${(e) => setQ(e.currentTarget.value)} placeholder="e.g. An agenda for the Q4 storage cost review with Kelly, including decisions needed and owners"></textarea>
        <div class="row row-between row-wrap"><label class="row"><input type="checkbox" class="check" checked=${useNotes} onChange=${(e) => setUseNotes(e.currentTarget.checked)} /><span>Use relevant notes as background</span></label>
          <button class="btn btn-primary" disabled=${busy || !q.trim()}>${busy ? html`<span class="spinner"></span>` : html`<${Icon} name="sparkles" />`}Draft it</button></div>
      </form>
      ${draft ? html`<div class="ai-result" style="margin-top:20px">
        <div class="ai-result-head"><strong style="color:var(--ink)">${draft.title || 'Draft'}</strong><span class="ai-stamp">${draft.model}, ${(draft.ms / 1000).toFixed(1)}s</span></div>
        <div class="ai-result-body" style="max-height:none"><div class="prose prose-sm" dangerouslySetInnerHTML=${{ __html: renderMarkdown(draft.markdown) }}></div></div>
        <div class="ai-result-foot"><button class="btn btn-primary" onClick=${keepDraft}>Keep as a note</button><button class="btn" onClick=${makeDraft}>Try again</button><button class="btn btn-ghost" onClick=${() => setDraft(null)}>Discard</button></div>
      </div>` : null}`}
  </div>`;
}
