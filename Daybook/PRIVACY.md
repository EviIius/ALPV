# Privacy model

Daybook is built to keep your notes, calendar and documents on hardware you control. This page explains exactly where data lives, where it travels, and what is and isn't protected.

## Where your data lives

| Data | Location | Protection |
| --- | --- | --- |
| Notes, events, tasks, settings | `~/Library/Application Support/Daybook/daybook.db` (SQLite) | macOS FileVault (check it's on: Settings → Privacy check-up). The folder is owner-only (`chmod 700`). |
| Locked notes | Same database, as AES-256-GCM ciphertext | Your vault passphrase via scrypt. Plain text exists only in memory while a note is open. History, search text and AI index are deleted when you lock. |
| Attachments and imported originals | `…/Daybook/attachments/` | FileVault |
| Daily snapshots | `…/Daybook/backups/` | FileVault |
| Reader cache (slides drawn from decks, their images) | `…/Daybook/cache/reader/` | FileVault. Rebuilt on demand; old entries are pruned. Locked notes are never drawn or cached. |
| Highlights, page text for search, glossary | Same database | FileVault. Locking a document deletes its highlights and page text; definitions found only in it leave the glossary. |
| Encrypted exports (`.daybook`) | Wherever you save them | AES-256-GCM with the passphrase you choose |
| Your sign-in passphrase | Never stored | Only an scrypt hash is kept |
| Session cookies | Your browser | HttpOnly, SameSite=Strict, and Secure over HTTPS. Only a SHA-256 of each token is stored on the server. |

The data folder is deliberately **outside the repository** (so it's never committed) and **outside Desktop/Documents** (which iCloud may sync).

## Your library folder (ALPV)

Daybook reads the files in your library folder **in place** and never sends them anywhere. Know these points:

- **The folder is a git repository with a GitHub remote.** Anything saved into it, including Markdown notes you write in Daybook's library notebooks and `.daybook/dates.json`, goes to GitHub the next time *you* push. Daybook never commits or pushes. If a note shouldn't leave this Mac, put it in a **private notebook** (Settings → Library folder → New notes go to → Daybook only), or lock it. Locked notes can't live in the library at all.
- **Converted text, images and the search index** for library files are stored in Daybook's own database (`~/Library/Application Support/Daybook`), not in the repository.
- **Mockups are sandboxed.** HTML files open in an iframe with no access to Daybook (a separate origin, no cookies) and a policy that blocks all network requests. A mockup that loads Google Fonts or a CDN script simply falls back to local fonts; nothing is fetched.
- **Files are served only from the library's own list.** Paths outside the folder, hidden folders (`.git`, `.daybook`) and anything Daybook didn't index can't be requested.
- **Opening files in Word or Finder** only works when you're using Daybook on the Mac itself.
- **Git is only read.** Daybook runs `git log` and `git status` with `--no-optional-locks`, so it never takes git's locks or changes your repository.
- **The reader draws decks and PDFs itself.** Slides are built from the `.pptx` file on this Mac (with a parser that refuses external entities and network lookups), PDFs are shown with a bundled copy of PDF.js, and spreadsheets are read locally. No file is sent to an online viewer, and pictures from a deck are served with a policy that blocks scripts.
- **"Save highlights as a note"** writes a Markdown note into the same folder as the document when that folder is in your library (so it's included when you push). Keep a document's highlights private by keeping the document in a private notebook.

## What leaves your computer

**By default, nothing.** Specifically:

- **No CDNs.** Fonts, icons, scripts and the editor are served by Daybook itself. The Content-Security-Policy (`connect-src 'self'`, `script-src 'self'`) means the page *cannot* contact other hosts, even if someone injected code into a note.
- **No analytics or telemetry.** There is no tracking code of any kind.
- **Local AI only.** Before every AI request the server resolves the endpoint's hostname and checks that every address is loopback, private LAN (10/8, 172.16/12, 192.168/16), link-local or Tailscale (100.64/10, fd7a:115c:a1e0::/48). Anything else is refused with an error. No setting turns this off, and the client also ignores system proxies for AI traffic.
- **Locked notes are never sent to AI**, even a local one.
- **Dating files with AI** sends only the file name, title, the clues found by rules and the first ~3,500 characters, and only to your local model. AI picks are applied only when the date also appears in the file's own clues or text.
- **Remote images are blocked** when notes are displayed, so imported emails and web pages can't load tracking pixels.
- **The glossary is built by rules on this Mac.** Your local AI and local transcription receive only the terms that matter for the material in front of them. When you ask for a suggested definition, the local AI is shown short passages from unlocked notes that use the term, and nothing else.

### Optional things that do leave your computer

| Feature | Off by default? | What's sent |
| --- | --- | --- |
| ntfy push | Yes | With **Hide details** on (the default), only "An event is coming up at 3:00 PM". Self-host ntfy on your tailnet to keep even that private. |
| Discord webhook | Yes | Same redacted text unless you turn redaction off. |
| Model downloads (`ollama pull`, mlx-whisper) | You run them | Model weights are downloaded. None of your data is uploaded. |
| Python packages (`pip`) | On first start, and when `requirements.txt` changed and you start or restart Daybook | Package names are sent to PyPI and the packages are downloaded. None of your data is uploaded. The rehearsal before an in-app restart copies your database to Daybook's own `tmp` folder and deletes the copy when it's done. |
| Tailscale HTTPS certificate | You run `tailscale serve` | Your machine and tailnet names appear in public certificate logs. No content does. |
| Phone calendar feed | Yes | Your events (full details, titles only, or just "Busy", your choice) to whoever has the feed's secret address. Daybook only answers it on this Mac, your tailnet or your home network, never the public internet, and it isn't cached by the app. Subscribe on the phone itself: a calendar added through iCloud would be fetched by Apple's servers. **Reset link** makes the old address stop working; turning the feed off stops it completely. |

## On the network

- **On the Mac itself** (`localhost`): traffic never leaves the machine.
- **Over Tailscale**: WireGuard encrypts traffic end to end between your devices.
- **Over plain Wi-Fi** (`http://192.168…`): **not encrypted**. Anyone on the same network could read it. The privacy check-up warns you. Use Tailscale or `tailscale serve` (HTTPS) instead.
- Setting up from another device requires the one-time code printed in the server's terminal. Failed sign-ins are throttled with growing delays.
- State-changing requests require a custom header that other websites can't send, which blocks cross-site request forgery.

## What Daybook doesn't protect against

- Someone with your unlocked Mac account (they can read the database, though not locked notes).
- Malware running as your user.
- A forgotten passphrase. There is no recovery for the sign-in passphrase, the vault passphrase or encrypted exports. That's the trade-off for nobody else holding a key.
- Plain-HTTP access over untrusted networks (see above).

## Checking for yourself

- **Settings → Privacy & security** runs a live check-up.
- In the browser's network inspector, every request goes to your Daybook address.
- `tests/test_daybook.py` includes tests that fail if any CDN URL appears in the frontend, or if a public AI endpoint is ever accepted.
