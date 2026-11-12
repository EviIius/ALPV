// Keeping the server and this device on the same version.
//
// Daybook's files can be updated while it runs. Pages come straight from disk, so a phone shows the
// new screens at once, but the server keeps running its old code until it restarts, and the new
// screens then ask for things it doesn't have ("Not Found"). This notices either side being behind
// and offers the one tap that fixes it: restart the server (it checks the new code first), or
// reload this device.
import { html, useEffect, useState } from '/vendor/preact.js';
import { get, post } from '../lib/api.js';
import { setState, useStore } from '../lib/store.js';
import { Icon } from '../lib/ui.js';
import { VERSION } from '../version.js';

const parts = (v) => String(v || '0').split('.').map((n) => parseInt(n, 10) || 0);
export function older(a, b) {
  const x = parts(a);
  const y = parts(b);
  for (let i = 0; i < Math.max(x.length, y.length); i += 1) {
    if ((x[i] || 0) !== (y[i] || 0)) return (x[i] || 0) < (y[i] || 0);
  }
  return false;
}

export async function checkServer() {
  try { setState({ server: await get('/api/health') }); } catch { /* offline; nothing to compare */ }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Fetch the newest pages (through the service worker, if there is one), then reload.
export async function refreshApp() {
  try {
    const reg = await navigator.serviceWorker?.getRegistration();
    if (reg) {
      await reg.update().catch(() => {});
      const w = reg.installing || reg.waiting;
      if (w) {
        await new Promise((done) => {
          const t = setTimeout(done, 4000);
          w.addEventListener('statechange', () => { if (w.state === 'activated') { clearTimeout(t); done(); } });
        });
      }
    }
  } catch { /* reload anyway */ }
  location.reload();
}

// phase: idle | checking | restarting | failed
export function useRestart() {
  const [phase, setPhase] = useState('idle');
  const [problem, setProblem] = useState('');
  const restart = async () => {
    setPhase('checking');
    setProblem('');
    let before = null;
    try { before = (await get('/api/health')).started_at; } catch { /* compare by time below */ }
    try { await post('/api/system/restart'); }
    catch (err) { setPhase('failed'); setProblem(err.missing ? 'This copy of Daybook is too old to restart itself. Restart it on your Mac this once.' : err.message); return; }
    setPhase('restarting');
    const until = Date.now() + 60000;
    while (Date.now() < until) {
      await sleep(800);
      try {
        const h = await get('/api/health');
        if (h.started_at && h.started_at !== before) { await refreshApp(); return; }
      } catch { /* still coming back */ }
    }
    setPhase('failed');
    setProblem("Daybook hasn't come back yet. Check its window on your Mac.");
  };
  return { phase, problem, restart, reset: () => { setPhase('idle'); setProblem(''); } };
}

export function UpdateNotice() {
  const server = useStore((s) => s.server);
  const pagesUpdated = useStore((s) => s.pagesUpdated);
  const [later, setLater] = useState(false);
  const { phase, problem, restart, reset } = useRestart();

  useEffect(() => {
    checkServer();
    const id = setInterval(checkServer, 5 * 60 * 1000);
    const seen = () => { if (!document.hidden) checkServer(); };
    const missing = () => { setLater(false); checkServer(); };
    document.addEventListener('visibilitychange', seen);
    window.addEventListener('daybook:missing', missing);
    return () => { clearInterval(id); document.removeEventListener('visibilitychange', seen); window.removeEventListener('daybook:missing', missing); };
  }, []);

  const serverBehind = !!server && (server.update_ready === true || older(server.version, VERSION));
  const deviceBehind = !serverBehind && (pagesUpdated || (!!server && older(VERSION, server.version)));
  const busy = phase === 'checking' || phase === 'restarting';
  if (!busy && phase !== 'failed' && (later || (!serverBehind && !deviceBehind))) return null;

  let icon = 'refresh-cw';
  let title;
  let body = null;
  let actions = null;
  if (phase === 'checking') { title = 'Checking the update…'; body = 'Making sure the new version starts before switching to it.'; }
  else if (phase === 'restarting') { title = 'Restarting Daybook…'; body = 'Back in a few seconds.'; }
  else if (phase === 'failed') {
    icon = 'triangle-alert';
    title = "The update didn't start";
    body = problem;
    actions = html`<button class="btn btn-sm" onClick=${restart}>Try again</button><button class="btn btn-ghost btn-sm" onClick=${() => { reset(); setLater(true); }}>Close</button>`;
  } else if (serverBehind && server.restartable) {
    title = 'Update ready';
    body = "Daybook's files on your Mac were updated. Restart it to finish. It takes a few seconds and nothing is lost.";
    actions = html`<button class="btn btn-primary btn-sm" onClick=${restart}>Restart now</button><button class="btn btn-ghost btn-sm" onClick=${() => setLater(true)}>Later</button>`;
  } else if (serverBehind) {
    icon = 'triangle-alert';
    title = 'Daybook needs a restart on your Mac';
    body = html`It was updated, but the copy that's running is still the old one (${server.version}), so newer pages show “Not Found”. On the Mac, double-click <strong>Restart Daybook.command</strong>.`;
    actions = html`<button class="btn btn-sm" onClick=${checkServer}>Check again</button><button class="btn btn-ghost btn-sm" onClick=${() => setLater(true)}>Later</button>`;
  } else {
    title = 'A newer Daybook is ready';
    body = 'Reload to use it on this device.';
    actions = html`<button class="btn btn-primary btn-sm" onClick=${refreshApp}>Reload</button><button class="btn btn-ghost btn-sm" onClick=${() => { setLater(true); setState({ pagesUpdated: false }); }}>Later</button>`;
  }
  return html`<aside class="update-note" role="status" aria-live="polite">
    ${busy ? html`<span class="spinner"></span>` : html`<${Icon} name=${icon} />`}
    <div class="grow"><strong>${title}</strong>${body ? html`<p>${body}</p>` : null}
      ${actions ? html`<div class="row" style="gap:6px;margin-top:8px">${actions}</div>` : null}</div>
  </aside>`;
}
