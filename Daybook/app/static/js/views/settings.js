// Settings: privacy, local AI, import, notifications, calendar, appearance, data.
import { html, useEffect, useState } from '/vendor/preact.js';
import { del, downloadPost, downloadUrl, get, post, put, upload } from '../lib/api.js';
import { emitChange, getState, setState, toast, toastError, useStore } from '../lib/store.js';
import { navigate } from '../lib/router.js';
import { fmtAgo, fmtBytes } from '../lib/dates.js';
import { Field, Icon, Seg, Spinner, Switch, confirmDialog, promptDialog } from '../lib/ui.js';

const SECTIONS = [
  ['privacy', 'Privacy & security', 'shield-check'],
  ['ai', 'Local AI', 'cpu'],
  ['library', 'Library folder', 'folder-open'],
  ['import', 'Import', 'file-up'],
  ['notifications', 'Reminders', 'bell'],
  ['calendar', 'Calendar', 'calendar-days'],
  ['appearance', 'Appearance', 'palette'],
  ['data', 'Backups & data', 'database'],
  ['about', 'About', 'info'],
];

async function saveSettings(patch, quiet = false) {
  try {
    const s = await put('/api/settings', patch);
    setState({ settings: s });
    if (!quiet) toast('Saved');
    return s;
  } catch (err) { toastError(err); return null; }
}

export function SettingsView({ route }) {
  const section = route.parts[1] || 'privacy';
  const settings = useStore((s) => s.settings);
  useEffect(() => {
    // On a phone the sections are a sideways strip: keep the current one in view.
    const nav = document.querySelector('.settings-nav');
    const btn = nav?.querySelector('[aria-current="true"]');
    if (!nav || !btn || nav.scrollWidth <= nav.clientWidth) return;
    const nr = nav.getBoundingClientRect();
    const br = btn.getBoundingClientRect();
    if (br.left < nr.left || br.right > nr.right) nav.scrollLeft += br.left - nr.left - 12;
  }, [section, !!settings]);
  if (!settings) return html`<div class="view"><${Spinner} /></div>`;
  const Section = { privacy: Privacy, ai: AiSettings, library: LibrarySettings, import: ImportSettings, notifications: Notifications, calendar: CalendarSettings, appearance: Appearance, data: Data, about: About }[section] || Privacy;
  return html`<div class="view">
    <div class="view-head"><h1 class="view-title">Settings</h1></div>
    <div class="settings-layout">
      <nav class="settings-nav" aria-label="Settings sections">${SECTIONS.map(([k, l, i]) => html`
        <button aria-current=${String(k === section)} onClick=${() => navigate(`#/settings/${k}`)}><${Icon} name=${i} />${l}</button>`)}</nav>
      <div><${Section} settings=${settings} /></div>
    </div>
  </div>`;
}

function Row({ title, sub, children, col = false }) {
  return html`<div class=${`set-row ${col ? 'col' : ''}`}><div class="txt"><strong>${title}</strong>${sub ? html`<small>${sub}</small>` : null}</div>${children}</div>`;
}

// ---------------------------------------------------------------------------
function Privacy() {
  const [audit, setAudit] = useState(null);
  const [sessions, setSessions] = useState(null);
  const load = () => {
    get('/api/privacy').then(setAudit).catch(toastError);
    get('/api/auth/sessions').then(setSessions).catch(toastError);
  };
  useEffect(load, []);
  const changePass = async () => {
    const current = await promptDialog({ title: 'Change sign-in passphrase', label: 'Current passphrase', type: 'password', confirm: 'Next' });
    if (!current) return;
    const next = await promptDialog({ title: 'Change sign-in passphrase', label: 'New passphrase', type: 'password', hint: 'At least 8 characters.', confirm: 'Next' });
    if (!next) return;
    const again = await promptDialog({ title: 'Change sign-in passphrase', label: 'Type the new passphrase again', type: 'password', confirm: 'Change passphrase' });
    if (next !== again) { toast("The new passphrases don't match.", { error: true }); return; }
    try { const r = await post('/api/auth/passphrase', { current, new: next }); toast(`Passphrase changed. ${r.signed_out} other device(s) signed out.`); load(); } catch (err) { toastError(err); }
  };
  const changeVault = async () => {
    const old = await promptDialog({ title: 'Change vault passphrase', label: 'Current vault passphrase', type: 'password', confirm: 'Next' });
    if (!old) return;
    const next = await promptDialog({ title: 'Change vault passphrase', label: 'New vault passphrase', type: 'password', confirm: 'Re-encrypt locked notes' });
    if (!next) return;
    try { const r = await post('/api/vault/change', { old, new: next }); setState({ vault: next }); toast(`Re-encrypted ${r.reencrypted} locked notes.`); } catch (err) { toastError(err); }
  };
  const revokeOthers = async () => {
    try { const r = await post('/api/auth/sessions/revoke-others'); toast(`Signed out ${r.ended} other device(s).`); load(); } catch (err) { toastError(err); }
  };
  const signOut = async () => { await post('/api/auth/logout'); location.reload(); };
  const ok = audit?.checks.filter((c) => c.status === 'ok').length;
  return html`<div class="set-section">
    <h2>Privacy check-up</h2>
    <p>${audit ? `${ok} of ${audit.checks.length} checks look good.` : 'Checking…'} Daybook has no accounts, analytics or cloud services. Here's exactly where your data lives and travels.</p>
    <div class="set-group">${!audit ? html`<div class="set-row"><${Spinner} /></div>` : audit.checks.map((c) => html`<div class="check-row">
      <${Icon} name=${c.status === 'ok' ? 'circle-check' : c.status === 'error' ? 'circle-alert' : c.status === 'warn' ? 'triangle-alert' : 'info'} cls=${`check-ico-${c.status}`} />
      <div><strong>${c.title}</strong><p>${c.detail}</p></div></div>`)}</div>
    ${audit ? html`<p class="faint" style="font-size:12px">Data folder: <code>${audit.data_dir}</code> (${fmtBytes(audit.bytes)})</p>` : null}

    <h2 style="margin-top:12px">Sign-in</h2>
    <div class="set-group">
      <${Row} title="Passphrase" sub="Needed on every new device. Changing it signs out your other devices."><button class="btn" onClick=${changePass}>Change</button></${Row}>
      <${Row} title="Vault passphrase" sub="Encrypts locked notes. Separate from your sign-in."><button class="btn" onClick=${changeVault}>Change</button></${Row}>
      <${Row} title="Sign out of this device" sub="You'll need your passphrase to get back in."><button class="btn" onClick=${signOut}>Sign out</button></${Row}>
    </div>
    <h2 style="margin-top:12px">Signed-in devices</h2>
    <div class="set-group">
      ${!sessions ? html`<div class="set-row"><${Spinner} /></div>` : sessions.map((s) => html`<${Row} title=${describeAgent(s.user_agent)} sub=${`${s.current ? 'This device. ' : ''}Last active ${fmtAgo(s.last_seen)}${s.ip ? `, from ${s.ip}` : ''}`}>
        ${s.current ? html`<span class="status status-ok">Current</span>` : null}</${Row}>`)}
      ${sessions && sessions.length > 1 ? html`<${Row} title="Sign out everywhere else"><button class="btn btn-danger" onClick=${revokeOthers}>Sign out others</button></${Row}>` : null}
    </div>
  </div>`;
}
function describeAgent(ua = '') {
  const os = /iPhone/.test(ua) ? 'iPhone' : /iPad/.test(ua) ? 'iPad' : /Android/.test(ua) ? 'Android' : /Mac OS X/.test(ua) ? 'Mac' : /Windows/.test(ua) ? 'Windows' : /Linux/.test(ua) ? 'Linux' : 'Device';
  const br = /Edg\//.test(ua) ? 'Edge' : /Chrome\//.test(ua) ? 'Chrome' : /Firefox\//.test(ua) ? 'Firefox' : /Safari\//.test(ua) ? 'Safari' : 'browser';
  return `${br} on ${os}`;
}

// ---------------------------------------------------------------------------
function AiSettings({ settings }) {
  const ai = useStore((s) => s.ai);
  const [form, setForm] = useState(settings.ai);
  const [test, setTest] = useState(null);
  const [testing, setTesting] = useState(false);
  const set = (p) => setForm((f) => ({ ...f, ...p }));
  const models = (test?.models || ai?.models || []);
  const chats = models.filter((m) => m.kind !== 'embedding');
  const embeds = models.filter((m) => m.kind === 'embedding');
  const runTest = async () => {
    setTesting(true);
    try { setTest(await post('/api/ai/test', { provider: form.provider, base_url: form.base_url })); } catch (err) { toastError(err); }
    setTesting(false);
  };
  const save = async () => {
    const s = await saveSettings({ ai: form });
    if (s) { const { refreshAi } = await import('../main.js'); refreshAi(); }
  };
  const reindex = async () => { try { await post('/api/ai/reindex'); toast('Rebuilding the index in the background'); } catch (err) { toastError(err); } };
  const setProvider = (p) => set({ provider: p, base_url: p === 'ollama' ? 'http://127.0.0.1:11434' : 'http://127.0.0.1:1234/v1' });
  const sel = (key, list, emptyLabel) => html`<select class="select" value=${form[key] || ''} onChange=${(e) => set({ [key]: e.currentTarget.value })}>
    <option value="">${emptyLabel}</option>${list.map((m) => html`<option value=${m.name}>${m.name}${m.parameters ? ` (${m.parameters})` : ''}${m.size ? `, ${fmtBytes(m.size)}` : ''}</option>`)}
    ${form[key] && !list.some((m) => m.name === form[key]) ? html`<option value=${form[key]}>${form[key]} (not found)</option>` : null}</select>`;
  const status = test || ai;
  return html`<div class="set-section">
    <h2>Local AI</h2>
    <p>Daybook can use AI models that run on your own computer (or another machine on your network or tailnet). It refuses to connect to cloud AI services, and there's no setting that changes that. Locked notes are never sent, even locally.</p>
    <div class="set-group">
      <${Row} title="Use local AI" sub="Summaries, action items, smart import, Ask your notes."><${Switch} checked=${form.enabled} onChange=${(v) => set({ enabled: v })} /></${Row}>
      <${Row} title="AI app" col=${true}>
        <${Seg} value=${form.provider} onChange=${setProvider} options=${[{ value: 'ollama', label: 'Ollama' }, { value: 'openai', label: 'LM Studio / other' }]} />
        <div class="row"><input class="input input-mono" value=${form.base_url} onInput=${(e) => set({ base_url: e.currentTarget.value })} aria-label="AI server address" />
          <button class="btn" onClick=${runTest} disabled=${testing}>${testing ? html`<span class="spinner"></span>` : null}Test</button></div>
        ${status ? html`<div class=${`banner ${status.ok ? '' : 'banner-warn'}`}><${Icon} name=${status.ok ? 'circle-check' : 'circle-alert'} />
          <div>${status.ok ? html`Connected${status.version ? ` to Ollama ${status.version}` : ''} on ${status.local?.where || 'your network'}. ${status.models.length} model(s) installed.` : (status.error || status.local?.reason)}</div></div>` : null}
      </${Row}>
      <${Row} title="Writing model" sub="For summaries, rewrites, answers. Larger is better quality, slower." col=${true}>${sel('smart_model', chats, 'Pick automatically (largest installed)')}</${Row}>
      <${Row} title="Quick model" sub="For titles, tags and parsing quick-adds. Small and fast." col=${true}>${sel('fast_model', chats, 'Pick automatically (smallest installed)')}</${Row}>
      <${Row} title="Embedding model" sub="Finds notes by meaning for Ask and Related notes." col=${true}>${sel('embed_model', embeds.length ? embeds : models, embeds.length ? 'Pick automatically' : 'None installed')}</${Row}>
      <${Row} title="Vision model (optional)" sub="Reads text in images and scans when on-device text recognition isn't available." col=${true}>${sel('vision_model', chats, 'None')}</${Row}>
      <${Row} title="Filing imported documents" sub="Suggest proposes a title, tags, summary, tasks and dates for you to accept. Automatic applies them (dates still need your OK).">
        <${Seg} value=${form.smart_import} onChange=${(v) => set({ smart_import: v })} options=${[{ value: 'off', label: 'Off' }, { value: 'suggest', label: 'Suggest' }, { value: 'auto', label: 'Automatic' }]} /></${Row}>
      <${Row} title="Index notes for Ask" sub=${ai?.index ? `${ai.index.indexed} of ${ai.index.total} notes indexed${ai.index.model ? ` with ${ai.index.model}` : ''}.` : ''}>
        <div class="row"><${Switch} checked=${form.auto_index} onChange=${(v) => set({ auto_index: v })} /><button class="btn btn-sm" onClick=${reindex}>Rebuild</button></div></${Row}>
      <${Row} title="Let models think first" sub="Reasoning models (like Qwen 3 or DeepSeek R1) give better answers but slower. Off is faster."><${Switch} checked=${form.think} onChange=${(v) => set({ think: v })} /></${Row}>
      <${Row} title="Context size" sub="How much text the model reads at once (tokens). 8192 suits most Macs; raise it for long documents if you have memory to spare.">
        <select class="select" style="width:auto" value=${String(form.num_ctx)} onChange=${(e) => set({ num_ctx: Number(e.currentTarget.value) })}>
          ${[4096, 8192, 16384, 32768].map((n) => html`<option value=${n}>${n.toLocaleString()}</option>`)}</select></${Row}>
    </div>
    <div class="row"><button class="btn btn-primary" onClick=${save}>Save AI settings</button></div>
    <div class="set-group"><div class="set-row col"><div class="txt"><strong>Getting models</strong>
      <small class="model-hint">With <a href="https://ollama.com" target="_blank" rel="noopener noreferrer">Ollama</a> installed, run these in Terminal once. Pick sizes your Mac's memory can hold; the first answer after a restart is slower while the model loads.</small>
      <code class="codeblock">ollama pull qwen3:14b          # writing model (try qwen3:30b-a3b or gemma3:27b with 48 GB+)
ollama pull qwen3:4b           # quick model
ollama pull nomic-embed-text   # embedding model for Ask your notes</code>
      <small class="model-hint" style="margin-top:6px">Using LM Studio instead? Start its local server (Developer tab), choose "LM Studio / other" above, and keep the address as <code>http://127.0.0.1:1234/v1</code>.</small></div></div></div>
  </div>`;
}


// ---------------------------------------------------------------------------
function LibrarySettings({ settings }) {
  const status = useStore((s) => s.library);
  const notebooks = useStore((s) => s.notebooks);
  const [f, setF] = useState(settings.library);
  const set = (p) => setF((x) => ({ ...x, ...p }));
  const save = async () => {
    const s = await saveSettings({ library: f });
    if (s) setTimeout(async () => { try { setState({ library: await get('/api/library/status'), notebooks: await get('/api/notebooks') }); } catch { /* ignore */ } }, 1500);
  };
  const folders = notebooks.filter((b) => b.lib_dir != null && b.lib_dir !== '');
  return html`<div class="set-section">
    <h2>Library folder</h2>
    <p>Daybook reads a folder of your own files in place: Word documents, decks, PDFs, transcripts, HTML mockups and Markdown notes.
      Each file becomes a searchable note on the right day of the calendar. Nothing is copied out of the folder or uploaded anywhere.</p>
    ${status?.configured ? html`<div class="banner" style="margin-bottom:14px"><${Icon} name="folder-open" /><div><strong>${status.name}</strong> · <span class="mono">${status.root}</span><br />
      ${status.files} files · ${status.dated} on the calendar · ${status.review} need a date${status.env ? ' · set by DAYBOOK_LIBRARY' : ''}</div></div>`
      : html`<div class="banner banner-warn" style="margin-bottom:14px"><${Icon} name="folder-plus" /><div>No folder connected yet.</div></div>`}
    <div class="set-group">
      <${Row} title="Read a library folder"><${Switch} checked=${f.enabled} onChange=${(v) => set({ enabled: v })} /></${Row}>
      <${Row} title="Folder" sub=${status?.auto_root ? `Leave empty to use ${status.auto_root}, the folder Daybook lives in.` : 'The full path, e.g. ~/Documents/GitHub/ALPV'} col=${true}>
        <input class="input input-mono" value=${f.path} placeholder=${status?.auto_root || '~/Documents/GitHub/ALPV'} onInput=${(e) => set({ path: e.currentTarget.value })} /></${Row}>
      <${Row} title="New notes go to" sub="Notes in a library folder are saved there as Markdown files. Private notes stay in Daybook's own database." col=${true}>
        <div class="row row-wrap">
          <select class="select" style="width:auto" value=${f.new_notes} onChange=${(e) => set({ new_notes: e.currentTarget.value })}>
            <option value="library">The library</option><option value="private">Daybook only (private)</option></select>
          ${f.new_notes === 'library' ? html`<span class="muted">folder</span><input class="input" style="width:160px" list="lib-folders" value=${f.new_notes_dir} onInput=${(e) => set({ new_notes_dir: e.currentTarget.value })} />
            <datalist id="lib-folders">${folders.map((b) => html`<option value=${b.lib_dir} />`)}</datalist>` : null}
        </div></${Row}>
      <${Row} title="Put new notes on the day you create them" sub="Unless the title or text names another day."><${Switch} checked=${f.date_new_notes} onChange=${(v) => set({ date_new_notes: v })} /></${Row}>
      <${Row} title="Local AI for dates Daybook isn't sure about" sub="Suggest: the AI proposes a day and you confirm. Automatic: picks it reads in the text are saved to dates.json. Locked notes are never sent." col=${true}>
        <${Seg} value=${f.ai_dates} onChange=${(v) => set({ ai_dates: v })} options=${[{ value: 'off', label: 'Off' }, { value: 'suggest', label: 'Suggest' }, { value: 'auto', label: 'Automatic' }]} /></${Row}>
      <${Row} title="Show dates your notes mention on the calendar" sub="A small marker on days that notes refer to, like a cutover date or a deadline."><${Switch} checked=${f.show_mentions} onChange=${(v) => set({ show_mentions: v })} /></${Row}>
    </div>
    <div class="row"><button class="btn btn-primary" onClick=${save}>Save library settings</button>
      ${status?.configured ? html`<a class="btn" href="#/library/status">Folder & git status</a>` : null}</div>
    <div class="set-group" style="margin-top:18px">
      <${Row} title="How dating works" col=${true}><p class="muted" style="font-size:13px;line-height:1.6">Daybook looks for the date in the file name, a "Meeting date" line, the top of the document,
        the document's properties, when the file was added to git, and when it last changed. Clues that agree make it more certain. Anything it isn't sure about waits in
        <a href="#/library/review">Needs a date</a>. Your choices (and your local AI's, or Claude's) are saved in <span class="mono">.daybook/dates.json</span> inside the folder.</p></${Row}>
    </div>
  </div>`;
}

// ---------------------------------------------------------------------------
function ImportSettings({ settings }) {
  const notebooks = useStore((s) => s.notebooks);
  const [f, setF] = useState(settings.import);
  const [info, setInfo] = useState(null);
  useEffect(() => { get('/api/import/info').then(setInfo).catch(() => {}); }, []);
  const set = (p) => setF((x) => ({ ...x, ...p }));
  return html`<div class="set-section">
    <h2>Import</h2>
    <p>Every conversion runs on the computer hosting Daybook. Nothing is uploaded.</p>
    <div class="set-group">
      <${Row} title="Watched folder" sub="Files saved here become notes, then move into its Imported folder."><${Switch} checked=${f.inbox_enabled} onChange=${(v) => set({ inbox_enabled: v })} /></${Row}>
      <${Row} title="Folder location" col=${true}><input class="input input-mono" value=${f.inbox_dir} onInput=${(e) => set({ inbox_dir: e.currentTarget.value })} />
        ${info?.inbox?.error ? html`<span class="error-text">${info.inbox.error}</span>` : null}</${Row}>
      <${Row} title="Keep original files" sub="Attach the original document to each note so nothing is lost in conversion."><${Switch} checked=${f.keep_originals} onChange=${(v) => set({ keep_originals: v })} /></${Row}>
      <${Row} title="Default notebook" col=${true}><select class="select" value=${f.default_notebook || ''} onChange=${(e) => set({ default_notebook: e.currentTarget.value })}>
        <option value="">No notebook</option>${notebooks.map((b) => html`<option value=${b.id}>${b.name}</option>`)}</select></${Row}>
      <${Row} title="Spreadsheet rows to include" sub="Larger sheets are trimmed in the note; the full file stays attached.">
        <input class="input" type="number" min="20" max="5000" style="width:110px" value=${f.max_table_rows} onInput=${(e) => set({ max_table_rows: Number(e.currentTarget.value) })} /></${Row}>
      <${Row} title="Text recognition (images and scans)" sub=${info ? `Apple Vision: ${info.ocr.apple_vision ? 'ready' : 'not installed'}. Tesseract: ${info.ocr.tesseract ? 'ready' : 'not installed'}. Local vision model: ${info.ocr.local_model ? 'ready' : 'not set'}.` : ''} col=${true}>
        <select class="select" value=${f.ocr} onChange=${(e) => set({ ocr: e.currentTarget.value })}>
          <option value="auto">Automatic (best available)</option><option value="vision">Apple Vision</option><option value="tesseract">Tesseract</option><option value="ai">Local vision model</option><option value="off">Off</option></select>
        <small class="model-hint">Recommended on a Mac: <code>.venv/bin/pip install pyobjc-framework-Vision</code> (Apple's on-device engine, very accurate).</small></${Row}>
      <${Row} title="Audio transcription" sub=${info ? (info.transcription.engines.length ? `Ready: ${info.transcription.engines.join(', ')}.` : 'Not installed.') : ''} col=${true}>
        <div class="row"><select class="select" value=${f.transcribe} onChange=${(e) => set({ transcribe: e.currentTarget.value })}>
          <option value="auto">Automatic</option><option value="mlx-whisper">mlx-whisper</option><option value="faster-whisper">faster-whisper</option><option value="off">Off</option></select>
          <select class="select" style="width:auto" value=${f.whisper_model} onChange=${(e) => set({ whisper_model: e.currentTarget.value })}>
            ${['tiny', 'base', 'small', 'medium', 'large'].map((m) => html`<option value=${m}>${m}</option>`)}</select></div>
        <small class="model-hint">On Apple Silicon: <code>.venv/bin/pip install mlx-whisper</code>. The model downloads once; audio never leaves your Mac.</small></${Row}>
    </div>
    <div class="row"><button class="btn btn-primary" onClick=${() => saveSettings({ import: f })}>Save import settings</button></div>
  </div>`;
}

// ---------------------------------------------------------------------------
function Notifications({ settings }) {
  const [f, setF] = useState(settings.notifications);
  const set = (p) => setF((x) => ({ ...x, ...p }));
  const [perm, setPerm] = useState(typeof Notification !== 'undefined' ? Notification.permission : 'unsupported');
  const test = async (channel) => {
    await saveSettings({ notifications: f }, true);
    try { const r = await post('/api/notifications/test', { channel }); toast(r.message); } catch (err) { toastError(err); }
  };
  const askPermission = async () => { if (typeof Notification !== 'undefined') setPerm(await Notification.requestPermission()); };
  return html`<div class="set-section">
    <h2>Reminders</h2>
    <p>Event and task reminders are sent by the Daybook server, so they arrive even when no browser is open.</p>
    <div class="set-group">
      <${Row} title="Default reminder for new events">
        <select class="select" style="width:auto" value=${String(f.default_reminder)} onChange=${(e) => set({ default_reminder: Number(e.currentTarget.value) })}>
          ${[[0, 'At start'], [5, '5 minutes before'], [10, '10 minutes before'], [15, '15 minutes before'], [30, '30 minutes before'], [60, '1 hour before']].map(([v, l]) => html`<option value=${v}>${l}</option>`)}</select></${Row}>
      <${Row} title="In Daybook" sub="Shown in every open Daybook window and tab."><div class="row"><${Switch} checked=${f.inapp} onChange=${(v) => set({ inapp: v })} /><button class="btn btn-sm" onClick=${() => test('inapp')}>Test</button></div></${Row}>
      <${Row} title="Browser notifications on this device" sub=${perm === 'granted' ? 'Allowed. You will see reminders even when Daybook is in the background.' : perm === 'denied' ? 'Blocked in your browser settings.' : perm === 'unsupported' ? 'Needs HTTPS on phones (see README: Tailscale HTTPS).' : 'Allow this browser to show reminder pop-ups.'}>
        ${perm === 'default' ? html`<button class="btn btn-sm" onClick=${askPermission}>Allow</button>` : null}</${Row}>
      <${Row} title="Mac Notification Center" sub="On the Mac running Daybook."><div class="row"><${Switch} checked=${f.macos} onChange=${(v) => set({ macos: v })} /><button class="btn btn-sm" onClick=${() => test('macos')}>Test</button></div></${Row}>
    </div>
    <h2 style="margin-top:12px">Phone push (optional)</h2>
    <div class="banner banner-warn"><${Icon} name="triangle-alert" /><div>These services are outside your devices. With <strong>Hide details</strong> on, they only receive "An event is coming up at 3:00 PM", never titles, notes or locations.</div></div>
    <div class="set-group">
      <${Row} title="Hide details in outside services"><${Switch} checked=${f.redact_external} onChange=${(v) => set({ redact_external: v })} /></${Row}>
      <${Row} title="ntfy" sub="Self-host ntfy on your tailnet for fully private phone push, or use ntfy.sh." col=${true}>
        <${Switch} checked=${f.ntfy.enabled} onChange=${(v) => set({ ntfy: { ...f.ntfy, enabled: v } })} label="Send to ntfy" />
        <div class="grid-2"><input class="input" placeholder="Server, e.g. http://100.x.y.z:8080" value=${f.ntfy.url} onInput=${(e) => set({ ntfy: { ...f.ntfy, url: e.currentTarget.value } })} />
          <input class="input" placeholder="Topic" value=${f.ntfy.topic} onInput=${(e) => set({ ntfy: { ...f.ntfy, topic: e.currentTarget.value } })} /></div>
        <div class="row"><input class="input" type="password" placeholder="Access token (optional)" value=${f.ntfy.token} onInput=${(e) => set({ ntfy: { ...f.ntfy, token: e.currentTarget.value } })} />
          <button class="btn" onClick=${() => test('ntfy')}>Test</button></div></${Row}>
      <${Row} title="Discord" sub="Posts reminders to a channel through a webhook." col=${true}>
        <${Switch} checked=${f.discord.enabled} onChange=${(v) => set({ discord: { ...f.discord, enabled: v } })} label="Send to Discord" />
        <div class="row"><input class="input input-mono" placeholder="https://discord.com/api/webhooks/…" value=${f.discord.webhook} onInput=${(e) => set({ discord: { ...f.discord, webhook: e.currentTarget.value } })} />
          <button class="btn" onClick=${() => test('discord')}>Test</button></div></${Row}>
    </div>
    <div class="row"><button class="btn btn-primary" onClick=${() => saveSettings({ notifications: f })}>Save reminder settings</button></div>
  </div>`;
}

// ---------------------------------------------------------------------------
function CalendarSettings({ settings }) {
  const calendars = useStore((s) => s.calendars);
  const [f, setF] = useState({ timezone: settings.timezone, ...settings.calendar, ...settings.appearance });
  const set = (p) => setF((x) => ({ ...x, ...p }));
  const zones = typeof Intl.supportedValuesOf === 'function' ? Intl.supportedValuesOf('timeZone') : [settings.timezone];
  const save = () => saveSettings({
    timezone: f.timezone,
    calendar: { default_duration: Number(f.default_duration), day_start: Number(f.day_start), day_end: Number(f.day_end) },
    appearance: { ...settings.appearance, week_start: Number(f.week_start), time_format: f.time_format },
  });
  const reloadCals = async () => setState({ calendars: await get('/api/calendars') });
  const updateCal = async (c, patch) => { try { await put(`/api/calendars/${c.id}`, { ...c, ...patch }); reloadCals(); emitChange({ entity: 'events' }); } catch (err) { toastError(err); } };
  const addCal = async () => {
    const name = await promptDialog({ title: 'New calendar', label: 'Name', placeholder: 'e.g. Family', confirm: 'Add calendar' });
    if (!name) return;
    try { await post('/api/calendars', { name, color: '#56665C' }); reloadCals(); } catch (err) { toastError(err); }
  };
  const removeCal = async (c) => {
    if (!(await confirmDialog({ title: `Delete “${c.name}”?`, message: 'Its events are kept without a calendar.', confirm: 'Delete calendar', danger: true }))) return;
    try { await del(`/api/calendars/${c.id}`); reloadCals(); emitChange({ entity: 'events' }); } catch (err) { toastError(err); }
  };
  const importIcs = () => {
    const input = document.createElement('input');
    input.type = 'file'; input.accept = '.ics,text/calendar';
    input.onchange = async () => {
      const fd = new FormData(); fd.append('file', input.files[0]);
      try { const r = await upload('/api/events/import', fd); toast(`Added ${r.created} events, updated ${r.updated}.`); emitChange({ entity: 'events' }); } catch (err) { toastError(err); }
    };
    input.click();
  };
  return html`<div class="set-section">
    <h2>Calendar</h2>
    <div class="set-group">
      <${Row} title="Time zone" sub="Reminders fire on this clock." col=${true}><select class="select" value=${f.timezone} onChange=${(e) => set({ timezone: e.currentTarget.value })}>
        ${zones.map((z) => html`<option value=${z}>${z}</option>`)}</select></${Row}>
      <${Row} title="Week starts on"><${Seg} value=${Number(f.week_start)} onChange=${(v) => set({ week_start: v })} options=${[{ value: 0, label: 'Sunday' }, { value: 1, label: 'Monday' }]} /></${Row}>
      <${Row} title="Time format"><${Seg} value=${f.time_format} onChange=${(v) => set({ time_format: v })} options=${[{ value: '12h', label: '1:30 PM' }, { value: '24h', label: '13:30' }]} /></${Row}>
      <${Row} title="Default event length"><select class="select" style="width:auto" value=${String(f.default_duration)} onChange=${(e) => set({ default_duration: Number(e.currentTarget.value) })}>
        ${[15, 30, 45, 60, 90, 120].map((m) => html`<option value=${m}>${m < 60 ? `${m} minutes` : `${m / 60} hour${m > 60 ? 's' : ''}`}</option>`)}</select></${Row}>
      <${Row} title="Day view starts at"><select class="select" style="width:auto" value=${String(f.day_start)} onChange=${(e) => set({ day_start: Number(e.currentTarget.value) })}>
        ${[...Array(13)].map((_, h) => html`<option value=${h}>${h}:00</option>`)}</select></${Row}>
    </div>
    <div class="row"><button class="btn btn-primary" onClick=${save}>Save calendar settings</button></div>
    <h2 style="margin-top:12px">Calendars</h2>
    <div class="set-group">${calendars.map((c) => html`<div class="cal-row">
      <input type="color" value=${c.color} onChange=${(e) => updateCal(c, { color: e.currentTarget.value.toUpperCase() })} aria-label=${`Color for ${c.name}`} />
      <input class="input input-sm grow" value=${c.name} onChange=${(e) => updateCal(c, { name: e.currentTarget.value })} aria-label="Calendar name" />
      <${Switch} checked=${c.visible} onChange=${(v) => updateCal(c, { visible: v })} label="Shown" />
      <button class="btn btn-ghost btn-icon btn-sm" onClick=${() => removeCal(c)} aria-label=${`Delete ${c.name}`}><${Icon} name="trash-2" /></button></div>`)}
      <div class="cal-row"><button class="btn btn-sm" onClick=${addCal}><${Icon} name="plus" />Add calendar</button></div></div>
    <div class="row"><button class="btn" onClick=${importIcs}><${Icon} name="upload" />Import .ics</button><button class="btn" onClick=${() => downloadUrl('/api/calendar.ics')}><${Icon} name="download" />Export .ics</button></div>
    <${PhoneFeed} />
  </div>`;
}

// Private .ics feed the phone's own Calendar app subscribes to (over Tailscale or Wi-Fi).
function PhoneFeed() {
  const [f, setF] = useState(null);
  useEffect(() => { get('/api/feed').then(setF).catch(toastError); }, []);
  if (!f) return null;
  const change = async (patch) => { try { setF(await post('/api/feed', patch)); } catch (err) { toastError(err); } };
  const reset = async () => {
    if (!(await confirmDialog({ title: 'Make a new feed link?', message: 'Calendars subscribed to the old link stop updating. Subscribe again with the new one.', confirm: 'New link' }))) return;
    try { setF(await post('/api/feed/reset')); toast('New feed link made.'); } catch (err) { toastError(err); }
  };
  const url = f.path ? `${location.origin}${f.path}` : '';
  const webcal = url.replace(/^https?:/, 'webcal:');
  const copy = () => navigator.clipboard?.writeText(url).then(() => toast('Feed link copied')).catch(() => toast(url));
  return html`<h2 style="margin-top:12px">Phone calendar feed</h2>
    <div class="set-group">
      <${Row} title="Show Daybook in your phone's Calendar" sub="A private subscription link. It only works on this Mac, your tailnet or your home Wi-Fi, never the open internet.">
        <${Switch} checked=${f.enabled} onChange=${(v) => change({ enabled: v })} label="Calendar feed" /></${Row}>
      ${f.enabled ? html`
        <${Row} title="What it shows"><${Seg} value=${f.detail} onChange=${(v) => change({ detail: v })}
          options=${[{ value: 'full', label: 'Everything' }, { value: 'titles', label: 'Titles only' }, { value: 'busy', label: 'Busy only' }]} /></${Row}>
        <${Row} title="Include tasks with due dates" sub="As ☐ items on their day."><${Switch} checked=${f.include_tasks} onChange=${(v) => change({ include_tasks: v })} label="Include tasks" /></${Row}>
        <div class="set-row col">
          <div class="txt"><strong>Subscribe on your iPhone</strong>
            <small>Open this page on the phone and tap Subscribe, or copy the link into Settings → Apps → Calendar → Calendar Accounts → Add Account → Other → Add Subscribed Calendar. Add it on the phone itself, not through iCloud, so Apple's servers never fetch it.</small></div>
          <div class="row" style="flex-wrap:wrap;gap:8px">
            <a class="btn btn-sm btn-primary" href=${webcal}><${Icon} name="calendar-plus" />Subscribe</a>
            <button class="btn btn-sm" type="button" onClick=${copy}><${Icon} name="copy" />Copy link</button>
            <button class="btn btn-sm btn-ghost" type="button" onClick=${reset}><${Icon} name="refresh-cw" />New link</button>
          </div>
          <code class="mono feed-url">${url.replace(/\/feed\/[^/]+\.ics$/, '/feed/••••••.ics')}</code>
        </div>` : null}
    </div>`;
}

// ---------------------------------------------------------------------------
function Appearance({ settings }) {
  const [name, setName] = useState(settings.profile?.name || '');
  const setTheme = async (theme) => {
    const { applyTheme } = await import('../main.js');
    applyTheme(theme);
    saveSettings({ appearance: { ...settings.appearance, theme } }, true);
  };
  return html`<div class="set-section">
    <h2>Appearance</h2>
    <div class="set-group">
      <${Row} title="Theme" sub="Dark uses the same pad, by lamplight."><${Seg} value=${settings.appearance.theme} onChange=${setTheme}
        options=${[{ value: 'system', label: 'Match system' }, { value: 'light', label: 'Light' }, { value: 'dark', label: 'Dark' }]} /></${Row}>
      <${Row} title="Your name" sub="Used in the greeting on Today. Stays on this computer."><div class="row">
        <input class="input" style="width:180px" value=${name} onInput=${(e) => setName(e.currentTarget.value)} />
        <button class="btn" onClick=${() => saveSettings({ profile: { name } })}>Save</button></div></${Row}>
    </div>
  </div>`;
}

// ---------------------------------------------------------------------------
function Data({ settings }) {
  const [snaps, setSnaps] = useState(null);
  const [busy, setBusy] = useState(false);
  const load = () => get('/api/backup/snapshots').then(setSnaps).catch(toastError);
  useEffect(load, []);
  const snapshot = async () => { setBusy(true); try { await post('/api/backup/snapshot'); toast('Backup saved'); load(); } catch (err) { toastError(err); } setBusy(false); };
  const encrypted = async () => {
    const pass = await promptDialog({ title: 'Encrypted export', label: 'Passphrase for this file', type: 'password', hint: 'Anyone with the file and this passphrase can read it. Without it, it can\'t be opened.', confirm: 'Next' });
    if (!pass) return;
    const again = await promptDialog({ title: 'Encrypted export', label: 'Type it again', type: 'password', confirm: 'Download' });
    if (pass !== again) { toast("The passphrases don't match.", { error: true }); return; }
    try { await downloadPost('/api/export/encrypted', { passphrase: pass }, `daybook-export-${new Date().toISOString().slice(0, 10)}.daybook`); } catch (err) { toastError(err); }
  };
  const restore = () => {
    const input = document.createElement('input');
    input.type = 'file'; input.accept = '.zip,.daybook,.json';
    input.onchange = async () => {
      const file = input.files[0];
      if (!file) return;
      const fd = new FormData();
      fd.append('file', file);
      if (file.name.endsWith('.daybook')) {
        const pass = await promptDialog({ title: 'Encrypted backup', label: 'Passphrase', type: 'password', confirm: 'Restore' });
        if (!pass) return;
        fd.append('passphrase', pass);
      }
      try {
        const r = await upload('/api/restore', fd);
        toast(`Restored ${r.notes ?? 0} notes, ${r.events ?? 0} events${r.tasks !== undefined ? `, ${r.tasks} tasks` : ''}. ${r.skipped ? `${r.skipped} already existed.` : ''}`);
        for (const e of ['notes', 'events', 'tasks', 'notebooks', 'calendars']) emitChange({ entity: e });
      } catch (err) { toastError(err); }
    };
    input.click();
  };
  return html`<div class="set-section">
    <h2>Backups & data</h2>
    <p>Your notes are plain Markdown underneath, so an export opens in any editor (or as an Obsidian vault). Nothing here touches the cloud.</p>
    <div class="set-group">
      <${Row} title="Export everything" sub="A ZIP with every note as a Markdown file, attachments, calendar.ics and a full backup file."><button class="btn" onClick=${() => downloadUrl('/api/export/zip')}><${Icon} name="download" />Download ZIP</button></${Row}>
      <${Row} title="Encrypted export" sub="The same export, locked with a passphrase (AES-256). Safe to keep on a USB drive or cloud storage."><button class="btn" onClick=${encrypted}><${Icon} name="lock" />Download</button></${Row}>
      <${Row} title="Restore" sub="From a Daybook export (.zip or .daybook) or a backup .json from the previous version of this app. Existing items are kept."><button class="btn" onClick=${restore}><${Icon} name="upload" />Choose file</button></${Row}>
    </div>
    <h2 style="margin-top:12px">Automatic backups</h2>
    <div class="set-group">
      <${Row} title="Daily snapshot" sub="A copy of the database is saved once a day in the data folder's backups."><${Switch} checked=${settings.backups.auto} onChange=${(v) => saveSettings({ backups: { ...settings.backups, auto: v } })} /></${Row}>
      <${Row} title="Snapshots to keep"><select class="select" style="width:auto" value=${String(settings.backups.keep)} onChange=${(e) => saveSettings({ backups: { ...settings.backups, keep: Number(e.currentTarget.value) } })}>
        ${[7, 14, 30, 60].map((n) => html`<option value=${n}>${n}</option>`)}</select></${Row}>
      <${Row} title="Back up now" sub=${snaps?.length ? `Latest: ${fmtAgo(snaps[0].created_at)} (${fmtBytes(snaps[0].size)}). ${snaps.length} kept.` : 'No snapshots yet.'}><button class="btn" disabled=${busy} onClick=${snapshot}>Back up now</button></${Row}>
    </div>
  </div>`;
}

// ---------------------------------------------------------------------------
function About() {
  const [sys, setSys] = useState(null);
  useEffect(() => { get('/api/system').then(setSys).catch(toastError); }, []);
  return html`<div class="set-section">
    <h2>About Daybook</h2>
    <p>A private, self-hosted workspace for notes, calendar, tasks and documents. Everything runs on your own hardware.</p>
    <div class="set-group">${!sys ? html`<div class="set-row"><${Spinner} /></div>` : html`
      <${Row} title="Version">${sys.version}</${Row}>
      <${Row} title="Python">${sys.python}</${Row}>
      <${Row} title="System"><span class="faint" style="font-size:12px;text-align:right">${sys.platform}</span></${Row}>
      <${Row} title="Database">${fmtBytes(sys.db_bytes)}${sys.fts ? ', full-text search on' : ''}</${Row}>
      <${Row} title="Running for">${Math.round(sys.uptime_s / 3600)} h</${Row}>`}</div>
    <p class="faint" style="font-size:12px">Open-source components: Preact, htm, TipTap and ProseMirror, marked, DOMPurify, Archivo and Literata (SIL OFL), Lucide icons, FastAPI. See app/static/vendor/THIRD_PARTY_NOTICES.txt.</p>
  </div>`;
}
