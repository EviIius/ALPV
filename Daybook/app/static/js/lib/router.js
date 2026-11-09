// Hash routes: #/today, #/calendar/2026-11-08?view=week, #/notes, #/notes/<id>,
// #/tasks/<view>, #/import, #/ask, #/settings/<section>
import { useEffect, useState } from '/vendor/preact.js';

export function parseHash(hash = location.hash) {
  const h = hash.replace(/^#\/?/, '');
  const [path, query] = h.split('?');
  const parts = path.split('/').filter(Boolean).map(decodeURIComponent);
  return { name: parts[0] || 'today', parts, params: new URLSearchParams(query || '') };
}

export function navigate(to, { replace = false } = {}) {
  const target = to.startsWith('#') ? to : `#${to}`;
  if (replace) history.replaceState(null, '', target);
  else if (location.hash !== target) location.hash = target;
  if (replace) window.dispatchEvent(new HashChangeEvent('hashchange'));
}

export function useRoute() {
  const [route, setRoute] = useState(() => parseHash());
  useEffect(() => {
    const fn = () => setRoute(parseHash());
    window.addEventListener('hashchange', fn);
    return () => window.removeEventListener('hashchange', fn);
  }, []);
  return route;
}

// Lets an editor flush unsaved work before the route changes.
const guards = new Set();
export function addRouteGuard(fn) { guards.add(fn); return () => guards.delete(fn); }
window.addEventListener('hashchange', () => { for (const g of guards) g(); });
