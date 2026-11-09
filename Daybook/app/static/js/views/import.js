// Import: turn Word, PDF, PowerPoint, Excel, email, images and more into notes.
import { html, useEffect, useRef, useState } from '/vendor/preact.js';
import { get, post, put, upload } from '../lib/api.js';
import { getState, setState, toast, toastError, useChanges, useStore } from '../lib/store.js';
import { navigate } from '../lib/router.js';
import { fmtAgo } from '../lib/dates.js';
import { Field, Icon, Seg, Spinner, Switch } from '../lib/ui.js';

const STATUS = {
  queued: ['Waiting', 'status-idle'],
  converting: ['Converting on this computer', 'status-busy'],
  ai: ['Local AI is reading it', 'status-busy'],
  done: ['Done', 'status-ok'],
  error: ["Couldn't import", 'status-error'],
};
const ICONS = { docx: 'file-text', doc: 'file-text', pdf: 'file-text', pptx: 'presentation', xlsx: 'file-spreadsheet', csv: 'file-spreadsheet', eml: 'mail', msg: 'mail',
  png: 'file-image', jpg: 'file-image', jpeg: 'file-image', heic: 'file-image', m4a: 'file-audio', mp3: 'file-audio', wav: 'file-audio', zip: 'file-archive', ics: 'calendar' };

export function ImportView() {
  const settings = useStore((s) => s.settings);
  const notebooks = useStore((s) => s.notebooks);
  const ai = useStore((s) => s.ai);
  const [info, setInfo] = useState(null);
  const [jobs, setJobs] = useState(null);
  const [over, setOver] = useState(false);
  const [progress, setProgress] = useState(null);
  const [notebook, setNotebook] = useState('');
  const [aiMode, setAiMode] = useState(settings.ai.smart_import || 'suggest');
  const [keep, setKeep] = useState(settings.import.keep_originals !== false);
  const library = useStore((s) => s.library);
  const libBooks = notebooks.filter((b) => b.lib_dir != null);
  const [dest, setDest] = useState(() => (library?.configured ? (settings.library?.new_notes_dir || 'Notes') : ''));
  const fileInput = useRef(null);

  const load = async () => {
    try { const [i, j] = await Promise.all([get('/api/import/info'), get('/api/import/jobs')]); setInfo(i); setJobs(j); } catch (err) { toastError(err); }
  };
  useEffect(() => { load(); }, []);
  useChanges(['imports'], (evt) => {
    if (!evt.job) return;
    setJobs((list) => {
      const rest = (list || []).filter((j) => j.id !== evt.job.id);
      return [evt.job, ...rest].sort((a, b) => b.created_at - a.created_at);
    });
  });

  const send = async (files) => {
    if (!files.length) return;
    const fd = new FormData();
    for (const f of files) fd.append('files', f, f.name);
    if (dest && library?.configured) fd.append('library_dir', dest || '/');
    if (notebook && !dest) fd.append('notebook_id', notebook);
    fd.append('ai_mode', ai?.ok ? aiMode : 'off');
    fd.append('keep_original', String(keep));
    setProgress(0);
    try {
      await upload('/api/import', fd, setProgress);
      toast(dest && library?.configured ? `Saved to ${library.name}/${dest} and dated` : `${files.length} file${files.length === 1 ? '' : 's'} queued for import`);
      load();
    } catch (err) { toastError(err); }
    setProgress(null);
  };
  const onDrop = (e) => { e.preventDefault(); setOver(false); send([...e.dataTransfer.files]); };
  const clear = async () => { try { await post('/api/import/jobs/clear'); load(); } catch (err) { toastError(err); } };
  const reveal = async () => { try { await post('/api/import/inbox/reveal'); } catch (err) { toastError(err); } };
  const toggleInbox = async (on) => {
    try {
      const s = await put('/api/settings', { import: { ...getState().settings.import, inbox_enabled: on } });
      setState({ settings: s });
      setTimeout(load, 600);
    } catch (err) { toastError(err); }
  };

  const local = getState().auth?.connection === 'loopback';
  const accept = info ? Object.values(info.formats).flat().join(',') : '';

  return html`<div class="view">
    <div class="view-head"><div><h1 class="view-title">Import</h1>
      <p class="view-sub">Documents are converted to notes on this computer. The original file is kept with each note.</p></div></div>

    <section class=${`dropzone ${over ? 'over' : ''}`} onDragOver=${(e) => { e.preventDefault(); setOver(true); }} onDragLeave=${() => setOver(false)} onDrop=${onDrop}>
      <h2>Drop files here to turn them into notes</h2>
      <p>Word, PDF, PowerPoint, Excel, CSV, email, web pages, Markdown, images and audio. Several at once is fine, and so is a ZIP of Markdown files.</p>
      <div class="row row-wrap">
        <button class="btn btn-primary btn-lg" onClick=${() => fileInput.current.click()} disabled=${progress !== null}><${Icon} name="upload" />Choose files</button>
        ${progress !== null ? html`<span class="row"><span class="spinner"></span><span class="muted">Uploading ${Math.round(progress * 100)}%</span></span>` : null}
      </div>
      <input type="file" multiple hidden ref=${fileInput} accept=${accept} onChange=${(e) => { send([...e.currentTarget.files]); e.currentTarget.value = ''; }} />
      <div class="row row-wrap" style="gap:16px;margin-top:6px">
        ${library?.configured ? html`<${Field} label="Save to"><select class="select input-sm" value=${dest} onChange=${(e) => setDest(e.currentTarget.value)}>
          ${libBooks.map((b) => html`<option value=${b.lib_dir || '/'}>${library.name}/${b.lib_dir || ''} (folder)</option>`)}
          <option value="">Daybook only (private copy)</option></select></${Field}>` : null}
        ${!dest || !library?.configured ? html`<${Field} label="Notebook"><select class="select input-sm" value=${notebook} onChange=${(e) => setNotebook(e.currentTarget.value)}>
          <option value="">Default</option>${notebooks.filter((b) => b.lib_dir == null).map((b) => html`<option value=${b.id}>${b.name}</option>`)}</select></${Field}>` : null}
        <${Field} label="Local AI filing">
          ${ai?.ok ? html`<${Seg} value=${aiMode} onChange=${setAiMode} options=${[{ value: 'off', label: 'Off' }, { value: 'suggest', label: 'Suggest' }, { value: 'auto', label: 'Automatic' }]} />`
            : html`<span class="muted" style="font-size:13px">Unavailable. <a href="#/settings/ai">Set up local AI</a> to get titles, summaries and tasks.</span>`}
        </${Field}>
        <${Field} label="Original file"><${Switch} checked=${keep} onChange=${setKeep} label="Keep with the note" /></${Field}>
      </div>
      ${dest && library?.configured ? html`<p class="faint" style="font-size:12px">The file is copied into your ${library.name} folder, read in place and put on the right day. Edit the original there; Daybook keeps up.</p>` : null}
      ${ai?.ok && !(dest && library?.configured) ? html`<p class="faint" style="font-size:12px">${aiMode === 'auto' ? 'Automatic: AI sets the title, tags and summary and creates tasks. Dates are always left for you to confirm.' : aiMode === 'suggest' ? 'Suggest: AI proposes a title, tags, summary, tasks and dates. You choose what to keep.' : 'Off: documents are converted only.'}</p>` : null}
    </section>

    ${info ? html`<div class="inbox-card" style="margin-top:20px">
      <${Icon} name="folder-open" size="lg" />
      <div><strong>Watched folder</strong>
        <div class="muted" style="font-size:13px;margin-top:2px">${settings.import.inbox_enabled
          ? html`Save or drop files into <code>${info.inbox.folder.replace(/^\/Users\/[^/]+/, '~')}</code> on the Mac running Daybook. They become notes automatically and move to its Imported folder.`
          : 'Turn this on to import anything you save into a folder on your Mac.'}</div>
        ${info.inbox.error ? html`<div class="error-text" style="margin-top:4px">${info.inbox.error}</div>` : null}</div>
      <div class="row">${settings.import.inbox_enabled && local && info.legacy_word.textutil ? html`<button class="btn btn-sm" onClick=${reveal}>Show in Finder</button>` : null}
        <${Switch} checked=${settings.import.inbox_enabled} onChange=${toggleInbox} label=${settings.import.inbox_enabled ? 'On' : 'Off'} /></div>
    </div>` : null}

    <section class="panel" style="margin-top:28px">
      <div class="panel-head"><h2>Recent imports</h2>${jobs?.some((j) => j.status === 'done' || j.status === 'error') ? html`<button class="btn btn-ghost btn-sm" onClick=${clear}>Clear finished</button>` : null}</div>
      ${!jobs ? html`<${Spinner} />` : !jobs.length ? html`<p class="muted" style="padding:10px 0">Nothing imported yet.</p>` : jobs.map((j) => {
        const ext = (j.filename.split('.').pop() || '').toLowerCase();
        const [label, cls] = STATUS[j.status] || [j.status, 'status-idle'];
        return html`<div class="job">
          <${Icon} name=${ICONS[ext] || 'file'} />
          <div style="min-width:0"><div class="fname">${j.filename}</div>
            <div class="row" style="gap:10px;margin-top:2px"><span class=${`status ${cls}`}>${label}</span><span class="faint" style="font-size:12px">${j.origin === 'inbox' ? 'From the watched folder, ' : ''}${fmtAgo(j.created_at)}</span></div>
            ${j.message ? html`<div class="msg">${j.message}</div>` : null}</div>
          <div class="row">${j.note_id ? html`<button class="btn btn-sm" onClick=${() => navigate(`#/notes/${j.note_id}`)}>${j.suggestions ? 'Review' : 'Open'}</button>` : null}</div>
        </div>`;
      })}
    </section>

    ${info ? html`<section class="panel" style="margin-top:28px">
      <div class="panel-head"><h2>What can be imported</h2></div>
      <div class="stack" style="padding-top:6px">
        ${Object.entries(info.formats).map(([k, exts]) => html`<div class="row row-wrap" style="align-items:baseline"><strong style="min-width:170px;font-size:13px">${k}</strong>
          <div class="format-list">${exts.map((x) => html`<span class="chip">${x}</span>`)}</div></div>`)}
        <div class="banner" style="margin-top:6px"><${Icon} name="scan-text" /><div>
          <strong>Text in images and scans:</strong> ${info.ocr.apple_vision ? 'Apple Vision (on-device) is ready.' : info.ocr.tesseract ? 'Tesseract is ready.' : info.ocr.local_model ? 'Your local vision model will read images.' : 'Not set up yet. See Settings → Import for a one-line install.'}
          <br /><strong>Audio transcription:</strong> ${info.transcription.engines.length ? `${info.transcription.engines.join(', ')} ready.` : 'Not set up yet (optional). See Settings → Import.'}
          <br /><strong>Older Word, RTF and Pages-exported files:</strong> ${info.legacy_word.textutil ? 'converted with macOS’s built-in tools.' : info.legacy_word.libreoffice ? 'converted with LibreOffice.' : 'save them as .docx or PDF first.'}
        </div></div>
      </div>
    </section>` : null}
  </div>`;
}
