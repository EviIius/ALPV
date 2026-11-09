// Markdown rendering + HTML sanitising for read-only views (note previews,
// AI answers, imported content). Bundled into app/static/vendor/md.js.
import { Marked } from 'marked';
import DOMPurify from 'dompurify';

const marked = new Marked({ gfm: true, breaks: false });

const SAFE_IMG = /^(\/|data:image\/(png|jpe?g|gif|webp|svg\+xml);|blob:)/i;
const EXTERNAL = /^(https?:|mailto:|tel:)/i;

DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  if (node.tagName === 'A') {
    const href = node.getAttribute('href') || '';
    if (EXTERNAL.test(href)) {
      node.setAttribute('target', '_blank');
      node.setAttribute('rel', 'noopener noreferrer nofollow');
    }
  }
  if (node.tagName === 'IMG') {
    // Remote images are blocked so a note can never "phone home"
    // (tracking pixels in imported emails, web clippings, etc.).
    const src = node.getAttribute('src') || '';
    if (!SAFE_IMG.test(src)) {
      node.removeAttribute('src');
      node.setAttribute('data-blocked', 'true');
      node.setAttribute('alt', 'Remote image blocked for privacy');
    }
    node.setAttribute('loading', 'lazy');
  }
  if (node.tagName === 'INPUT') {
    if ((node.getAttribute('type') || '').toLowerCase() !== 'checkbox') {
      node.remove();
    } else {
      node.setAttribute('disabled', '');
    }
  }
});

const PURIFY = {
  USE_PROFILES: { html: true },
  FORBID_TAGS: ['style', 'form', 'button', 'select', 'textarea', 'iframe', 'object', 'embed'],
  FORBID_ATTR: ['style'],
};

export function sanitizeHtml(html) {
  return DOMPurify.sanitize(html || '', PURIFY);
}

export function renderMarkdown(src, { inline = false } = {}) {
  const html = inline ? marked.parseInline(src || '') : marked.parse(src || '');
  return sanitizeHtml(html);
}

export function markdownToText(src) {
  const div = document.createElement('div');
  div.innerHTML = renderMarkdown(src);
  return (div.textContent || '').replace(/\s+\n/g, '\n').trim();
}
