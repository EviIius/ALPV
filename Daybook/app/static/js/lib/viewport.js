// How iOS lays out Home Screen apps, and what Daybook does about it.
//
// iPhone Home Screen app, iOS 26 and later: with a see-through status bar iOS lays the page out one
// status-bar height short of the screen (47 to 62 px, depending on the phone) and draws nothing of
// the page below that line, so stretching the app into the strip only gets it cut off. This measures
// the strip and lets the styles work with it (--vp-gap in base.css, html.vp-gap in app.css): the tab
// bar stays where iOS draws, without the home-indicator padding the strip already gives, and the
// strip takes the bar's colour. On a fixed iOS, in Safari and everywhere else the gap is 0.
export const viewport = { gap: 0, info: null };

(() => {
  const root = document.documentElement;
  if (!/iPhone|iPod/.test(navigator.userAgent)) return;
  let probe = null;
  const measure = () => {
    if (!document.body) return;
    const standalone = window.navigator.standalone === true || window.matchMedia('(display-mode: standalone)').matches;
    if (!probe) {
      probe = document.createElement('div');
      probe.setAttribute('aria-hidden', 'true');
      probe.style.cssText = 'position:fixed;top:0;bottom:0;left:0;width:0;visibility:hidden;pointer-events:none;'
        + 'padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)';
      document.body.appendChild(probe);
    }
    const cs = getComputedStyle(probe);
    const laidOut = Math.round(probe.getBoundingClientRect().height) || window.innerHeight;
    const insetTop = Math.round(parseFloat(cs.paddingTop) || 0);
    const insetBottom = Math.round(parseFloat(cs.paddingBottom) || 0);
    const portrait = window.matchMedia('(orientation: portrait)').matches;
    const screenH = (portrait ? Math.max(screen.width, screen.height) : Math.min(screen.width, screen.height)) || 0;
    const missing = screenH - laidOut;
    // The bug leaves out exactly the status bar. Anything else (an opaque status bar, a fixed iOS,
    // landscape) is a normal layout and is left alone.
    const gap = standalone && insetTop >= 20 && missing >= 20 && missing <= 80 && Math.abs(missing - insetTop) <= 3 ? missing : 0;
    viewport.info = { standalone, screen: `${screen.width}×${screen.height}`, window: `${window.innerWidth}×${window.innerHeight}`, laidOut, insetTop, insetBottom, gap };
    if (gap === viewport.gap) return;
    viewport.gap = gap;
    root.classList.toggle('vp-gap', gap > 0);
    if (gap) root.style.setProperty('--vp-gap', `${gap}px`); else root.style.removeProperty('--vp-gap');
  };
  measure();
  for (const ms of [250, 1000, 3000]) setTimeout(measure, ms); // iOS can settle the layout a moment after launch
  window.addEventListener('resize', measure);
  window.addEventListener('pageshow', measure);
  window.addEventListener('orientationchange', () => setTimeout(measure, 300));
  window.visualViewport?.addEventListener('resize', measure);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) measure(); });
})();
