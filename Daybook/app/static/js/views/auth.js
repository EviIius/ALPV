// First-run setup and sign-in.
import { html, useState } from '/vendor/preact.js';
import { post } from '../lib/api.js';
import { BrandMark, Field, Icon } from '../lib/ui.js';

const PROMISES = [
  'Notes, calendar and files stay on your computer.',
  'No accounts, analytics or tracking. Nothing is loaded from the internet.',
  'AI features only use models running on your own hardware.',
];

export function AuthScreen({ auth, onDone }) {
  const setup = !auth.configured;
  const [pass, setPass] = useState('');
  const [pass2, setPass2] = useState('');
  const [code, setCode] = useState('');
  const [error, setError] = useState(auth.error || '');
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setError('');
    if (setup) {
      if (pass.length < 8) return setError('Use at least 8 characters.');
      if (pass !== pass2) return setError("The two passphrases don't match.");
    }
    setBusy(true);
    try {
      if (setup) await post('/api/auth/setup', { passphrase: pass, code });
      else await post('/api/auth/login', { passphrase: pass });
      onDone();
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  };

  return html`<div class="auth paper-grid">
    <form class="auth-sheet" onSubmit=${submit}>
      <div class="auth-top"><${BrandMark} />
        <div><h1>Daybook</h1><p>${setup ? 'Set up your private workspace' : 'Your private notes, calendar and tasks'}</p></div>
      </div>
      <div class="auth-body">
        ${setup ? html`<div class="auth-promise">${PROMISES.map((p) => html`<div><${Icon} name="check" /><span>${p}</span></div>`)}</div>` : null}
        ${setup && auth.setup_needs_code ? html`<${Field} label="Setup code" hint="Shown in the terminal window where Daybook is running. It proves this device belongs to you.">
          <input class="input" inputmode="numeric" autocomplete="one-time-code" placeholder="123-456" value=${code} onInput=${(e) => setCode(e.currentTarget.value)} required />
        </${Field}>` : null}
        <${Field} label=${setup ? 'Choose a passphrase' : 'Passphrase'} hint=${setup ? 'At least 8 characters. A few random words work well. There is no reset, so keep it somewhere safe.' : ''}>
          <input class="input" type="password" autocomplete=${setup ? 'new-password' : 'current-password'} value=${pass} onInput=${(e) => setPass(e.currentTarget.value)} autofocus required />
        </${Field}>
        ${setup ? html`<${Field} label="Type it again">
          <input class="input" type="password" autocomplete="new-password" value=${pass2} onInput=${(e) => setPass2(e.currentTarget.value)} required />
        </${Field}>` : null}
        ${error ? html`<p class="error-text" role="alert">${error}</p>` : null}
        <button class="btn btn-ink btn-lg btn-block" disabled=${busy}>${busy ? 'One moment…' : setup ? 'Create my workspace' : 'Unlock'}</button>
        ${!setup && auth.connection === 'lan' && !auth.secure ? html`<p class="faint" style="font-size:12px;line-height:1.5">You're on your local network without encryption. Connecting over Tailscale keeps your passphrase and notes encrypted in transit.</p>` : null}
      </div>
    </form>
  </div>`;
}
