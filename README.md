# Echoes

> The voices of books and ideas, returning in rhythm.

A single-user personal knowledge resurfacing system. Quotes you've saved in
Notion get shuffled into a prepared playlist and delivered three at a time,
once a day, over WhatsApp (via Twilio). It is not a SaaS, not multi-tenant,
and not built for scale — it is built to be quiet, dependable, and boring.

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

## Table of contents

1. [How it works](#how-it-works)
2. [Getting started](#getting-started)
3. [Every command, in one place](#every-command-in-one-place)
4. [Configuration](#configuration)
5. [WhatsApp delivery (via Twilio)](#whatsapp-delivery-via-twilio)
6. [Where the playlist is stored](#where-the-playlist-is-stored)
7. [Deployment (GitHub Actions)](#deployment-github-actions)
8. [Project layout](#project-layout)
9. [**Tracing the code flow**](#tracing-the-code-flow) — for anyone reading the codebase for the first time
10. [Invariants and things that look like bugs but aren't](#invariants-and-things-that-look-like-bugs-but-arent)
11. [Design values](#design-values)

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

**Calendar-keyed playlist.** When a pool is built, every date it will cover
gets its own bundle of quotes assigned up front, all at once. Each day's run
just reads that date's bundle out of the saved file — so a skipped or delayed
run leaves no drift to recover from. There is no counter that could fall out
of step, only a calendar.

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

Once a week (Sunday by default — see `REFRESH_WEEKDAY`), Echoes checks Notion
for anything new and appends it to the playlist — *after* that day's message
has already gone out, so a refresh failure can never block delivery.

New-quote detection compares Notion's internal block IDs (a permanent ID
every block gets, invisible in the UI) against a record of every ID Echoes
has ever scheduled — the "seen index." This one mechanism catches three
different cases: a newly completed book, a newly tagged quotes page, and new
quotes added to a page that already existed. Because that record never
shrinks or resets, a missed refresh — say you didn't run Echoes for two weeks
— is caught up automatically the next time it runs, with nothing lost.

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
intended, not a bug being missed. (More of these in
["Invariants and things that look like bugs but aren't"](#invariants-and-things-that-look-like-bugs-but-arent)
below.)

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

That's enough to run everything except actual WhatsApp delivery — see
[WhatsApp delivery](#whatsapp-delivery-via-twilio) below when you're ready
for that. Until then, `DELIVERY_MODE=console` prints the message instead of
sending it, and everything else (collection, scheduling, refresh, failure
handling) behaves exactly as it will in production.

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

`--dry-run` and `--log-level DEBUG|INFO|WARNING|ERROR` both work either
before or after the subcommand — `echoes --dry-run run` and
`echoes run --dry-run` are equivalent, use whichever reads better.

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

See `.env.example` for the complete, commented list. Full reference:

| Variable | Default | What it means |
|---|---|---|
| `NOTION_API_KEY` | *(required)* | Internal integration token |
| `NOTION_BOOKS_DATABASE_ID` | *(required)* | `Books! Books! Books!` database ID |
| `NOTION_ME_SECTION_DATABASE_ID` | *(required)* | `The Me Section` database ID |
| `NOTION_API_VERSION` | `2022-06-28` | Pinned so a Notion platform change can't silently alter behaviour |
| `NOTION_TIMEOUT_SECONDS` / `NOTION_MAX_RETRIES` | `30` / `3` | HTTP timeout and retry count for Notion calls |
| `NOTION_BOOKS_STATUS_PROPERTY` / `_VALUE` | `Status` / `Completed` | Which property + value marks a book "done" |
| `NOTION_BOOKS_DATE_PROPERTY` | `Completion Date` | Which property holds the completion date |
| `NOTION_BOOKS_COMPLETED_ON_OR_AFTER` | `2024-04-24` | Cutoff date for eligible books |
| `NOTION_ME_SECTION_TAG_PROPERTY` / `_VALUE` | `Tags` / `Quote` | Which tag marks a standalone quotes page |
| `QUOTES_PER_DAY_BOOKS` | `2` | Book quotes per day |
| `QUOTES_PER_DAY_STANDALONE` | `1` | Standalone quotes per day — `0` disables that pool entirely |
| `QUOTES_PER_DAY_BOOKS_FALLBACK` | `3` | Book quotes per day when the standalone pool is completely empty |
| `RANDOM_SEED` | *(unset)* | Set to a number to make shuffling reproducible (testing only) |
| `BOOK_SEPARATOR` | `" - "` | Joins a book quote to its title |
| `ATTRIBUTION_SEPARATOR` | `" — "` | Joins a standalone quote to its attribution |
| `DELIVERY_MODE` | `console` | `console` prints the message; `whatsapp` sends it via Twilio |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` | *(required if whatsapp)* | From the Twilio Console home page |
| `TWILIO_WHATSAPP_FROM` | *(required if whatsapp)* | The sending WhatsApp number, no `+`, no spaces |
| `WHATSAPP_RECIPIENT_NUMBERS` | *(required if whatsapp)* | Comma-separated recipient numbers, same format |
| `TWILIO_DAILY_CONTENT_SID` | *(required if whatsapp)* | Approved Content Template SID for the daily message |
| `TWILIO_ALERT_CONTENT_SID` | *(optional)* | Approved Content Template SID for alerts; unset = alerts are only logged |
| `TWILIO_TIMEOUT_SECONDS` / `TWILIO_MAX_RETRIES` | `30` / `3` | HTTP timeout and retry count for Twilio calls |
| `TIMEZONE` | `Asia/Kolkata` | Which timezone "today" and the daily schedule are measured in |
| `STATE_DIR` | `state` | Where the playlist/seen-index JSON files live |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `DRY_RUN` | `false` | Same effect as `--dry-run` on every command |
| `ALERTS_ENABLED` | `true` | Set `false` to suppress sending alerts (they're still logged) |
| `SUNDAY_REFRESH_ENABLED` | `true` | Set `false` to disable the weekly refresh entirely |
| `REFRESH_WEEKDAY` | `6` | Which day runs the weekly refresh (`0` = Monday … `6` = Sunday) |

---

## WhatsApp delivery (via Twilio)

**Not required to start** — everything above works with `DELIVERY_MODE=console`,
which just prints the message instead of sending it. This section is for when
you're ready to actually receive quotes on WhatsApp.

Echoes sends WhatsApp messages through **[Twilio](https://www.twilio.com)**
rather than calling Meta's Graph API directly. Twilio is a "Business Solution
Provider" — an officially authorized reseller of the same underlying WhatsApp
Business Platform, with its own account signup and console instead of Meta's
developer console. Practically: Twilio's own phone verification (independent
of Meta's), a free Sandbox for instant testing, and Twilio support if
something gets stuck.

### Why it needs more than an API key

Echoes never *receives* a WhatsApp message from you, so there's never an open
conversation window the way there is when you message a business first.
Every message Echoes sends is "business-initiated," and the underlying
WhatsApp Business Platform requires every business-initiated message to use
a pre-approved **Content Template** — a fixed message shape with blanks that
have been reviewed in advance. That means two templates need approval: one
for the daily quotes, one for the failure alert.

### Step-by-step setup

**1. Create a Twilio account.**
[twilio.com/try-twilio](https://www.twilio.com/try-twilio). Verification is
via Twilio's own system, independent of Meta's.

**2. Complete Trust Hub identity verification.**
This is easy to miss and blocks *every* real send until it's done, regardless
of which sender you use. In the Console, go to **Account → Trust Hub →
Customer Profile**, choose **Individual** (for personal/hobbyist use, not a
registered business), and complete the form: name, date of birth, address, a
photo ID upload, and a live selfie for verification. Submit and wait for
status **"Twilio Approved"** — this is Twilio's own KYC check, separate from
Meta's WhatsApp review, and every message send fails with a
`"Primary compliance profile is not approved"` error (Twilio error 63051)
until it clears.

**3. Get your Account SID and Auth Token.**
Console home page → **Account SID** and **Auth Token** (click "Show" to
reveal it).

**4. Join the WhatsApp Sandbox (for testing).**
**Messaging → Try it out → Send a WhatsApp message** shows a Sandbox number
(`+1 415 523 8886`) and a join code like `join <two-words>`. Send that code
as a WhatsApp message to the Sandbox number from any account you want to
test with.

**5. Register a real Sender (for actual daily use).**
The Sandbox is for testing only — for unattended daily sending, register a
proper Sender: **Messaging → Senders → WhatsApp Senders → Create new
sender**. This links a phone number to your WhatsApp Business Account (WABA)
through Meta (via **"Continue with Facebook"**, an embedded, guided version
of Meta's own business verification). If your Twilio account already has a
WABA linked (e.g. from Sandbox use), you must select that *same* WABA for the
new sender — creating a second one gets the request rejected.

**6. Set your recipients.**
`WHATSAPP_RECIPIENT_NUMBERS`: one or more numbers, comma-separated,
international format with no `+` and no spaces (India = `91XXXXXXXXXX`). The
same message goes to every number in the list independently — this fans a
message out to individuals, it is **not** a WhatsApp group. On the Sandbox,
each recipient must join first (step 4); with a real registered Sender, no
join step is needed.

**7. Create the two Content Templates.**
**Messaging → Content Template Builder → Create new**, twice:

- **Daily quotes** — type **Text**, category **Utility**, body:
  ```
  Hello! This is Echoes sending you your quotes for today:
  {{1}}
  {{2}}
  {{3}}

  Have a lovely day ahead! Echoes Signing Out!
  ```
  Three separate variables, since template parameters can't contain line
  breaks. **Important:** the variables must hold *bare* quote text with no
  leading number — the numbering ("1. ", "2. ", "3. ") is already in the
  template body above. Echoes' own code already accounts for this (see
  `deliver/twilio.py`); adding numbering on both sides would double it up in
  the delivered message.
- **Alert** — type **Text**, category **Utility**, body:
  ```
  Heads up — something needs your attention:
  {{1}}

  — Echoes
  ```

One placement rule that isn't obvious and *will* get a template rejected: **a
variable can't be the very first or very last thing in the body.** There must
be real static text both before the first variable and after the last one —
both templates above satisfy this already.

Submit both — Twilio forwards them to Meta for approval, typically minutes to
about a day. Once each shows **Approved**, copy its **Content SID** (starts
with `HX`).

**8. Fill in `.env` and switch delivery mode on.**

```
DELIVERY_MODE=whatsapp
TWILIO_ACCOUNT_SID=...
TWILIO_AUTH_TOKEN=...
TWILIO_WHATSAPP_FROM=...              # Sandbox number while testing, your real Sender once registered
WHATSAPP_RECIPIENT_NUMBERS=919999999999
TWILIO_DAILY_CONTENT_SID=HX...
TWILIO_ALERT_CONTENT_SID=HX...
```

Then `echoes run --dry-run` to check the config loads without errors,
followed by `echoes run` for a real send.

### A few details worth knowing

- **A parameter-count mismatch gets rejected outright.** On a short day —
  when a pool doesn't divide evenly and the last day has only 1 or 2 quotes
  instead of 3 — the unused template slots are padded with an em dash so the
  send is still accepted.
- **One recipient failing doesn't block the others.** If you've added more
  than one number and one of them fails to receive a message, Echoes still
  delivers to the rest and logs which one failed — the run only counts as
  fully failed if *every* recipient failed.
- **The Sandbox is for testing, not daily production use.** Joined numbers
  can need rejoining after a period of inactivity, and it's meant for
  verifying the pipeline works end to end — not as the permanent delivery
  channel. Moving to a real registered Sender is a Twilio Console step, not a
  code change.
- **A newly registered Sender can still fail to send** with error 63051
  ("WhatsApp Sender or Account is Locked") even when the Sender itself shows
  `ONLINE`/`HIGH quality` in Twilio's own Senders API — this points at an
  account-level restriction rather than the sender resource, and is a
  Twilio Support case, not something fixable through more Console
  configuration.
- **Pricing scales per recipient**, not per message: sending to 3 numbers is
  billed as 3 independent conversations, not one shared cost. Twilio also
  adds its own small per-message fee on top of the underlying WhatsApp fee.
  Check Twilio's current WhatsApp pricing page for exact rates, since they
  vary by recipient country and change over time. The Sandbox itself is free.

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
day to the next. Without this, every run would rebuild the entire playlist
from scratch, with no memory of what was already sent, risking real repeats.
Quotes are written out in full inside the file rather than referenced by ID
elsewhere, on purpose: you should be able to open the file and read it, not
need to run code to decode it.

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
TWILIO_ACCOUNT_SID
TWILIO_AUTH_TOKEN
TWILIO_WHATSAPP_FROM
WHATSAPP_RECIPIENT_NUMBERS
```

Non-secret settings (`DELIVERY_MODE`, `TIMEZONE`, the per-day rates, Content
Template SIDs) go under the **Variables** tab instead, or can be left unset
entirely to use the defaults baked into `config.py`.

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
├── config.py           resolves settings from .env or GitHub secrets - Settings, NotionSettings, TwilioSettings
├── models.py            Quote, PoolSchedule, Playlist, SeenIndex, DailyBundle - the shared data shapes
├── errors.py             the fatal-vs-recoverable exception hierarchy
├── logging_setup.py      stdout logging, secret masking for logs
├── cli.py                 the echoes command: run / refresh / collect / show
│
├── collect/               Class 1 - Notion client, callout extraction (read-only)
│   ├── notion_api.py       thin Notion REST client: pagination, retries, error translation
│   └── collector.py         turns Notion pages/callouts into Quote objects
│
├── playlist/               Class 2 - state storage, the scheduler, playlist orchestration
│   ├── state_store.py       reads/writes state/*.json (atomic writes)
│   ├── scheduler.py          pure functions: build_schedule, append_schedule (no I/O, no clock)
│   └── service.py             PlaylistService - the daily "prepare and pick" + weekly refresh logic
│
├── deliver/                 Class 3 - message formatting, console and Twilio senders
│   ├── base.py                the Sender interface
│   ├── formatter.py            format_quote / format_lines / format_bundle
│   ├── console.py               ConsoleSender - prints instead of sending
│   ├── twilio.py                 TwilioSender - sends via Twilio's WhatsApp API
│   └── factory.py                build_sender() - picks Console or Twilio based on DELIVERY_MODE
│
└── pipeline/                 daily orchestration and failure safety (Class 2.3)
    ├── daily.py                run_daily() - the full daily run, in order
    └── refresh.py               perform_refresh() / run_refresh() - the weekly refresh

scripts/
└── dump_quotes.py         manual, network-using helper - see "Preview real quotes" above

tests/                       the pytest suite - network-free, fakes Notion at the transport boundary
state/                       the committed playlist and seen-index JSON files
```

The conceptual classes from the original design doc map onto these packages
directly. `playlist/scheduler.py` is deliberately pure — no I/O, no reading
the clock beyond what's explicitly passed in — which is what makes the date
arithmetic straightforward to test.

---

## Tracing the code flow

This section is for reading the codebase for the first time: where execution
starts, which function calls which, and how different scenarios branch. Read
it alongside the [project layout](#project-layout) above.

### Entry point

`pyproject.toml` registers the console script:
```toml
[project.scripts]
echoes = "echoes.cli:main"
```
So typing `echoes run` calls `main()` in [`cli.py`](src/echoes/cli.py).
(`python -m echoes` goes through [`__main__.py`](src/echoes/__main__.py),
which just calls the same `main()`.)

`main()` does the same four things regardless of which command was typed:

1. Parse arguments (`build_parser()`).
2. Configure logging twice — once with a safe default (`"INFO"`), so a
   *configuration* error is still visible, then again once the real
   `LOG_LEVEL` is known from `.env`.
3. `Settings.from_env()` ([`config.py`](src/echoes/config.py)) — resolves
   every environment variable into one frozen `Settings` object. Raises
   `ConfigurationError` (fatal, exit code `2`) if something required is
   missing.
4. Dispatch on `args.command` to one of four places.

### Scenario: `echoes run` (the real daily run)

This is the main path — everything else is a variant of it.

```
cli.main()
  └─ run_daily(settings)                          pipeline/daily.py
       ├─ builds NotionAPI                         collect/notion_api.py
       ├─ wraps it in QuoteCollector                collect/collector.py
       ├─ wraps store+collector in PlaylistService    playlist/service.py
       ├─ builds a Sender via build_sender()           deliver/factory.py
       │
       ├─ _prepare()
       │    └─ service.prepare_for(today)
       │         ├─ for STANDALONE, then BOOKS pool:
       │         │    └─ _ensure_pool() → _rebuild_reason() decides
       │         │         if a rebuild is needed; if so:
       │         │         collector.collect_*() → build_schedule()   playlist/scheduler.py (pure)
       │         └─ bundle_for(playlist, today) - reads today's
       │              quotes straight out of the in-memory Playlist
       │    (back in daily.py) if state changed: store.save_playlist()/save_seen()   playlist/state_store.py
       │
       ├─ _deliver()
       │    └─ sender.send_daily(bundle)
       │         → TwilioSender (deliver/twilio.py) or ConsoleSender (deliver/console.py)
       │         both call formatter.format_quote()/format_lines()      deliver/formatter.py
       │
       ├─ _maybe_refresh()   (only if today is the configured refresh weekday)
       │    └─ perform_refresh()                    pipeline/refresh.py
       │         └─ service.refresh(playlist, seen, today)
       │              └─ collector.collect_*() → seen.unseen() → append_schedule()   playlist/scheduler.py
       │
       └─ _raise_alerts()  - sends anything in report.alerts via sender.send_alert(),
                              swallowing any exception so alerting can never mask
                              the original failure
```

### Scenario: `echoes run --dry-run`

Identical call path to above, with one flag threaded through: `settings.dry_run
= True`. Concretely:
- `_prepare()` still rebuilds pools in memory (may still call Notion), but
  skips the `store.save_playlist()`/`save_seen()` calls.
- `TwilioSender.send_daily()`/`send_alert()` log `"DRY RUN - would send..."`
  and return without calling Twilio at all.
- `perform_refresh()` still detects new quotes but skips
  `store.save_playlist()`/`save_seen()`.

Nothing on disk changes and nothing is sent — everything else runs for real,
which is what makes this useful for checking config and Notion connectivity.

### Scenario: Notion is unreachable during a scheduled rebuild

`service.prepare_for()` lets a `CollectionError` propagate up.
`_prepare()` in `pipeline/daily.py` catches it:
```
except (CollectionError, StateError) as exc:
    report.degraded = True
    report.add_alert(...)
    playlist = store.load_playlist()      # fall back to what's already on disk
    seen = store.load_seen()
    return playlist, seen, service.bundle_for(playlist, today)
```
Delivery then proceeds normally from the *existing* playlist. This is why an
outage on a day whose quotes were already scheduled produces no error at all
— `_ensure_pool()` never needed to call Notion in the first place, so there
was nothing to fail.

### Scenario: the standalone pool is empty (fallback to 3 book quotes)

Inside `service.prepare_for()`:
```python
standalone_has_quotes = bool(standalone_schedule and standalone_schedule.total_quotes > 0)
books_rate = (
    settings.quotes_per_day_books
    if standalone_has_quotes
    else settings.quotes_per_day_books_fallback   # 3, by default
)
```
The books pool gets rebuilt (if needed) at the fallback rate, and
`bundle_for()` computes `used_fallback` by checking whether more book quotes
were picked than the normal per-day rate. This is resolved once, at *build*
time — not re-decided every day at delivery time.

### Scenario: delivery fails partway (multiple recipients)

Inside `TwilioSender.send_daily()` (`deliver/twilio.py`): each recipient is
sent to independently, in a loop, with failures collected rather than
raised immediately:
```python
for recipient in recipients:
    try:
        self._post(payload)
    except DeliveryError:
        failed.append(recipient)

if len(failed) == len(recipients):
    raise DeliveryError(...)   # only a *total* failure is reported as undelivered
```
So one bad number logs a warning and still lets everyone else receive the
message that day.

### Scenario: `echoes collect`

Bypasses `PlaylistService` and the pipeline package entirely —
`_command_collect()` in `cli.py` builds a `NotionAPI` + `QuoteCollector`
directly, calls `collect_books()`/`collect_standalone()`, and prints. Nothing
is scheduled, nothing is saved. This is the shortest path through the
codebase and the fastest way to sanity-check Notion connectivity.

### Scenario: `echoes show [--date ...]`

`_command_show()` in `cli.py` never touches Notion at all: it loads the
playlist straight from disk (`StateStore.load_playlist()`) and calls the same
`PlaylistService.bundle_for()` used internally by the real run, passed a
`collector=None` — safe, because `bundle_for()` only reads what's already
scheduled and never triggers a rebuild.

### Scenario: `echoes refresh`

`run_refresh()` in `pipeline/refresh.py` is the same `perform_refresh()` used
inside a real `echoes run`, just invoked standalone without a daily delivery
around it — useful for manually pulling in new quotes without waiting for
Sunday.

### Scenario: `scripts/dump_quotes.py`

Not part of the CLI at all — a separate script that builds its own
`Settings`, `NotionAPI`, `QuoteCollector`, and `PlaylistService`, calls
`prepare_for()` to build/check pools (real Notion calls, like `--dry-run`),
then loops `bundle_for()` across a date range and writes the result to
`quotes_snapshot.json`. It never calls `store.save_playlist()`, so it can be
run repeatedly without side effects.

---

## Invariants and things that look like bugs but aren't

Worth reading before "fixing" anything below — each of these is intentional,
not an oversight.

1. **An outage on a day the playlist already covers is invisible.** No Notion
   call is made on a prepared day, so `degraded` stays `False`. That's the
   resilience property described above, not a missed error path.
2. **An empty pool is only rechecked once per day.** Guarded by
   `PoolSchedule.built_on`. Recovery from the fallback lands on the *next*
   run, not the same one — this stops a genuinely empty Notion from being
   hammered every single run.
3. **Pools intentionally desynchronise.** Books and standalone drain at
   different rates and each rebuilds on its own exhaustion, without touching
   the other. Syncing their horizons would mean book quotes get reshuffled
   before they've all been seen.
4. **Short tail days are fine.** When a pool doesn't divide evenly, the last
   day of a cycle sends 1 or 2 quotes instead of 3. Intended.
5. **`state/*.json` is deliberately not gitignored.** Runners are ephemeral;
   committing state back is what carries the playlist between days. See
   ["Where the playlist is stored"](#where-the-playlist-is-stored).
6. **Quotes are stored inline in the playlist, not normalised by reference.**
   Transparency over deduplication — the file should be readable by opening
   it directly.
7. **Quote identity is the Notion block UUID, never text hashing.** A typo
   fix in Notion would resurface as a "new" quote under text hashing.
8. **The seen index is a complete record, not a moving cursor.** This is what
   makes a missed refresh self-healing — there's no "last refreshed at"
   timestamp to fall behind.
9. **The playlist is calendar-keyed, not counter-based.** A skipped run
   leaves no drift; there's no day-pointer that increments.
10. **The refresh is append-only**, with one permitted exception: topping up
    a short tail day that's still in the future. Today and the past are
    never written to.
11. **Randomness happens at build/append time only** — never at delivery
    time. Once a date has quotes assigned, they don't change.
12. **Alerting must never raise.** An alert failure is caught and logged, so
    it can never mask the original failure it was trying to report.

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

Explicit non-goals: no recommendation engine, no AI-generated quotes, no
real-time sync, no analytics, no dashboards, no tagging UI, no feedback
loops, no ML.

The system succeeds when it becomes boring and dependable.
