// Tiny global store with a Preact hook. Holds things many views share.
import { useEffect, useState } from '/vendor/preact.js';

const state = {
  auth: null,          // /api/auth/status
  settings: null,
  calendars: [],
  notebooks: [],
  tags: [],
  ai: null,            // /api/ai/status
  counts: {},
  toasts: [],
  online: true,
  live: false,
  vault: null,         // vault passphrase held in memory only while unlocked
  library: null,       // /api/library/status
  dateDialog: null,
};
const listeners = new Set();

export const getState = () => state;

export function setState(patch) {
  Object.assign(state, typeof patch === 'function' ? patch(state) : patch);
  for (const fn of listeners) fn(state);
}

export function useStore(select = (s) => s) {
  const [value, setValue] = useState(() => select(state));
  useEffect(() => {
    const fn = (s) => {
      const next = select(s);
      setValue((prev) => (Object.is(prev, next) ? prev : next));
    };
    listeners.add(fn);
    fn(state);
    return () => listeners.delete(fn);
  }, []);
  return value;
}

let toastId = 0;
export function toast(message, { error = false, action = null, timeout = 3600 } = {}) {
  const id = ++toastId;
  setState((s) => ({ toasts: [...s.toasts, { id, message, error, action }] }));
  if (timeout) setTimeout(() => dismissToast(id), error ? timeout + 2400 : timeout);
  return id;
}
export function dismissToast(id) {
  setState((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }));
}
export const toastError = (err) => toast(err && err.message ? err.message : String(err), { error: true });

// Lightweight pub/sub for "data changed" events coming from the server stream.
const changeListeners = new Set();
export function onChange(fn) { changeListeners.add(fn); return () => changeListeners.delete(fn); }
export function emitChange(evt) { for (const fn of changeListeners) fn(evt); }

export function useChanges(entities, fn, deps = []) {
  useEffect(() => onChange((evt) => {
    if (!entities || entities.includes(evt.entity)) fn(evt);
  }), deps);
}
