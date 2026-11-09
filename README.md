# ALPV

Working notes, decks, recordings and mockups, plus **Daybook**: a private notes, calendar and tasks app that reads this folder in place and puts every file on the day it belongs to.

| Folder | What's in it |
| --- | --- |
| `Notes/` | Meeting notes and working files |
| `Jen/`, `Kelly/` | Notes, decks and recordings with Jen and Kelly |
| `PPTX/` | Deck versions |
| `Mockups/` | HTML mockups |
| `Daybook/` | The Daybook app ([README](Daybook/README.md)) |
| `.daybook/` | Daybook's notes about this folder: `library.json` marks it, `dates.json` records the day chosen for files whose names don't say |

## Open Daybook

Double-click **`Start Daybook.command`**, then open <http://localhost:8000>. The first start installs dependencies and reads every file (a minute or two). Daybook keeps its own database outside this repo, in `~/Library/Application Support/Daybook`.

## Names that date themselves

Daybook reads dates from file names, so these land on the right day automatically:

- `Topic_Notes_2026-10-23.docx` (meeting notes)
- `2026-10-23_143032_000.txt` (a recording, at 2:30 PM)
- `Workshop_Nov17-18_DRAFT.pptx` (a two-day event)

A "Meeting date" line at the top of a document works too. Anything else shows up in Daybook under **Library → Needs a date**.

## Dating files from a terminal (or with Claude)

```bash
python3 Daybook/daybook-dates scan                 # what still needs a date
python3 Daybook/daybook-dates explain FILE         # every clue, with scores
python3 Daybook/daybook-dates set FILE 2026-10-23 --by claude --note "why"
```

Choices are saved in `.daybook/dates.json`, which is safe to commit.

> This repo has a GitHub remote. Notes you write in Daybook's library folders are files here, so they're pushed when you push. Keep anything that shouldn't leave this Mac in a private Daybook notebook.
