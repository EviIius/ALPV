// Builds every third-party frontend asset Daybook needs into app/static so the
// app never loads anything from a CDN at runtime.
//
//   cd tools && npm install && npm run build
//
import { build } from 'esbuild';
import { copyFileSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const nm = join(here, 'node_modules');
const STATIC = join(here, '..', 'app', 'static');
const VENDOR = join(STATIC, 'vendor');
const FONTS = join(STATIC, 'fonts');
const ICONS = join(STATIC, 'icons');

// 1. JavaScript bundles (ES modules, shared chunks) -------------------------
rmSync(VENDOR, { recursive: true, force: true });
mkdirSync(VENDOR, { recursive: true });
await build({
  absWorkingDir: here,
  entryPoints: {
    editor: 'src/editor-entry.js',
    md: 'src/md-entry.js',
    preact: 'src/preact-entry.js',
  },
  bundle: true,
  splitting: true,
  format: 'esm',
  minify: true,
  target: ['es2020', 'safari15'],
  outdir: VENDOR,
  chunkNames: 'chunk-[hash]',
  legalComments: 'none',
  logLevel: 'info',
});

// 2. Fonts (self-hosted, OFL) -----------------------------------------------
mkdirSync(FONTS, { recursive: true });
const fontFiles = [
  ['@fontsource-variable/archivo/files/archivo-latin-wdth-normal.woff2', 'archivo-latin.woff2'],
  ['@fontsource-variable/archivo/files/archivo-latin-ext-wdth-normal.woff2', 'archivo-latin-ext.woff2'],
  ['@fontsource-variable/literata/files/literata-latin-opsz-normal.woff2', 'literata-latin.woff2'],
  ['@fontsource-variable/literata/files/literata-latin-ext-opsz-normal.woff2', 'literata-latin-ext.woff2'],
  ['@fontsource-variable/literata/files/literata-latin-opsz-italic.woff2', 'literata-latin-italic.woff2'],
  ['@fontsource-variable/literata/files/literata-latin-ext-opsz-italic.woff2', 'literata-latin-ext-italic.woff2'],
];
for (const [src, dest] of fontFiles) copyFileSync(join(nm, src), join(FONTS, dest));
copyFileSync(join(nm, '@fontsource-variable/archivo/LICENSE'), join(FONTS, 'LICENSE-archivo.txt'));
copyFileSync(join(nm, '@fontsource-variable/literata/LICENSE'), join(FONTS, 'LICENSE-literata.txt'));

// 3. Icon sprite (Lucide, ISC) -------------------------------------------------
const ICON_NAMES = [
  'sun', 'calendar', 'calendar-days', 'calendar-plus', 'calendar-check', 'calendar-clock', 'notebook-pen', 'notebook',
  'file-text', 'file', 'file-plus', 'file-up', 'file-spreadsheet', 'presentation', 'file-image', 'file-audio',
  'file-archive', 'file-code', 'mail', 'list-checks', 'list-todo', 'inbox', 'upload', 'download', 'sparkles',
  'message-square-text', 'settings', 'search', 'plus', 'x', 'chevron-left', 'chevron-right', 'chevron-down',
  'chevron-up', 'ellipsis', 'pin', 'pin-off', 'trash-2', 'archive', 'archive-restore', 'lock', 'lock-open',
  'shield-check', 'shield-alert', 'eye', 'eye-off', 'tag', 'hash', 'folder', 'folder-plus', 'folder-open', 'clock',
  'bell', 'bell-ring', 'bell-off', 'link', 'link-2', 'external-link', 'history', 'copy', 'refresh-cw', 'rotate-ccw',
  'wand-sparkles', 'list', 'list-ordered', 'heading-1', 'heading-2', 'heading-3', 'pilcrow', 'quote', 'code', 'table',
  'minus', 'image', 'bold', 'italic', 'underline', 'strikethrough', 'highlighter', 'undo-2', 'redo-2', 'check',
  'circle', 'circle-check', 'circle-alert', 'flag', 'star', 'menu', 'log-out', 'monitor', 'smartphone', 'cpu',
  'hard-drive', 'server', 'wifi', 'triangle-alert', 'info', 'loader-circle', 'send', 'arrow-up', 'arrow-down',
  'arrow-left', 'corner-down-left', 'map-pin', 'repeat', 'command', 'moon', 'sun-moon', 'palette', 'square',
  'square-check', 'panel-left', 'maximize-2', 'book-open', 'layout-grid', 'columns-3', 'rows-3', 'mic', 'key-round',
  'sliders-horizontal', 'users', 'zap', 'brain', 'globe', 'ban', 'cloud-off', 'database', 'scan-text', 'audio-lines',
  'message-circle', 'printer', 'filter', 'calendar-range', 'list-tree', 'gauge', 'hourglass', 'pencil', 'undo',
  'library', 'folder-git-2', 'git-branch', 'git-compare', 'gavel', 'calendar-x', 'calendar-search', 'user-round',
  'file-question-mark', 'app-window', 'clipboard-list', 'square-arrow-out-up-right', 'circle-help', 'badge-check',
  'milestone', 'file-clock', 'arrow-right-left', 'check-check',
];
mkdirSync(ICONS, { recursive: true });
const iconDir = join(nm, 'lucide-static', 'icons');
const missing = [];
const symbols = ICON_NAMES.map((name) => {
  const file = join(iconDir, `${name}.svg`);
  if (!existsSync(file)) { missing.push(name); return ''; }
  const svg = readFileSync(file, 'utf8');
  const inner = svg.replace(/^[\s\S]*?<svg[^>]*>/, '').replace(/<\/svg>\s*$/, '').replace(/<!--[\s\S]*?-->/g, '').trim();
  return `<symbol id="${name}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${inner}</symbol>`;
});
if (missing.length) console.warn('Missing icons:', missing.join(', '));
writeFileSync(
  join(ICONS, 'sprite.svg'),
  `<svg xmlns="http://www.w3.org/2000/svg" style="display:none">${symbols.join('')}</svg>\n`,
);
copyFileSync(join(nm, 'lucide-static', 'LICENSE'), join(ICONS, 'LICENSE-lucide.txt'));

// 4. Third-party notices -----------------------------------------------------
const notices = ['preact', 'htm', 'dompurify', 'marked', '@tiptap/core', '@tiptap/pm']
  .map((p) => {
    const pkg = JSON.parse(readFileSync(join(nm, p, 'package.json'), 'utf8'));
    return `${pkg.name}@${pkg.version} — ${typeof pkg.license === 'string' ? pkg.license : JSON.stringify(pkg.license)}`;
  })
  .join('\n');
writeFileSync(
  join(VENDOR, 'THIRD_PARTY_NOTICES.txt'),
  `Bundled by tools/build.mjs. All code is served from this machine; nothing is fetched from a CDN.\n\n${notices}\n` +
    `Fonts: Archivo, Literata (SIL Open Font License 1.1). Icons: Lucide (ISC).\n`,
);
console.log('Vendored assets written to', STATIC, `(${readdirSync(VENDOR).length} vendor files)`);
