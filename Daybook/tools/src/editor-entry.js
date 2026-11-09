// Rich Markdown editor for Daybook notes (TipTap / ProseMirror).
// Notes are stored as Markdown; this module turns Markdown into an editable
// document and back. Bundled into app/static/vendor/editor.js.
import { Editor, Extension } from '@tiptap/core';
import StarterKit from '@tiptap/starter-kit';
import { Markdown } from '@tiptap/markdown';
import { TaskList, TaskItem } from '@tiptap/extension-list';
import { TableKit } from '@tiptap/extension-table';
import Highlight from '@tiptap/extension-highlight';
import Image from '@tiptap/extension-image';
import { Placeholder, CharacterCount } from '@tiptap/extensions';
import Suggestion from '@tiptap/suggestion';
import { PluginKey } from '@tiptap/pm/state';

export { renderMarkdown, sanitizeHtml, markdownToText } from './md-entry.js';

const iconSvg = (name) =>
  `<svg class="ico" aria-hidden="true"><use href="/icons/sprite.svg#${name}"></use></svg>`;

// ---------------------------------------------------------------------------
// Small floating list used by the "/" command menu and the "[[" note linker.
// ---------------------------------------------------------------------------
function createPopup(className) {
  const el = document.createElement('div');
  el.className = `db-popup ${className}`;
  el.setAttribute('role', 'listbox');
  el.hidden = true;
  document.body.appendChild(el);

  let items = [];
  let active = 0;
  let onPick = null;
  let emptyText = 'No matches';
  let lastRect = null;

  function place(rect) {
    if (!rect) return;
    lastRect = rect;
    el.style.left = '0px';
    el.style.top = '0px';
    const w = el.offsetWidth;
    const h = el.offsetHeight;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    let x = Math.max(8, Math.min(rect.left, vw - w - 8));
    let y = rect.bottom + 6;
    if (y + h > vh - 8 && rect.top - h - 6 > 8) y = rect.top - h - 6;
    el.style.left = `${x}px`;
    el.style.top = `${y}px`;
  }

  function draw() {
    el.innerHTML = '';
    if (!items.length) {
      const empty = document.createElement('div');
      empty.className = 'db-popup-empty';
      empty.textContent = emptyText;
      el.appendChild(empty);
      return;
    }
    let group = null;
    items.forEach((it, i) => {
      if (it.group && it.group !== group) {
        group = it.group;
        const g = document.createElement('div');
        g.className = 'db-popup-group';
        g.textContent = group;
        el.appendChild(g);
      }
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'db-popup-item' + (i === active ? ' is-active' : '');
      b.setAttribute('role', 'option');
      b.setAttribute('aria-selected', i === active ? 'true' : 'false');
      b.innerHTML = `${it.icon ? iconSvg(it.icon) : ''}<span class="db-popup-title"></span><span class="db-popup-hint"></span>`;
      b.querySelector('.db-popup-title').textContent = it.title;
      b.querySelector('.db-popup-hint').textContent = it.hint || '';
      b.addEventListener('mousedown', (e) => {
        e.preventDefault();
        active = i;
        pick();
      });
      el.appendChild(b);
    });
    const act = el.querySelector('.is-active');
    if (act && act.scrollIntoView) act.scrollIntoView({ block: 'nearest' });
  }

  function pick() {
    const it = items[active];
    if (it && onPick) onPick(it);
  }

  return {
    show({ items: next, rect, pick: pickFn, empty }) {
      items = next || [];
      active = Math.min(active, Math.max(0, items.length - 1));
      onPick = pickFn;
      if (empty) emptyText = empty;
      el.hidden = false;
      draw();
      place(rect || lastRect);
    },
    move(delta) {
      if (!items.length) return;
      active = (active + delta + items.length) % items.length;
      draw();
    },
    pick,
    reset() {
      active = 0;
    },
    hide() {
      el.hidden = true;
    },
    destroy() {
      el.remove();
    },
  };
}

function suggestionRenderer(className, emptyText) {
  return () => {
    const popup = createPopup(className);
    let current = null;
    return {
      onStart(props) {
        current = props;
        popup.reset();
        popup.show({ items: props.items, rect: props.clientRect && props.clientRect(), pick: (it) => props.command(it), empty: emptyText });
      },
      onUpdate(props) {
        current = props;
        popup.reset();
        popup.show({ items: props.items, rect: props.clientRect && props.clientRect(), pick: (it) => props.command(it), empty: emptyText });
      },
      onKeyDown({ event }) {
        if (event.key === 'ArrowDown') { popup.move(1); return true; }
        if (event.key === 'ArrowUp') { popup.move(-1); return true; }
        if (event.key === 'Enter' || event.key === 'Tab') {
          if (current && current.items && current.items.length) { popup.pick(); return true; }
          return false;
        }
        if (event.key === 'Escape') { popup.hide(); return true; }
        return false;
      },
      onExit() {
        popup.destroy();
        current = null;
      },
    };
  };
}

// ---------------------------------------------------------------------------
// Built-in "/" commands. The app can append its own (e.g. local AI actions).
// ---------------------------------------------------------------------------
// Block commands typed at the end of a line start a new block instead of
// converting the paragraph you were writing.
function block(e, r, apply) {
  e.chain().focus().deleteRange(r).run();
  const { $from } = e.state.selection;
  let chain = e.chain().focus();
  if ($from.parent.textContent.trim().length > 0) chain = chain.splitBlock();
  return apply(chain).run();
}

function builtinCommands(opts) {
  const today = () =>
    new Date().toLocaleDateString(undefined, { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' });
  return [
    { title: 'Text', icon: 'pilcrow', keywords: 'paragraph plain body', group: 'Blocks',
      run: (e, r) => block(e, r, (c) => c.setParagraph()) },
    { title: 'Heading 1', icon: 'heading-1', keywords: 'h1 title large', group: 'Blocks',
      run: (e, r) => block(e, r, (c) => c.setNode('heading', { level: 1 })) },
    { title: 'Heading 2', icon: 'heading-2', keywords: 'h2 section', group: 'Blocks',
      run: (e, r) => block(e, r, (c) => c.setNode('heading', { level: 2 })) },
    { title: 'Heading 3', icon: 'heading-3', keywords: 'h3 subsection', group: 'Blocks',
      run: (e, r) => block(e, r, (c) => c.setNode('heading', { level: 3 })) },
    { title: 'Checklist', icon: 'list-checks', keywords: 'todo task checkbox to-do', group: 'Blocks',
      run: (e, r) => block(e, r, (c) => c.toggleTaskList()) },
    { title: 'Bulleted list', icon: 'list', keywords: 'ul bullet unordered', group: 'Blocks',
      run: (e, r) => block(e, r, (c) => c.toggleBulletList()) },
    { title: 'Numbered list', icon: 'list-ordered', keywords: 'ol ordered numbers', group: 'Blocks',
      run: (e, r) => block(e, r, (c) => c.toggleOrderedList()) },
    { title: 'Quote', icon: 'quote', keywords: 'blockquote citation', group: 'Blocks',
      run: (e, r) => block(e, r, (c) => c.toggleBlockquote()) },
    { title: 'Code block', icon: 'code', keywords: 'pre snippet sql python', group: 'Blocks',
      run: (e, r) => block(e, r, (c) => c.toggleCodeBlock()) },
    { title: 'Table', icon: 'table', keywords: 'grid columns rows', group: 'Blocks',
      run: (e, r) => e.chain().focus().deleteRange(r).insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run() },
    { title: 'Divider', icon: 'minus', keywords: 'hr rule line separator', group: 'Blocks',
      run: (e, r) => e.chain().focus().deleteRange(r).setHorizontalRule().run() },
    { title: 'Image', icon: 'image', keywords: 'picture photo upload', group: 'Insert',
      run: (e, r) => { e.chain().focus().deleteRange(r).run(); opts.pickImage && opts.pickImage(); } },
    { title: 'Link to a note', icon: 'link-2', keywords: 'wiki backlink reference [[', group: 'Insert', hint: '[[',
      run: (e, r) => e.chain().focus().deleteRange(r).insertContent('[[').run() },
    { title: "Today's date", icon: 'calendar', keywords: 'date now timestamp', group: 'Insert',
      run: (e, r) => e.chain().focus().deleteRange(r).insertContent(today() + ' ').run() },
  ];
}

function matchCommands(list, query) {
  const q = (query || '').toLowerCase().trim();
  if (!q) return list.slice(0, 40);
  return list
    .filter((c) => (c.title + ' ' + (c.keywords || '')).toLowerCase().includes(q))
    .slice(0, 40);
}

function makeSlashExtension(getCommands) {
  return Extension.create({
    name: 'slashCommands',
    addProseMirrorPlugins() {
      return [
        Suggestion({
          editor: this.editor,
          pluginKey: new PluginKey('slashCommands'),
          char: '/',
          startOfLine: false,
          allowSpaces: false,
          allowedPrefixes: [' ', ' '],
          items: ({ query }) => matchCommands(getCommands(), query),
          command: ({ editor, range, props }) => props.run(editor, range),
          render: suggestionRenderer('db-slash', 'No matching command'),
        }),
      ];
    },
  });
}

function makeNoteLinkExtension(opts) {
  return Extension.create({
    name: 'noteLinks',
    addProseMirrorPlugins() {
      return [
        Suggestion({
          editor: this.editor,
          pluginKey: new PluginKey('noteLinks'),
          char: '[[',
          allowSpaces: true,
          allowedPrefixes: null,
          items: async ({ query }) => {
            let found = [];
            try { found = (await opts.searchNotes(query || '')) || []; } catch { found = []; }
            const items = found.slice(0, 8).map((n) => ({ id: n.id, title: n.title || 'Untitled', icon: 'file-text', hint: n.hint || '' }));
            const q = (query || '').trim();
            if (q && opts.createNote && !items.some((i) => i.title.toLowerCase() === q.toLowerCase())) {
              items.push({ create: true, title: `Create “${q}”`, newTitle: q, icon: 'plus' });
            }
            return items;
          },
          command: async ({ editor, range, props }) => {
            let target = props;
            if (props.create) {
              target = await opts.createNote(props.newTitle);
              if (!target) return;
            }
            editor
              .chain()
              .focus()
              .deleteRange(range)
              .insertContent([
                { type: 'text', text: target.title, marks: [{ type: 'link', attrs: { href: `#/notes/${target.id}` } }] },
                { type: 'text', text: ' ' },
              ])
              .run();
          },
          render: suggestionRenderer('db-notelink', 'Type to search your notes'),
        }),
      ];
    },
  });
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------
export function createEditor(opts) {
  const extra = () => (typeof opts.extraCommands === 'function' ? opts.extraCommands() : opts.extraCommands || []);
  const getCommands = () => [...builtinCommands(opts), ...extra()];

  const uploadAndInsert = async (editor, files, pos) => {
    for (const file of files) {
      try {
        if (file.type && file.type.startsWith('image/') && opts.onUploadImage) {
          const url = await opts.onUploadImage(file);
          if (!url) continue;
          const chain = editor.chain().focus();
          if (typeof pos === 'number') chain.insertContentAt(pos, { type: 'image', attrs: { src: url, alt: file.name } });
          else chain.setImage({ src: url, alt: file.name });
          chain.run();
        } else if (opts.onUploadFile) {
          const res = await opts.onUploadFile(file);
          if (!res) continue;
          const node = [
            { type: 'text', text: res.name || file.name, marks: [{ type: 'link', attrs: { href: res.url } }] },
            { type: 'text', text: ' ' },
          ];
          if (typeof pos === 'number') editor.chain().focus().insertContentAt(pos, node).run();
          else editor.chain().focus().insertContent(node).run();
        }
      } catch (err) {
        if (opts.onError) opts.onError(err);
      }
    }
  };

  const editor = new Editor({
    element: opts.element,
    editable: opts.editable !== false,
    content: opts.markdown || '',
    contentType: 'markdown',
    autofocus: opts.autofocus || false,
    extensions: [
      StarterKit.configure({
        heading: { levels: [1, 2, 3, 4] },
        link: {
          openOnClick: false,
          autolink: true,
          linkOnPaste: true,
          HTMLAttributes: { rel: 'noopener noreferrer nofollow', target: null },
        },
      }),
      Markdown.configure({ indentation: { style: 'space', size: 2 } }),
      TaskList,
      TaskItem.configure({ nested: true }),
      TableKit.configure({ table: { resizable: false } }),
      Highlight,
      Image.configure({ inline: false, allowBase64: false }),
      Placeholder.configure({
        placeholder: ({ node }) => {
          if (node.type.name === 'heading') return 'Heading';
          return opts.placeholder || 'Write here. Press / for blocks, [[ to link a note.';
        },
      }),
      CharacterCount,
      makeSlashExtension(getCommands),
      ...(opts.searchNotes ? [makeNoteLinkExtension(opts)] : []),
    ],
    editorProps: {
      attributes: { class: 'prose db-editor', spellcheck: 'true', 'aria-label': 'Note body' },
      handleClick(view, pos, event) {
        const a = event.target && event.target.closest ? event.target.closest('a') : null;
        if (!a) return false;
        const href = a.getAttribute('href') || '';
        if (event.metaKey || event.ctrlKey || !view.editable) {
          event.preventDefault();
          if (opts.onOpenLink) opts.onOpenLink(href);
          return true;
        }
        return false;
      },
      handlePaste(view, event) {
        const files = Array.from((event.clipboardData && event.clipboardData.files) || []);
        if (!files.length) return false;
        event.preventDefault();
        uploadAndInsert(editor, files);
        return true;
      },
      handleDrop(view, event, _slice, moved) {
        if (moved) return false;
        const files = Array.from((event.dataTransfer && event.dataTransfer.files) || []);
        if (!files.length) return false;
        event.preventDefault();
        const coords = view.posAtCoords({ left: event.clientX, top: event.clientY });
        uploadAndInsert(editor, files, coords ? coords.pos : undefined);
        return true;
      },
    },
    onUpdate: () => opts.onUpdate && opts.onUpdate(),
    onSelectionUpdate: () => opts.onSelectionUpdate && opts.onSelectionUpdate(),
    onTransaction: () => opts.onTransaction && opts.onTransaction(),
    onFocus: () => opts.onFocus && opts.onFocus(),
    onBlur: () => opts.onBlur && opts.onBlur(),
  });

  const selectionMarkdown = () => {
    const { from, to, empty } = editor.state.selection;
    if (empty) return '';
    const slice = editor.state.doc.slice(from, to);
    try {
      return editor.markdown.serialize({ type: 'doc', content: slice.content.toJSON() || [] }).trim();
    } catch {
      return editor.state.doc.textBetween(from, to, '\n\n');
    }
  };

  return {
    editor,
    getMarkdown: () => editor.getMarkdown(),
    setMarkdown: (md, emitUpdate = false) => editor.commands.setContent(md || '', { contentType: 'markdown', emitUpdate }),
    insertMarkdown: (md) => editor.chain().focus().insertContent(md, { contentType: 'markdown' }).run(),
    replaceSelection: (md) => {
      const { from, to } = editor.state.selection;
      editor.chain().focus().insertContentAt({ from, to }, md, { contentType: 'markdown' }).run();
    },
    selectionText: () => {
      const { from, to } = editor.state.selection;
      return editor.state.doc.textBetween(from, to, '\n\n');
    },
    selectionMarkdown,
    wordCount: () => editor.storage.characterCount.words(),
    isActive: (name, attrs) => editor.isActive(name, attrs),
    chain: () => editor.chain().focus(),
    setEditable: (v) => editor.setEditable(v),
    // view.dom.focus() is synchronous, so keystrokes go to the editor right away;
    // TipTap's focus() then places the cursor on the next frame.
    focus: (where) => { editor.view.dom.focus(); editor.commands.focus(where); },
    insertFiles: (files) => uploadAndInsert(editor, files),
    destroy: () => editor.destroy(),
  };
}
