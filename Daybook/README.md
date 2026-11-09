# Daybook

A private, self-hosted workspace for your **notes, calendar, tasks and documents**, with optional **local-only AI**. It runs on your own Mac (or any computer). You reach it from your phone over Tailscale. Nothing goes to anyone else's servers.

Daybook lives inside the **ALPV** repository (`ALPV/Daybook/`) and reads the rest of that folder as its **library**: every meeting note, deck, transcript and mockup in `Notes/`, `Jen/`, `Kelly/`, `PPTX/` and `Mockups/` shows up as a searchable note on the right day of the calendar. Nothing is copied out of the folder.

> Daybook started life as *Calendar-Notes-Discord*. See [ENHANCEMENTS.md](ENHANCEMENTS.md) for what changed and what's next, and [PRIVACY.md](PRIVACY.md) for exactly how your data is protected.

## What it does

- **Library (your ALPV folder).** Word documents, PowerPoint decks, PDFs, spreadsheets, transcripts, HTML mockups and Markdown notes are read **in place**. Each top-level folder is a notebook. Markdown notes sync both ways; other files are read-only copies with *Open in Word / PowerPoint* and *Show in Finder*. Files you add later are picked up within seconds.
- **Dates for everything.** A date scrubber reads file names (`…_Notes_2026-10-23.docx`, `2026-10-23_143032_000.txt`, `…_Nov17-18_…pptx`), "Meeting date" lines, the top of each document, document properties and git history, and files each item on its day, with the meeting time when it can find one. Anything it isn't sure about waits in **Needs a date**, where you, your local AI or Claude decide. Decisions are saved next to the files in `.daybook/dates.json`.
- **Meeting intelligence, no AI needed.** Attendees, decisions, action items and open questions are read from your meeting notes. That gives you a **decisions log**, **follow-ups** you can turn into tasks, a **people** index, recordings linked to the notes made from them, and deck **versions** you can compare.
- **Reader for decks, PDFs and spreadsheets.** PowerPoint files are drawn as real slides (layouts, theme colours, shapes, tables, charts, pictures, speaker notes), PDFs page by page, and spreadsheets as tables, all inside Daybook, offline, with no Office install or cloud viewer. Highlight and comment, find in a document, present full screen with your speaker notes, and save your highlights as a note. Search looks **inside** every deck and PDF and opens the right slide or page.
- **Glossary.** Daybook reads your notes for what acronyms stand for ("Data private cloud (DPC)", glossary tables) and for how transcripts mishear them (`Readings used: FCRM ("FICRAM")`). Select a term anywhere to see its meaning, fix mishearings in a note with one click, and your local AI and local transcription get the right spellings.
- **Meeting prep.** One click on an event gathers the people, last meeting, open action items, decisions, questions and decks that relate to it, plus an optional brief from your local model.
- **Search operators and saved searches.** `in:Kelly type:deck after:2026-10 person:Jacqueline has:actions -draft "storage costs"`, saved to the sidebar.
- **Phone calendar.** A private calendar address your iPhone's Calendar app can subscribe to. It only answers on this Mac, your tailnet or your home network, and can show full details, titles only or just "Busy".
- **Make time for tasks.** Daybook finds open stretches on your calendar for a task and books the one you pick. **Look back** on Today shows what you wrote a week, a month and a year ago.
- **Notes.** A rich editor that saves notes as plain Markdown. Includes checklists, tables, images, notebooks, tags, `[[links]]` between notes with backlinks, full-text search, version history, trash, templates and daily notes.
- **Calendar.** Month, week, day and agenda views. Repeating events with "this / following / all" edits. Reminders that actually fire. `.ics` import and export.
- **Tasks.** Plain-language capture ("Send cost deck Friday !high #work"), due dates, priorities, lists and reminders.
- **Today.** Your date, schedule, what's due and recent notes, plus a box that understands "Lunch with Kelly tomorrow 12:30".
- **Import documents.** Word, PDF, PowerPoint, Excel, CSV, email, HTML, Markdown, images and audio become notes. Use the upload page, or just save files into the `~/Notes Inbox` folder. The original file stays attached to each note.
- **Local AI (optional).** Works with [Ollama](https://ollama.com) or [LM Studio](https://lmstudio.ai). It can:
  - summarize notes
  - pull out action items, then create tasks from them
  - find dates and add them to the calendar
  - suggest titles and tags
  - clean up messy notes or transcripts
  - rewrite a selection
  - draft notes
  - answer questions about your notes with citations ("Ask")

  The server **refuses** to talk to any AI endpoint on the public internet.
- **Private by design:**
  - Sign-in passphrase.
  - Encrypted locked notes.
  - Encrypted exports.
  - A strict browser lockdown, so the page can only talk to your server.
  - No CDNs, analytics or telemetry.
  - A built-in **privacy check-up** page.

## Quick start (macOS)

Requires Python 3.11 or newer (3.14 works).

Double-click **`Start Daybook.command`** at the top of the ALPV folder, or:

```bash
cd ~/Documents/GitHub/ALPV/Daybook
./start.sh
```

`start.sh` creates a `.venv`, installs the requirements and starts the server. Open **http://localhost:8000** on the Mac and choose a passphrase. No setup code is needed on the Mac itself. The first start also reads the whole ALPV folder (about a minute for ~70 documents); the Library page shows progress.

Your data lives in `~/Library/Application Support/Daybook`, outside the repo and outside iCloud-synced folders. Override the location with `--data /path` or the `DAYBOOK_DATA` environment variable.

### Already used the old version?

Open Daybook in the same browser you used before. It will offer to bring over the notes and events that were stored in that browser, and it skips the sample notes. You can also restore an old `calendar_notes_backup_*.json` from **Settings → Backups & data → Restore**.

## Use it from your phone (Tailscale)

1. Run Tailscale on the Mac and on your phone.
2. Open the `Over Tailscale` address printed in the terminal (for example `http://100.x.y.z:8000`). The first time, enter the **setup code** shown in the terminal, or set up from the Mac first.
3. **Recommended: turn on HTTPS.** Enable MagicDNS and HTTPS certificates in the Tailscale admin console, then run:

   ```bash
   tailscale serve --bg 8000
   ```

   Now use `https://<your-mac>.<your-tailnet>.ts.net`. With HTTPS, the app installs properly to your home screen (Safari → Share → Add to Home Screen), opens offline, and can show reminder notifications. Daybook detects that it's behind the Tailscale proxy.

   Tailscale's certificates are public records, so your machine and tailnet names appear in certificate logs. Your traffic and data don't.

   With HTTPS through `tailscale serve`, you can make Daybook unreachable from your Wi-Fi entirely by running `./start.sh --localhost-only`. Tailscale forwards to it locally.

   If another app on the Mac already uses Tailscale's default HTTPS port, give Daybook its own: `tailscale serve --bg --https=8443 8000`, then open `https://<your-mac>.<your-tailnet>.ts.net:8443`.

4. **Optional: see Daybook events in your phone's Calendar app.** In **Settings → Calendar → Phone calendar feed**, turn it on and copy its address. On the iPhone go to **Settings → Apps → Calendar → Calendar Accounts → Add Account → Other → Add Subscribed Calendar** (on older iOS: Settings → Calendar → Accounts) and paste it. Add it on the phone itself, not through iCloud on the Mac: iCloud would fetch it from Apple's servers, which can't (and shouldn't) reach Daybook.

Tailscale traffic is end-to-end encrypted. Plain `http://192.168…` on your Wi-Fi is not, and the privacy check-up will tell you so.

## Local AI setup

1. Install [Ollama](https://ollama.com) (or LM Studio with its local server turned on).
2. Pull models that fit your Mac's memory. For example:

   ```bash
   ollama pull qwen3:14b          # writing model: summaries, rewrites, answers
   ollama pull qwen3:4b           # quick model: titles, tags, parsing
   ollama pull nomic-embed-text   # embeddings: "Ask your notes" and related notes
   ```

   With 48–64 GB of memory you can use a bigger writing model, such as `qwen3:30b-a3b` or `gemma3:27b`.
3. Open **Settings → Local AI**, press **Test**, and pick models (or let Daybook choose).

Ollama on another machine works too, as long as it's on your LAN or tailnet (for example `http://100.x.y.z:11434`). Public addresses are blocked, and no setting overrides that.

How AI is used:
- It runs at specific moments: filing an imported document, after a meeting, or at the start of your day.
- Every result is a suggestion with **Apply / Insert / Create tasks** buttons. Nothing changes until you choose.
- Locked notes are never sent to the model.
- Answers show which model produced them and how long it took.

## Your library (ALPV)

Daybook finds the library automatically because it lives inside it (the folder above `Daybook/` has a `.daybook/library.json` marker). To use a different folder, set it in **Settings → Library folder** or start with `DAYBOOK_LIBRARY=/path/to/folder ./start.sh`.

| In the folder | In Daybook |
| --- | --- |
| `Notes/`, `Jen/`, `Kelly/`, `PPTX/`, `Mockups/` | Notebooks in the sidebar under **ALPV** |
| `*.md` | Editable notes. Edits are written back to the file; edits made elsewhere show up within seconds. If both change at once, nothing is lost: your Daybook version is saved as `<name> (Daybook copy …).md`. |
| `*.docx`, `*.pptx`, `*.pdf`, `*.xlsx`, `*.txt`, `*.eml` | Read-only notes with the converted text, images and tables. *Open in Word/PowerPoint* edits the original; Daybook re-reads it when you save. |
| `*.html` | A live, sandboxed preview of the mockup (scripts run; it can't reach the internet or Daybook). |
| `.daybook/dates.json` | Dates chosen by you, the local AI or Claude. Safe to commit. |
| `.daybook/trash/` | Files you delete from Daybook (restore them from Daybook's trash). Ignored by git. |

New notes you write in Daybook are saved as Markdown in `ALPV/Notes/` (change this in Settings, or pick **Daybook only (private)**). Locked notes always stay private, in Daybook's database.

### How dates are picked

Every clue gets a score, and clues that agree reinforce each other:

| Clue | Example | Score |
| --- | --- | --- |
| Front matter | `date: 2026-10-23` | 0.99 |
| File name | `SAS_Workspaces_Notes_2026-10-23.docx`, `2026-10-23_143032_000.txt`, `Workshop_Nov17-18.pptx` | 0.95 (0.8 without a year) |
| A labelled line | `Meeting date: Friday, October 23, 2026 (recording started about 2:30 PM)` | 0.92 |
| The top of the document | `October 19, 2026 · Jake` | 0.8 |
| Document properties | Word/PowerPoint "created" (ignored when it's a template default shared by several files) | 0.45 |
| Git | When the file was first committed | 0.5 |
| Modified time | Ignored when many files share it (a pull or a copy) | 0.35 |

At 75% or more the item goes on the calendar. Between 40% and 75% it's a suggestion in **Library → Needs a date**. A wrong weekday in a document never moves a date a year away, version numbers (`v6.2`) and IDs (`Model_14779`) aren't mistaken for dates, and other dates a document mentions (deadlines, a cutover) are shown as small markers on the calendar.

### For Claude, scripts and you: `daybook-dates`

The scrubber also runs on its own, with the system Python and no Daybook server:

```bash
cd ~/Documents/GitHub/ALPV
python3 Daybook/daybook-dates scan              # files that need a date
python3 Daybook/daybook-dates scan --all --json # everything, for scripts and agents
python3 Daybook/daybook-dates explain "Kelly/SAS_Workspace_Owner_Kickoff.pptx"
python3 Daybook/daybook-dates set "Kelly/SAS_Workspace_Owner_Kickoff.pptx" 2026-10-23 --by claude --note "Committed with the kickoff brief"
python3 Daybook/daybook-dates none README.md
```

Decisions go to `.daybook/dates.json`; a running Daybook picks them up within seconds.

## Importing documents

| How | What happens |
| --- | --- |
| **Import page** | Drag in files. With a library connected, **Save to: ALPV/Notes** copies them into the folder (dated and read in place); **Daybook only** keeps a private copy instead. ZIPs of Markdown files (an Obsidian vault) work too. |
| **Watched folder** `~/Notes Inbox` | Save or drop files there. They become notes and move into `Imported/`. |
| **Inside a note** | Drop or paste images and files to attach them. |
| **Calendar** | `.ics` files add events. |

Optional extras, all on-device:

```bash
.venv/bin/pip install -r requirements-optional.txt
```

- `pyobjc-framework-Vision` reads text in screenshots, photos and scanned PDFs using Apple's on-device engine.
- `mlx-whisper` transcribes audio and voice memos on Apple Silicon.
- `extract-msg` imports Outlook `.msg` emails.

Older `.doc`, `.rtf` and `.odt` files are converted with macOS's built-in `textutil`.

## Keep it running (always on)

```bash
scripts/install-launchd.sh                        # starts at login, restarts if it stops
scripts/install-launchd.sh 8000 --localhost-only  # same, but only reachable via tailscale serve
scripts/uninstall-launchd.sh      # removes it
```

Logs go to `~/Library/Logs/Daybook/daybook.log`, which is also where the setup code appears.

If the repo is inside `~/Documents`, macOS may ask to allow Python to access that folder the first time. Allow it in System Settings → Privacy & Security → Files and Folders.

## Backups

- **Automatic:** a daily snapshot of the database goes to `<data>/backups` (14 are kept by default).
- **Export:** Settings → Backups & data gives you a ZIP of Markdown files, attachments, `calendar.ics` and `daybook.json`. You can also make an **encrypted** `.daybook` export protected by a passphrase.
- Time Machine covers the data folder, and FileVault encrypts it at rest.

## Keyboard shortcuts

| Keys | Action |
| --- | --- |
| `⌘K` / `Ctrl+K` or `/` | Search and commands |
| `N` | Add something (event, task or note) |
| `G` then `T` `C` `N` `L` `K` `I` `A` `S` | Go to Today, Calendar, Notes, Library, Tasks, Import, Ask, Settings |
| `/` in a note | Insert block (heading, checklist, table, image…) |
| `[[` in a note | Link to another note |
| `⌘F`, `←` `→`, `P` in the reader | Find in the document, previous / next slide or page, present |
| `S` or `N` while presenting | Show or hide speaker notes |

## Project layout

```
run.py                 Start the server (prints addresses and the setup code)
start.sh               Create .venv, install, run
daybook-dates          The date scrubber as a command (standard library only)
app/
  main.py              FastAPI app, security middleware (auth, CSRF, CSP)
  db.py                SQLite + FTS5 schema, migrations, settings
  security.py          Passphrase hashing, sessions, AES-GCM encryption
  routers/             HTTP API (notes, calendar, tasks, import, ai, settings…)
  services/            notes, events (recurrence, ICS), tasks, converters, OCR,
                       transcription, local AI client and prompts, embeddings,
                       scheduler (reminders, inbox, backups), notify, backup,
                       library (the ALPV folder: sync, trash, versions),
                       datescan (the date scrubber), meeting (decisions, actions, people),
                       slides (draws .pptx as HTML), reader (PDFs, sheets, highlights,
                       page search), prep (meeting prep), glossary (terms and readings)
  static/              The web app (Preact + htm, no build step) and
                       self-hosted vendor bundles, fonts and icons
tools/                 Rebuilds app/static/vendor (TipTap editor etc.) — optional
tests/                 pytest suite with a mock Ollama server
scripts/               launchd install/uninstall
```

The frontend has **no build step**. Edit files in `app/static/js` and reload. The editor bundle and other vendored libraries are prebuilt into `app/static/vendor`. To upgrade them, run `cd tools && npm install && npm run build`.

## Tests

```bash
.venv/bin/pip install pytest
.venv/bin/python -m pytest
```

The suite covers:
- auth and CSRF
- notes, locking and history
- recurrence and `.ics`
- every import format
- the local-only AI guard and the AI features (against a mock model)
- reminders and redaction
- export, restore, and migration from the old version
- the library: two-way Markdown sync, conflicts, renames, moves, trash, safe file serving
- the date scrubber against real file-name patterns, labelled dates, templates, git history and `dates.json`
- the `daybook-dates` command
- the reader: slides drawn from real `.pptx` features, PDF and sheet pages, page-level search, highlights, and what locking removes
- meeting prep, the phone calendar feed (and that it refuses public addresses), search operators and saved searches
- the glossary (definitions and readings found by rule, your own terms, "sounds like" fixes), making time for tasks and Look back

## License

MIT.
