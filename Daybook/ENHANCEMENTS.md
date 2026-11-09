# Enhancements

Version 2.0 rebuilt Calendar-Notes-Discord as **Daybook**. It started as a browser-only note and calendar page. It's now a private, self-hosted workspace with server storage, document import and local AI. Version 2.1 merges it into the **ALPV** repository and reads that folder as its library. Version 2.2 adds things most note and calendar apps don't do, all offline and on your own Mac: a reader that draws PowerPoint decks as slides, a glossary that learns your acronyms and how transcripts mishear them, meeting prep, a private calendar feed for your phone, search operators, and time set aside for tasks.

This file lists what shipped and what's planned next. Each planned item has a short reason and a rough size: **S** (an evening), **M** (a weekend), **L** (bigger).

---

## Shipped in 2.2: read, prepare and plan, without leaving Daybook

### Reader for decks, PDFs and spreadsheets
- **PowerPoint drawn as slides, offline.** Daybook builds each slide from the `.pptx` itself: layouts and masters, theme colours and fonts, text boxes with bullets and numbering, about 40 common shapes (rounded and snipped rectangles, ellipses, arrows, chevrons, stars, callouts, flowchart shapes) and lines, pictures (with cropping), tables with their styles, charts (bar, column, line, area, pie, doughnut) from the values saved in the file, groups, SmartArt and speaker notes. Calibri-metric fonts (Carlito) are bundled so text wraps like it does in PowerPoint. Most apps either can't show a deck at all or upload it to an online viewer.
- **PDFs** page by page with selectable text (bundled PDF.js), and **spreadsheets** as tables with sheet tabs.
- **Highlights and comments** in five colours on slides, PDF pages and documents. They're anchored to the text, so they survive re-rendering. **Save highlights as a note** gathers them into an editable note, and the **Highlights** page lists everything you've marked.
- **Find in a document** (⌘F) with every match marked, a slide rail, and **Present** mode (full screen, arrow keys or swipe, speaker notes with S).
- **Search inside every deck and PDF.** Each slide and page is indexed, so search and ⌘K jump straight to slide 13 of the right deck, with the term highlighted.
- Deep links (`#/read/<note>?p=4&q=storage`) and a phone layout with a bottom sheet.

### Glossary that learns your words
- Definitions are found **by rule** in your notes: "Data private cloud (DPC)", "SAS (Statistical Analysis System)", glossary tables and lists. Acronyms you use in several notes but never spell out are listed under **Needs a definition**.
- **Readings from your transcripts.** Notes that record how a recording was heard (`Readings used: FCRM ("FICRAM")`, `transcribed "YAG"`, `"GRD" is likely eGRC`) teach Daybook the variants. On ALPV that's over 100 terms and names.
- **Select a term anywhere** (a note, a slide, a PDF) to see what it means.
- **One-click fixes** for mishearings in a note ("FICRAM → FCRM"), only for distinctive variants or ones you added, and never inside the term itself ("Credit 360" stays as it is).
- The **local AI** gets the definitions and variants for the terms in front of it, and **local Whisper** gets your terms as its vocabulary, so new recordings come out spelled right.
- Your own definitions always win and are never overwritten. Hidden terms stay hidden after a rescan. Nothing from a locked note is used.

### Meeting prep
- A **Prep** button on events (and on Today's schedule) gathers the people, the last meeting in the series, open action items (theirs first), decisions, open questions, related decks and documents, and tasks. Track any follow-up as a task in one click, or ask your local model for a short brief.

### Search operators and saved searches
- `in:Kelly`, `type:deck`, `tag:sas` or `#sas`, `before:` / `after:` / `on:` (dates, months, years, `7d`), `is:pinned` / `is:private` / `is:highlighted`, `has:actions` / `has:decisions` / `has:highlights`, `person:Jacqueline`, `-word` and `"exact phrase"`. Filters show as chips; **Save search** puts it in the sidebar.

### Phone calendar feed
- A private `.ics` address your iPhone's Calendar app subscribes to. Choose full details, titles only or just "Busy", with or without tasks. It's only answered on this Mac, your tailnet or your home network, and **Reset link** retires the old address.

### Plan the day
- **Make time for it**: open a task and Daybook lists the open stretches on that day's calendar (8:00 to 18:00, around your meetings) for 30 minutes to 2 hours. Pick one and it's booked, linked to the task, with a reminder. The event can tick the task off.
- **Look back** on Today: what you wrote a week, a month, three and six months, and a year ago.

### Phone fixes
- No more zoom when tapping a field, no drifting off-centre afterwards, no gap under the tab bar on the home-screen app, and lists that fit the screen.
- Clicking or selecting text in a read-only document no longer tries to save it.

### Tests
- 64 tests (13 new): the slide renderer against real deck features, PDF and sheet pages, page search, highlights, locking, meeting prep, the phone feed (including refusing public addresses), search operators, the glossary and fixes, time blocking and Look back.

---

## Shipped in 2.1: the ALPV library and dates for everything

### Library
- Daybook lives in `ALPV/Daybook/` and reads the rest of ALPV **in place**. Nothing is copied out; top-level folders (`Notes`, `Jen`, `Kelly`, `PPTX`, `Mockups`) become notebooks.
- **Two-way Markdown sync.** Editing a `.md` note in Daybook writes the file; editing it anywhere else (VS Code, Claude, a `git pull`) updates Daybook within seconds. If both change at once, both versions are kept. Renaming a note renames the file; moving it to another folder moves the file.
- **Word, PowerPoint, PDF, Excel, email and text files** become read-only notes with *Open in Word/PowerPoint*, *Show in Finder* and *Download*. Embedded images and tables come along.
- **HTML mockups** get a live, sandboxed preview and a gallery.
- Files deleted outside Daybook go to Daybook's trash and come back if the file returns. Renames and moves are recognised by content, so a note keeps its tags, date and history.
- **Library page**: timeline, needs a date, decisions, follow-ups, people, mockups, and folder & git status (branch, unpushed commits, uncommitted files; read-only).
- Import can save files straight into an ALPV folder.
- New notes are saved as Markdown in `ALPV/Notes/` (or stay private, your choice).

### Dates
- **Date scrubber** reads file names (ISO dates, `2026-10-23_143032_000` recordings, `Nov17-18` ranges), "Meeting date" lines, the top of documents, email dates, document properties (ignoring template defaults), git history and modified times (ignoring pull/copy clusters). Agreeing clues reinforce each other; the result says how sure it is and why.
- Times come along: "recording started about 2:30 PM" puts the note at 2:30 PM on the day.
- **Needs a date** queue with one-click *Use this*, a date picker that shows every clue, *No date*, and *Ask local AI*. The local AI's pick is applied only if the date is backed by the file's own clues or text.
- Decisions live in `ALPV/.daybook/dates.json` so they travel with the files and other tools (and Claude) can read and write them.
- **`daybook-dates` command** (standard library only) for Claude, scripts or a terminal: `scan`, `explain`, `set`, `none`, `clear`, `list`.
- Notes written in Daybook land on the day you create them unless the title or text names another day.
- **Mentioned dates**: deadlines and dates a note refers to show as small markers on the calendar, with the sentences that mention them.

### Meeting intelligence (rules first, AI optional)
- **At a glance** card on meeting notes: people, decisions, action items with owners and due dates, open questions.
- **Decisions log** across every meeting, searchable.
- **Follow-ups**: every action item, filterable by owner ("Mine"), one click to track as a task.
- **People**: everyone in attendee lines and action owners, with meeting counts and a per-person timeline.
- **Recordings ↔ notes**: a transcript is linked to the meeting notes from the same day and time.
- **Versions and series**: decks like `…Deck_v5.1/5.2/5.3` are grouped, and **Compare** shows what changed (plus an optional AI summary of the changes). Recurring meetings with the same title are linked as a series.
- **Draft meeting notes from a recording** with your local model, in the same layout as your existing notes (details table, decisions, action items), saved next to the recording.
- **Week in review** (local AI) for any week on the timeline, saved as a note if you like.

### First run on ALPV
- All 72 files dated: 67 automatically (66 from file names, 1 from git history), 4 by Claude with the reason recorded in `.daybook/dates.json`, and the README marked as undated.
- 51 tests (15 new) cover the library, the date rules, the `daybook-dates` command and the local-AI paths.

---

## Shipped in 2.0

### Privacy and security
- Passphrase sign-in with scrypt hashing and 30-day device sessions. You can see signed-in devices and sign the others out.
- First-run setup from another device needs a one-time code from the terminal, so nobody else on your Wi-Fi can claim the app first.
- CSRF protection (custom header plus SameSite=Strict cookies) and login throttling.
- A strict Content-Security-Policy: the browser can only connect to your server.
- No CDNs. Fonts, icons, the editor and every script are self-hosted. The old app loaded Tailwind and Lucide from public CDNs on every visit.
- Remote images in notes are blocked, so imported emails can't load tracking pixels.
- **Locked notes**: AES-256-GCM with a separate vault passphrase. Locking also deletes the note's history, search text and AI index.
- **Encrypted exports**: `.daybook` files protected by a passphrase.
- Data lives in `~/Library/Application Support/Daybook`, outside the repo and outside iCloud-synced folders.
- **Privacy check-up page**: storage location, FileVault, connection type, AI locality, outside services and backups.
- Discord is now optional and off by default. Outside services get **redacted** reminders ("An event is coming up at 3:00 PM").
- Secrets (webhook URLs, tokens) are masked in the UI and API.

### Notes
- Rich editor (TipTap) that **stores Markdown**, so notes stay portable.
- `/` command menu, a selection toolbar, checklists, tables, code, highlights, and pasted or dropped images and files.
- Notebooks, tags, pins, colours, archive, and trash with 30-day auto-delete.
- `[[` links between notes, with backlinks.
- SQLite FTS5 full-text search with highlighted snippets.
- Automatic version history (a snapshot every 10 minutes and before big changes) with restore.
- Autosave. Edits from another device are detected and you choose which version to keep.
- Templates (meeting, one-on-one, project brief, incident review, decision record, weekly review) plus your own: tag any note `template`.
- Daily notes that start pre-filled with that day's schedule.
- Export a note as Markdown, or print / save it as PDF.

### Calendar
- Month (with a hand-drawn "today" circle), week and day time grids with overlap layout and a now-line, plus an agenda view.
- Repeating events (daily, weekdays, weekly on chosen days, every two weeks, monthly, yearly, ending never / on a date / after N times). Edit or delete "this", "this and following" or "all".
- Multi-day and all-day events.
- Calendars with colours and visibility toggles.
- Reminders that actually send, from the server even with no browser open. Missed reminders within 20 minutes still go out.
- `.ics` import (with time zones, repeats, exceptions and alarms) and export.
- One click from an event to its meeting notes.
- Fixed the old UTC date bug that made "today" become tomorrow every evening.

### Tasks and Today
- Tasks with due date and time, priority, lists, reminders, details and a link to a source note.
- Today, Upcoming, No date, All open and Done views.
- **Plain-language capture** that works without AI: "Standup every weekday 9:15am for 15m #work". The local AI can interpret anything unusual.
- A Today dashboard: the date ("Sheet 312 of 365"), schedule with a now marker, upcoming events, what's due, recent notes and an AI briefing.

### Document import
- Word (.docx, plus .doc, .rtf and .odt through macOS `textutil` or LibreOffice). Embedded images are kept.
- PDF, with scans read by on-device text recognition (OCR).
- PowerPoint (slides, bullets, tables, speaker notes) and Excel / CSV (sheets become tables).
- Email (.eml with attachments; .msg optional), HTML (sanitised), Markdown with front matter, and code / JSON / XML.
- Images (Apple Vision, Tesseract or a local vision model reads the text) and audio (mlx-whisper or faster-whisper).
- ZIP files and Obsidian vaults (folders become notebooks).
- **Watched folder** `~/Notes Inbox`: files saved there become notes automatically.
- The original file stays attached to every imported note.

### Local AI (Ollama, LM Studio, llama.cpp, MLX)
- A hard **local-only guard**: every request re-checks that the endpoint resolves to this machine, your LAN or your tailnet.
- Two-model routing: a fast model for tags, titles and parsing, a writing model for summaries and rewrites. Daybook picks automatically from what's installed.
- **Smart import filing**: title, summary, tags, action items and dates, either suggested or applied automatically.
- Note actions: summarize, action items → tasks, find dates → events, title and tags, clean up (structure, concise, grammar, meeting notes, transcript → notes), continue writing, ask this note.
- Selection rewrite: improve, shorten, expand, bullets, checklist, professional, plain language, or a custom instruction.
- **Ask your notes**: hybrid semantic plus keyword retrieval with numbered citations that link to notes. Answers can be saved as a note.
- **Draft a note** from a request, optionally grounded in your notes.
- A daily briefing, and related-note suggestions from embeddings.
- Reasoning ("think") output is filtered out, every answer shows the model and timing, and long documents are condensed in chunks so they fit.

### App and design
- New visual identity built on the engineer's computation pad: green gridded paper, pad-cover green, red pencil for attention, prussian blue for actions. Fonts are Archivo and Literata.
- Light and dark themes.
- Responsive: desktop sidebar layout and a mobile tab bar, built as an installable PWA.
- Command palette (⌘K), keyboard shortcuts and live sync between devices (Server-Sent Events).
- Automatic daily backups, ZIP export (Markdown + attachments + ICS + JSON) and restore.
- Imports data from the previous version automatically.
- A test suite with 36 tests and a mock local model.

---

## Planned

### Next up (highest value)
1. **Offline mode for the phone (M).** Keep recently opened notes readable offline, and queue edits that sync when you reconnect. *Why:* covers Tailscale outages and planes.
2. **Recurring tasks (S).** "Pay rent on the 1st every month" already parses; tasks just need a repeat rule. *Why:* finishes the plain-language capture.
3. **Drag to move or resize events (M)** in the week and day views. *Why:* the fastest way to reschedule.
4. **Share sheet import on Android, and an iOS Shortcut (S).** Send a PDF or web page from your phone straight into Daybook. *Why:* makes "documents become notes" work from anywhere.
5. **Voice memo pipeline (S).** Watch a voice-memo or recorder export folder, transcribe locally, then run "transcript → notes". *Why:* builds on the transcription engine that's already here.
6. **Full database encryption at rest (M).** SQLCipher, unlocked by your sign-in passphrase. *Why:* protects the database file even if FileVault is off.

### Notes
- Note-to-note graph view (M).
- Inline AI autocomplete while you type, off by default (M).
- Web clipper bookmarklet that saves the current page as a note, sanitised (S).
- Per-notebook sort order and nested notebooks (S).
- Export a note as `.docx`, the reverse of import (S).

### Calendar and tasks
- Subscribe to read-only calendar feeds (work or holiday `.ics` URLs) on your LAN or tailnet (M).
- Natural-language search for events: "meetings with Kelly in October" (S).
- Drag a task onto the week view to book time, and drag blocks to move them (M). *(Make time for it already finds and books open time.)*
- Weekly review that also covers finished tasks and slipped items (the library's *Week in review* already covers notes) (S).

### Library and dates (next)
- **Watch more folders**: add `~/Transcription` (or any recorder export folder) as a second library so new recordings are dated and turned into notes automatically (M).
- **Calendar events from meeting notes**: offer to create the event for a dated meeting note that has no matching calendar event (S).
- **Deck thumbnails** in lists and on the timeline, from the reader's slide renderer (S).
- **Reader for Word documents with their own layout** (headers, columns, page breaks), not just the converted text (M).
- **Glossary across the library**: suggest definitions for "Needs a definition" terms from your local model, citing the notes it read (S).
- **Commit from Daybook**: a reviewed "commit these notes" button for library changes (never push) (S).
- **Smarter people**: merge "Jen" and "Jennifer", and let you rename or hide a person (S).

### Local AI
- Per-feature model choice and prompt customisation in Settings (S).
- **Watch-folder rules**: "PDFs from *X* go to notebook *Y* with tag *Z*" (M).
- Local re-ranking for "Ask" results with a small cross-encoder (M).
- Notes as tools for your agentic harness: a small MCP or HTTP endpoint on the tailnet for your local agents (M).
- Model health panel: memory use, load time, tokens per second history (S).

### Privacy and operations
- **Passkeys / Touch ID sign-in** (WebAuthn) alongside the passphrase (M).
- Auto-lock after inactivity on shared devices (S).
- An audit log of sign-ins and exports (S).
- Encrypted off-site backup to a NAS or USB drive on a schedule (S).
- A one-command update script (`git pull` plus dependency refresh plus restart) (S).
- Docker image for running on a NAS or Linux box (S).

### Notifications
- Web Push with end-to-end encrypted payloads, for reminders on a locked phone without ntfy (M).
- A morning digest notification: today's events and tasks at a time you choose (S).
