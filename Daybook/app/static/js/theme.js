// Runs before first paint so dark-mode users never see a flash of light paper.
(function () {
  var pref = 'system';
  try { pref = localStorage.getItem('daybook_theme') || 'system'; } catch (e) { /* private mode */ }
  var dark = pref === 'dark' || (pref !== 'light' && window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
  document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light');
})();
