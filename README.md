# Echoes

> The voices of books and ideas, returning in rhythm.

A single-user personal knowledge resurfacing system. Quotes you've saved in
Notion get shuffled into a prepared playlist and delivered three at a time,
once a day, over WhatsApp. It is not a SaaS, not multi-tenant, and not built
for scale — it is built to be quiet, dependable, and boring.

```
Notion          the bookshelf
Echoes          the librarian who prepares reading slips
Playlist        a shuffled deck of quote cards
WhatsApp        a daily envelope
Sunday          restocking day
Exhaustion      reshuffle the deck
Failure         the librarian leaves a note explaining what happened
```

---

## How it works

**Two sources, one pattern.** Both are a filtered database query in Notion;
every "callout" block (the highlighted box you get from typing `/callout`)
in a matching page's body becomes one quote.

| | Books pool | Standalone pool |
|---|---|---|
| Database | `Books! Books! Books!` | `The Me Section` |
| Filter | Status = Completed **and** Completion Date ≥ 2024-04-24 | Tags contains `Quote` |
| Quotes per day | 2 | 1 |
| Message | quote carries the book title | quote is bare |

To add another standalone quotes page, tag it `Quote` in Notion. The next
weekly refresh picks it up automatically — no code change needed.

**Calendar-keyed playlist.** When the playlist is built, every date gets its
own bundle of quotes assigned up front. Each day's run just reads that date's
bundle — so a skipped or delayed run leaves no drift to recover from. There is
no counter that could fall out of step, only a calendar.

**Independent pools.** Books and standalone quotes drain at different speeds
and don't share a horizon. Each one assigns dates forward until its own
quotes run out, then reshuffles and rebuilds *on its own*, without touching
the other. A 40-quote standalone pool might recycle twice while a 180-quote
books pool is still on its first pass — that's intended: no book quote gets
skipped just because you added personal quotes faster than you finish books.

**Randomness happens once**, at the moment a pool is built or topped up.
Once a date has been assigned its quotes, that assignment never changes.

### What a message looks like

```
1. Love wins. Love always wins. - Tuesdays With Morrie
2. Learn to detach. - Tuesdays With Morrie
3. If you are not busy being born, you are busy dying. — Bob Dylan
```

Lines 1–2 are book quotes and carry their titles. Line 3 is the standalone
quote; if you wrote an attribution beneath it in Notion (like "— Bob Dylan"),
it gets folded onto the same line. On a day when the standalone pool has
nothing at all, all three lines fall back to book quotes and all three carry
titles.

### The Sunday refresh

Once a week (Sunday by default), Echoes checks Notion for anything new and
appends it to the playlist — *after* that day's message has already gone out,
so a refresh failure can never block delivery.

New-quote detection compares Notion's internal block IDs (a permanent ID
every block gets, invisible in the UI) against a record of every ID Echoes
has ever scheduled. This one mechanism catches three different cases: a
newly completed book, a newly tagged quotes page, and new quotes added to a
page that already existed. Because that record never shrinks or resets, a
missed refresh — say you didn't run Echoes for two weeks — is caught up
automatically the next time it runs, with nothing lost.

Appending only ever adds to the future. The one exception: if the very last
day of the current schedule doesn't have a full three quotes (because the
pool didn't divide evenly) *and that day hasn't happened yet*, new quotes top
it up first. Today and every day before it are never touched.

### Failure safety

**The message goes out even when preparation fails.** If Notion is
unreachable when Echoes tries to check for new quotes, it falls back to
whatever playlist is already saved on disk, delivers today's quotes from
that, and sends a separate alert explaining what went wrong. A broken Notion
connection should never mean a missed day.

One consequence worth knowing: if Notion is down on a day whose quotes were
already scheduled in advance, you won't see any error at all — Echoes simply
doesn't need to call Notion that day. That's the resilience working as
intended, not a bug being missed.

---

## Getting started

### 1. Install

```bash
./setup_env.sh              # creates the conda environment, installs Echoes, scaffolds .env
conda activate echoes
```

This uses [conda](https://docs.conda.io/) to create an environment named
`echoes` (see `environment.yml`), installs the project into it in editable
mode, and copies `.env.example` to `.env` if you don't already have one.

### 2. Connect Notion

Echoes reads from Notion using an **internal integration** — a token scoped
only to the databases you explicitly share with it.

1. Go to [notion.so/my-integrations](https://www.notion.so/my-integrations)
   and create a new integration (read-only capability is enough). Copy the
   secret it gives you — it starts with `ntn_`.
2. Open each of your two databases (`Books! Books! Books!` and
   `The Me Section`) in Notion, click the `•••` menu, and add your new
   integration under **Connections**. Skipping this step doesn't produce an
   error — Notion just returns an empty result, which looks identical to
   "no quotes yet" and can cost you an hour of confusion.
3. Open each database as a full page and copy its ID out of the browser URL:
   `notion.so/<workspace>/<DATABASE_ID>?v=<view_id>` — the 32-character
   string right after the workspace name.

Put all three values in `.env`:

```
NOTION_API_KEY=ntn_...
NOTION_BOOKS_DATABASE_ID=...
NOTION_ME_SECTION_DATABASE_ID=...
```

That's enough to run everything except actual WhatsApp delivery — see the
[WhatsApp section](#whatsapp-delivery) below when you're ready for that.

### 3. First run

Run these in order the first time, so each one confirms the previous step
before you move on:

```bash
echoes collect          # read-only: prints every quote Echoes can see, touches no files
echoes run --dry-run     # does everything else, but sends nothing and saves nothing
echoes run               # the real thing (prints to your terminal by default, doesn't send WhatsApp yet)
echoes show               # prints whatever is scheduled for today, straight from the saved playlist
```

Start with `echoes collect`. It's the fastest way to confirm your Notion API
key, database IDs, and filters are all correct — including whether
attributions are being folded in properly — before any scheduling or state
gets involved.

---

## Every command, in one place

### The `echoes` CLI

| Command | What it does | Touches Notion? | Writes state/sends messages? |
|---|---|---|---|
| `echoes collect` | Prints both quote pools as Echoes currently sees them in Notion | Yes | No — read-only |
| `echoes run` | The real daily run: prepares the playlist, delivers today's quotes, runs the weekly refresh if it's the configured day | If needed | Yes |
| `echoes run --dry-run` | Same as `echoes run`, but nothing is sent and nothing is saved to disk | If needed | No |
| `echoes refresh` | Runs just the weekly refresh step on its own, without a daily delivery | If needed | Yes |
| `echoes show` | Prints what's scheduled for a date, straight from the saved playlist | No | No |
| `echoes show --date 2026-09-01` | Same, for a specific date instead of today | No | No |

Every command accepts `--log-level DEBUG` (or `INFO`/`WARNING`/`ERROR`) to
override how much detail gets printed, and `--dry-run` works on any command
that would otherwise write or send something.

Exit codes, if you're scripting around it: `0` success, `1` completed but
something needed an alert, `2` fatal error.

### Development commands

```bash
pytest                       # runs the full test suite (network-free — nothing here calls real Notion)
ruff check src tests         # lints the code for style and common mistakes
```

### Preview real quotes without touching the saved playlist

`scripts/dump_quotes.py` is a small, separate helper — not part of the
`echoes` CLI, not run in production, and not covered by the test suite (it
calls real Notion, and the test suite deliberately never does that). Its only
job is to let you *see* what would be scheduled, using your real Notion data,
without disturbing anything already saved:

```bash
python scripts/dump_quotes.py                          # today onward, for as far as the playlist currently reaches
python scripts/dump_quotes.py --days 7                  # today plus the next 6 days
python scripts/dump_quotes.py --start 2026-09-01 --days 14
```

It writes the result to `quotes_snapshot.json` in the project root — open it
in any editor to read exactly what's scheduled for each date. It behaves like
`echoes run --dry-run`: it may call Notion to build or check a pool, but it
never writes to `state/` and never sends anything, so it's safe to run as
often as you like. The file is gitignored — it's a throwaway snapshot for
your own eyes, regenerated fresh every time you run the script, not something
the project tracks.

---

## Configuration

Every setting is an environment variable. Locally they're read from `.env`;
on GitHub Actions the exact same names come from repository secrets instead.
Both paths end up in the same place (`os.environ`), so the application code
can't tell — and there's no code path anywhere that reads a secret from a
file that could accidentally get committed.

See `.env.example` for the complete, commented list. The ones you're most
likely to touch:

| Variable | Default | What it means |
|---|---|---|
| `DELIVERY_MODE` | `console` | `console` prints the message to your terminal; `whatsapp` actually sends it |
| `QUOTES_PER_DAY_BOOKS` | `2` | book quotes per day |
| `QUOTES_PER_DAY_STANDALONE` | `1` | standalone quotes per day — set to `0` to disable that pool entirely |
| `QUOTES_PER_DAY_BOOKS_FALLBACK` | `3` | how many book quotes to send on a day the standalone pool is completely empty |
| `TIMEZONE` | `Asia/Kolkata` | which timezone "today" and the daily schedule are measured in |
| `RANDOM_SEED` | *(unset)* | set to a number to make shuffling reproducible (useful for testing); leave unset in real use so shuffles are genuinely random |
| `DRY_RUN` | `false` | do everything except send and save — same effect as `--dry-run` on the CLI |
| `REFRESH_WEEKDAY` | `6` | which day runs the weekly refresh (`0` = Monday … `6` = Sunday) |

---

## WhatsApp delivery

**Not required to start** — everything above works with `DELIVERY_MODE=console`,
which just prints the message instead of sending it. This section is for when
you're ready to actually receive quotes on WhatsApp.

### Why it needs more than an API key

Echoes never *receives* a WhatsApp message from you, so there's never an open
conversation window the way there is when you message a business first.
Every message Echoes sends is "business-initiated," and Meta (WhatsApp's
parent company) requires every business-initiated message to use a
pre-approved **template** — a fixed message shape with blanks Meta has
reviewed in advance. That means two templates need approval before this
works at all: one for the daily quotes, one for the failure alert.

### Step-by-step setup

**1. Create a Meta app.**
Go to [developers.facebook.com](https://developers.facebook.com), log in,
and under **My Apps → Create App** choose the **Business** type. Give it any
name (e.g. `echoes`).

**2. Add the WhatsApp product.**
In the app dashboard, find **WhatsApp** and click **Set up**. This
automatically creates a free, Meta-owned **test phone number** for you — you
don't need to register your own number or verify a business to start.

**3. Get your phone number ID and API version.**
On the **WhatsApp → API Setup** tab, copy the **Phone number ID** shown under
"From" — that's `WHATSAPP_PHONE_NUMBER_ID`. The sample request on that same
page shows the current API version (e.g. `v21.0`) — that's
`WHATSAPP_API_VERSION`.

**4. Add and verify recipients.**
Still on that page, under "To," add each phone number that should receive
quotes and verify it with the code WhatsApp sends. The free test tier allows
up to 5 verified numbers — plenty for personal use. These become
`WHATSAPP_RECIPIENT_NUMBERS`: one or more numbers, comma-separated,
international format with no `+` and no spaces (India = `91XXXXXXXXXX`). The
same message goes to every number in the list independently — this fans a
message out to individuals, it is **not** a WhatsApp group.

**5. Create the two templates.**
Go to **WhatsApp Manager → Message Templates** and create:

- **`echoes_daily_quotes`** — category **Utility**, body:
  ```
  1. {{1}}
  2. {{2}}
  3. {{3}}
  ```
  (three separate variables, because template parameters can't contain line
  breaks — the template itself supplies the line breaks between them)
- **`echoes_alert`** — category **Utility**, body: `{{1}}`

Both need a sample value per variable to submit for review. Approval
typically takes minutes to about a day. Note the exact language code you
picked (e.g. `en`) — it has to match exactly in `.env` later.

**6. Generate a permanent access token.**
The token shown on the API Setup page expires in 24 hours — fine for a quick
test, useless for a job that runs unattended once a day. For a real token:
go to [business.facebook.com/settings](https://business.facebook.com/settings)
→ **Users → System Users** → add a system user → assign it your app and
WhatsApp Business Account → **Generate new token**, selecting the
`whatsapp_business_messaging` and `whatsapp_business_management` permissions
and an expiration of **Never**. That's `WHATSAPP_ACCESS_TOKEN`.

**7. Fill in `.env` and switch delivery mode on.**

```
DELIVERY_MODE=whatsapp
WHATSAPP_API_VERSION=v21.0
WHATSAPP_PHONE_NUMBER_ID=...
WHATSAPP_ACCESS_TOKEN=...
WHATSAPP_RECIPIENT_NUMBERS=919999999999
WHATSAPP_TEMPLATE_NAME=echoes_daily_quotes
WHATSAPP_ALERT_TEMPLATE_NAME=echoes_alert
WHATSAPP_TEMPLATE_LANGUAGE=en
```

Then `echoes run --dry-run` to check the config loads without errors,
followed by `echoes run` for a real send.

### A few details worth knowing

- **A parameter-count mismatch gets rejected outright.** On a short day —
  when a pool doesn't divide evenly and the last day has only 1 or 2 quotes
  instead of 3 — the unused template slots are padded with an em dash so
  Meta still accepts the send.
- **One recipient failing doesn't block the others.** If you've added more
  than one number and one of them fails to receive a message, Echoes still
  delivers to the rest and logs which one failed — the run only counts as
  fully failed if *every* recipient failed.
- **Pricing scales per recipient**, not per message: sending to 3 numbers is
  billed as 3 independent conversations, not one shared cost. Check Meta's
  current WhatsApp Business Platform pricing page for exact rates, since they
  vary by recipient country and change over time.

---

## Where the playlist is stored

Two files, committed to the repository itself:

```
state/quotes_schedule.json   the prepared playlist — every date, and the quotes assigned to it
state/seen_blocks.json       every quote block ID Echoes has ever scheduled, so refreshes know what's new
```

**These are deliberately *not* gitignored.** GitHub Actions runners start
fresh every single run and throw everything away afterward — committing
these files back to the repo is the only way the playlist survives from one
day to the next. Quotes are written out in full inside the file rather than
referenced by ID elsewhere, on purpose: you should be able to open the file
and read it, not need to run code to decode it.

Writes are atomic (written to a temp file, then swapped into place), so an
interrupted run can never leave a half-written playlist behind. Since this
repository is private, the quote text inside these files stays private too.

---

## Deployment (GitHub Actions)

The workflow (`.github/workflows/echoes-daily.yml`) runs daily at `32 1 * * *`
UTC — 07:02 IST. It's deliberately not exactly on the hour, since GitHub's
shared runners are busiest right at the top of the hour and scheduled runs
are best-effort anyway. A late run is harmless here: the playlist is
calendar-keyed, so even a delayed run still reads the correct day's quotes.

Add these under **Settings → Secrets and variables → Actions → Secrets**:

```
NOTION_API_KEY
NOTION_BOOKS_DATABASE_ID
NOTION_ME_SECTION_DATABASE_ID
WHATSAPP_PHONE_NUMBER_ID
WHATSAPP_ACCESS_TOKEN
WHATSAPP_RECIPIENT_NUMBERS
```

Non-secret settings (`DELIVERY_MODE`, `TIMEZONE`, the per-day rates, template
names) go under the **Variables** tab instead, or can be left unset entirely
to use the defaults baked into `config.py`.

`workflow_dispatch` is enabled, so you can trigger a run by hand from the
**Actions** tab — with a dry-run toggle and a log-level picker — without
waiting for the schedule.

### Cost

A run takes roughly 1–3 minutes. At around 90 minutes a month, that's well
inside the 2,000 free Linux minutes a private repository gets on GitHub's
Free plan.

---

## Project layout

```
src/echoes/
├── config.py          resolves settings from .env or GitHub secrets
├── models.py          Quote, PoolSchedule, Playlist, SeenIndex — the shared data shapes
├── errors.py          the fatal-vs-recoverable exception hierarchy
├── logging_setup.py   stdout logging, secret masking for logs
├── cli.py             the echoes command: run / refresh / collect / show
├── collect/           Class 1 — Notion client, callout extraction (read-only)
├── playlist/          Class 2 — state storage, the scheduler, and playlist orchestration
├── deliver/           Class 3 — message formatting, console and WhatsApp senders
└── pipeline/          daily orchestration and failure safety (Class 2.3)

scripts/
└── dump_quotes.py     manual, network-using helper — see "Preview real quotes" above

tests/                 the pytest suite — network-free, fakes Notion at the transport boundary
state/                 the committed playlist and seen-index JSON files
```

The conceptual classes from the original design doc map onto these packages
directly. `playlist/scheduler.py` is deliberately pure — no I/O, no reading
the clock beyond what's explicitly passed in — which is what makes the date
arithmetic straightforward to test.

---

## Design values

Preserved from the original specification. Any change that goes against one
of these needs an explicit reason stated up front, not buried in the code.

```
Deterministic         >  clever
Scheduled             >  reactive
Quiet                 >  noisy
Transparent failures  >  silent failures
Conceptual clarity    >  technical purity
```

The system succeeds when it becomes boring and dependable.
