// Shared UI pieces: icons, dialogs, menus, toasts, form controls.
import { html, useEffect, useRef, useState, useCallback } from '/vendor/preact.js';
import { renderMarkdown } from '/vendor/md.js';
import { dismissToast, setState, useStore } from './store.js';

export const Icon = ({ name, size = '', cls = '' }) =>
  html`<svg class=${`ico ${size ? 'ico-' + size : ''} ${cls}`} aria-hidden="true"><use href=${`/icons/sprite.svg#${name}`}></use></svg>`;

export const BrandMark = ({ cls = 'brand-mark' }) => html`
  <svg class=${cls} viewBox="0 0 64 64" width="28" height="28" aria-hidden="true">
    <rect width="64" height="64" rx="14" fill="#E8F0E3" />
    <path d="M8 18v40M16 18v46M24 18v46M32 18v46M40 18v46M48 18v46M56 18v40M4 24h56M0 32h64M0 40h64M0 48h64M2 56h60" stroke="#386C48" stroke-opacity=".17" stroke-width=".8" />
    <path d="M0 14A14 14 0 0 1 14 0h36a14 14 0 0 1 14 14v3H0z" fill="#1F2D26" />
    <path d="M43.2 29.4c5.3 2.9 7.6 8.9 4.6 14.5-3.1 5.8-12.2 8.9-20.3 7.9-8.1-1-13.9-6.2-13.1-12.3.8-6.4 9.3-11.2 18.6-10.8 3.4.1 6.3.8 8.4 1.8" fill="none" stroke="#C23A2E" stroke-width="2.8" stroke-linecap="round" />
  </svg>`;

// A slightly irregular red-pencil ring, as if today's date was circled by hand.
export const PencilCircle = () => html`
  <svg class="pencil-circle" viewBox="0 0 44 36" preserveAspectRatio="none" aria-hidden="true">
    <path d="M37.8 9.6C42.2 13.9 41.6 22 35.6 27.6 28.8 33.9 15.4 34.6 8.4 29.1 1.6 23.8 2.9 13.4 10.6 7.9 17.6 2.9 29.4 2.2 35.2 6.7"
      fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" vector-effect="non-scaling-stroke"/>
  </svg>`;

export function Markdown({ text, cls = 'prose prose-sm' }) {
  return html`<div class=${cls} dangerouslySetInnerHTML=${{ __html: renderMarkdown(text || '') }}></div>`;
}

export function Spinner({ label }) {
  return html`<span class="row" role="status"><span class="spinner"></span>${label ? html`<span class="muted">${label}</span>` : null}</span>`;
}

export function Switch({ checked, onChange, label, disabled }) {
  return html`<label class="switch">
    <input type="checkbox" checked=${!!checked} disabled=${disabled} onChange=${(e) => onChange(e.currentTarget.checked)} />
    <span class="track"></span>${label ? html`<span>${label}</span>` : null}
  </label>`;
}

export function Seg({ value, options, onChange, label }) {
  return html`<div class="seg" role="group" aria-label=${label || ''}>
    ${options.map((o) => html`<button type="button" aria-pressed=${String(o.value === value)} onClick=${() => onChange(o.value)}>${o.label}</button>`)}
  </div>`;
}

export function Field({ label, hint, children, cls = '' }) {
  return html`<label class=${`field ${cls}`}><span>${label}</span>${children}${hint ? html`<span class="hint">${hint}</span>` : null}</label>`;
}

export function Empty({ title, children, icon }) {
  return html`<div class="empty">${icon ? html`<${Icon} name=${icon} size="lg" />` : null}<strong>${title}</strong>${children}</div>`;
}

// ---------------------------------------------------------------------------
// Modal
// ---------------------------------------------------------------------------
export function Modal({ title, onClose, children, footer, wide = false, cls = '', labelledBy }) {
  const ref = useRef(null);
  useEffect(() => {
    const prev = document.activeElement;
    const el = ref.current;
    const first = el && el.querySelector('[autofocus], input:not([type=hidden]), textarea, select, button.btn-primary');
    if (first) setTimeout(() => first.focus(), 30);
    const onKey = (e) => {
      if (e.key === 'Escape') { e.stopPropagation(); onClose && onClose(); }
      if (e.key === 'Tab' && el) {
        const items = [...el.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])')].filter((x) => !x.disabled && x.offsetParent !== null);
        if (!items.length) return;
        const firstI = items[0];
        const lastI = items[items.length - 1];
        if (e.shiftKey && document.activeElement === firstI) { e.preventDefault(); lastI.focus(); }
        else if (!e.shiftKey && document.activeElement === lastI) { e.preventDefault(); firstI.focus(); }
      }
    };
    document.addEventListener('keydown', onKey, true);
    return () => { document.removeEventListener('keydown', onKey, true); if (prev && prev.focus) prev.focus(); };
  }, []);
  return html`<div class="backdrop" onMouseDown=${(e) => { if (e.target === e.currentTarget && onClose) onClose(); }}>
    <div class=${`modal ${wide ? 'modal-wide' : ''} ${cls}`} role="dialog" aria-modal="true" aria-label=${title} ref=${ref}>
      ${title ? html`<div class="modal-head"><h2 id=${labelledBy}>${title}</h2>
        <button class="btn btn-ghost btn-icon" onClick=${onClose} aria-label="Close"><${Icon} name="x" /></button></div>` : null}
      <div class="modal-body">${children}</div>
      ${footer ? html`<div class="modal-foot">${footer}</div>` : null}
    </div>
  </div>`;
}

// ---------------------------------------------------------------------------
// Promise-based dialogs (confirm, prompt, choose)
// ---------------------------------------------------------------------------
export function confirmDialog({ title, message, confirm = 'Confirm', danger = false, cancel = 'Cancel' }) {
  return new Promise((resolve) => setState({ dialog: { kind: 'confirm', title, message, confirm, danger, cancel, resolve } }));
}
export function promptDialog({ title, label, value = '', placeholder = '', confirm = 'Save', type = 'text', hint = '' }) {
  return new Promise((resolve) => setState({ dialog: { kind: 'prompt', title, label, value, placeholder, confirm, type, hint, resolve } }));
}
export function chooseDialog({ title, message, choices }) {
  return new Promise((resolve) => setState({ dialog: { kind: 'choose', title, message, choices, resolve } }));
}

export function DialogHost() {
  const d = useStore((s) => s.dialog);
  const [val, setVal] = useState('');
  useEffect(() => { if (d && d.kind === 'prompt') setVal(d.value || ''); }, [d]);
  if (!d) return null;
  const close = (result) => { setState({ dialog: null }); d.resolve(result); };
  if (d.kind === 'confirm') {
    return html`<${Modal} title=${d.title} onClose=${() => close(false)} footer=${html`
      <button class="btn" onClick=${() => close(false)}>${d.cancel}</button>
      <button class=${`btn ${d.danger ? 'btn-danger' : 'btn-primary'}`} onClick=${() => close(true)} autofocus>${d.confirm}</button>`}>
      <p class="muted" style="line-height:1.55">${d.message}</p>
    </${Modal}>`;
  }
  if (d.kind === 'choose') {
    return html`<${Modal} title=${d.title} onClose=${() => close(null)}>
      ${d.message ? html`<p class="muted" style="margin-bottom:12px;line-height:1.5">${d.message}</p>` : null}
      <div class="stack">${d.choices.map((c) => html`<button class=${`btn btn-block ${c.danger ? 'btn-danger' : c.primary ? 'btn-primary' : ''}`} onClick=${() => close(c.value)}>${c.label}</button>`)}
        <button class="btn btn-ghost btn-block" onClick=${() => close(null)}>Cancel</button></div>
    </${Modal}>`;
  }
  return html`<${Modal} title=${d.title} onClose=${() => close(null)} footer=${html`
      <button class="btn" onClick=${() => close(null)}>Cancel</button>
      <button class="btn btn-primary" onClick=${() => close(val)}>${d.confirm}</button>`}>
    <form onSubmit=${(e) => { e.preventDefault(); close(val); }}>
      <${Field} label=${d.label} hint=${d.hint}>
        <input class="input" type=${d.type} value=${val} placeholder=${d.placeholder} onInput=${(e) => setVal(e.currentTarget.value)} autofocus autocomplete=${d.type === 'password' ? 'current-password' : 'off'} />
      </${Field}>
    </form>
  </${Modal}>`;
}

// ---------------------------------------------------------------------------
// Popover menu
// ---------------------------------------------------------------------------
export function openMenu(anchor, items, { align = 'start' } = {}) {
  const r = anchor.getBoundingClientRect();
  setState({ menu: { rect: { left: r.left, right: r.right, top: r.top, bottom: r.bottom }, items, align } });
}

export function MenuHost() {
  const m = useStore((s) => s.menu);
  const ref = useRef(null);
  const [pos, setPos] = useState(null);
  useEffect(() => {
    if (!m) return undefined;
    const el = ref.current;
    if (el) {
      const w = el.offsetWidth;
      const h = el.offsetHeight;
      let left = m.align === 'end' ? m.rect.right - w : m.rect.left;
      left = Math.max(8, Math.min(left, window.innerWidth - w - 8));
      let top = m.rect.bottom + 4;
      if (top + h > window.innerHeight - 8) top = Math.max(8, m.rect.top - h - 4);
      setPos({ left, top });
      const first = el.querySelector('.menu-item');
      if (first) first.focus();
    }
    const close = (e) => { if (!el || !el.contains(e.target)) setState({ menu: null }); };
    const key = (e) => {
      if (e.key === 'Escape') setState({ menu: null });
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        const items = [...el.querySelectorAll('.menu-item')];
        const i = items.indexOf(document.activeElement);
        const next = items[(i + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length];
        if (next) { e.preventDefault(); next.focus(); }
      }
    };
    setTimeout(() => document.addEventListener('mousedown', close), 0);
    document.addEventListener('keydown', key);
    window.addEventListener('resize', () => setState({ menu: null }), { once: true });
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', key); setPos(null); };
  }, [m]);
  if (!m) return null;
  return html`<div class="menu" role="menu" ref=${ref} style=${pos ? `left:${pos.left}px;top:${pos.top}px` : 'left:-9999px;top:0'}>
    ${m.items.filter(Boolean).map((it) => {
      if (it.sep) return html`<div class="menu-sep"></div>`;
      if (it.header) return html`<div class="menu-label">${it.header}</div>`;
      return html`<button class=${`menu-item ${it.danger ? 'danger' : ''}`} role="menuitem" disabled=${it.disabled}
        onClick=${() => { setState({ menu: null }); it.onSelect && it.onSelect(); }}>
        ${it.icon ? html`<${Icon} name=${it.icon} />` : html`<span style="width:16px"></span>`}<span>${it.label}</span>
        ${it.hint ? html`<span class="hint">${it.hint}</span>` : null}
      </button>`;
    })}
  </div>`;
}

export function Toasts() {
  const toasts = useStore((s) => s.toasts);
  return html`<div class="toasts" aria-live="polite">
    ${toasts.map((t) => html`<div class=${`toast ${t.error ? 'toast-error' : ''}`} key=${t.id}>
      <${Icon} name=${t.error ? 'circle-alert' : 'check'} />
      <span>${t.message}</span>
      ${t.action ? html`<button onClick=${() => { dismissToast(t.id); t.action.run(); }}>${t.action.label}</button>` : null}
    </div>`)}
  </div>`;
}

// ---------------------------------------------------------------------------
// Hooks
// ---------------------------------------------------------------------------
export function useDebounced(fn, ms) {
  const t = useRef(null);
  const latest = useRef(fn);
  latest.current = fn;
  const call = useCallback((...args) => {
    clearTimeout(t.current);
    t.current = setTimeout(() => latest.current(...args), ms);
  }, [ms]);
  call.flush = (...args) => { clearTimeout(t.current); return latest.current(...args); };
  call.cancel = () => clearTimeout(t.current);
  return call;
}

export function useTick(ms = 30000) {
  const [, set] = useState(0);
  useEffect(() => { const id = setInterval(() => set((x) => x + 1), ms); return () => clearInterval(id); }, [ms]);
}

export function useMedia(query) {
  const [match, setMatch] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const fn = () => setMatch(mq.matches);
    mq.addEventListener('change', fn);
    return () => mq.removeEventListener('change', fn);
  }, [query]);
  return match;
}

export const isMac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
export const modKey = isMac ? '⌘' : 'Ctrl';
