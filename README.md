# Echoes

> The voices of books and ideas, returning in rhythm.

A single-user personal knowledge resurfacing system. Quotes saved in Notion are
shuffled into a prepared playlist and delivered three at a time, once a day,
over WhatsApp. It is not a SaaS, not multi-tenant, and not built for scale — it
is built to be quiet, dependable, and boring.

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

**Two sources, one pattern.** Both are a filtered database query; every callout
block in a matching page body is one quote.

| | Books pool | Standalone pool |
|---|---|---|
| Database | `Books! Books! Books!` | `The Me Section` |
| Filter | Status = Completed **and** Completion Date ≥ 2024-04-24 | Tags contains `Quote` |
| Per day | 2 | 1 |
| Message | quote carries the book title | quote is bare |

To add another standalone quotes page, tag it `Quote` in Notion. The next
refresh picks it up — no code change.

**Calendar-keyed playlist.** Each date is assigned its own bundle when the
playlist is built. A run reads its own date, so a skipped or delayed run leaves
no drift to recover from — no counter to fall out of step.

**Independent pools.** Books and standalone drain at different rates and do not
share a horizon. Each assigns dates forward until its own quotes run out, then
reshuffles and rebuilds on its own without touching the other. A 40-quote
standalone pool recycles roughly twice while a 180-quote books pool runs once,
which is intended: no book quote is skipped just because internet quotes
accumulate more slowly.

**Randomness happens once**, at build and append time. Once a date is assigned,
it is fixed.

### Message shape

```
1. Love wins. Love always wins. - Tuesdays With Morrie
2. Learn to detach. - Tuesdays With Morrie
3. If you are not busy being born, you are busy dying. — Bob Dylan
```

Lines 1–2 are book quotes and carry their titles. Line 3 is the standalone
quote; any attribution written beneath it in Notion is collapsed onto the same
line. When the standalone pool holds nothing at all, all three lines are book
quotes and all three carry titles.

### Sunday refresh

Runs after delivery, so a refresh failure can never stop that day's quotes.
Detection is a set difference over Notion **block UUIDs**, which catches three
cases with one mechanism: a new book page, a new tagged quotes page, and new
callouts added to a page that already existed. Because the seen index is a
complete record rather than a moving cursor, a missed week is caught up
automatically on the next run.

Appending is non-destructive. New quotes go to dates after the current end. The
one exception: if the final day of a cycle is a short tail *and still in the
future*, it is topped up first — additive only, never a replacement. Days at or
before today are never touched.

### Failure safety

The message goes out even when preparation fails. If Notion is unreachable, the
playlist already on disk is used, the quotes are delivered anyway, and a
separate alert explains what happened. Failures are never silent.

Note that an outage on a day the playlist already covers is invisible by
design — no Notion call is made at all on a prepared day.

---

## Setup

```bash
./setup_env.sh              # create the conda env, install, scaffold .env
conda activate echoes
```

Then fill in `.env`. Three values are enough to start:

```
NOTION_API_KEY=...
NOTION_BOOKS_DATABASE_ID=...
NOTION_ME_SECTION_DATABASE_ID=...
```

Get the token from [notion.so/my-integrations](https://www.notion.so/my-integrations).
**Share both databases with the integration** — an unshared database returns
empty results rather than an error, which is a confusing way to lose an hour.

Database ids are the 32-character string in the database URL:
`notion.so/<workspace>/<DATABASE_ID>?v=<view_id>`.

### First run

```bash
echoes collect          # read-only: print every quote found, touch no state
echoes run --dry-run    # full pass, nothing sent, nothing written
echoes run              # the real thing (console delivery by default)
echoes show             # what is scheduled for today
pytest                  # 62 tests
```

Start with `collect`. It verifies both database filters and the callout parsing
against your real Notion — including attribution collapsing — before any
scheduling logic gets involved.

Exit codes: `0` success, `1` completed with alerts, `2` fatal.

---

## Configuration

Every setting is an environment variable. Locally they come from `.env`; on
GitHub Actions the same names come from repository secrets. Both paths end at
`os.environ`, so no application code can tell the difference — and there is no
path where a secret is read from a file that might get committed.

See `.env.example` for the full annotated list. The ones worth knowing:

| Variable | Default | Notes |
|---|---|---|
| `DELIVERY_MODE` | `console` | `console` prints; `whatsapp` sends |
| `QUOTES_PER_DAY_BOOKS` | `2` | |
| `QUOTES_PER_DAY_STANDALONE` | `1` | `0` disables the pool |
| `QUOTES_PER_DAY_BOOKS_FALLBACK` | `3` | used when standalone is empty |
| `TIMEZONE` | `Asia/Kolkata` | |
| `RANDOM_SEED` | *(unset)* | set an integer to make shuffles reproducible |
| `DRY_RUN` | `false` | send nothing, write nothing |
| `REFRESH_WEEKDAY` | `6` | 0 = Monday, 6 = Sunday |

---

## State

Two files, committed to the repository:

```
state/quotes_schedule.json   the prepared playlist, one section per pool
state/seen_blocks.json       every quote block UUID ever scheduled
```

**These are deliberately not gitignored.** Runners are ephemeral; committing
them back is what carries the playlist from one day to the next. Quotes are
stored inline rather than by reference so the file can be opened and read
directly — transparency over normalisation.

Writes are atomic (temp file + `os.replace`), so an interrupted run cannot
leave a half-written playlist behind.

Since the repository is private, the quote text in these files stays private
with it.

---

## Deployment

The workflow runs daily at `32 1 * * *` UTC — 07:02 IST, deliberately off the
top of the hour, since GitHub's shared runners are most contended there and
scheduled runs are best-effort. A late run is harmless: the playlist is
calendar-keyed, so a delayed run still reads the correct day.

Add these under **Settings → Secrets and variables → Actions**:

```
NOTION_API_KEY
NOTION_BOOKS_DATABASE_ID
NOTION_ME_SECTION_DATABASE_ID
WHATSAPP_PHONE_NUMBER_ID
WHATSAPP_ACCESS_TOKEN
WHATSAPP_RECIPIENT_NUMBER
```

Non-secret settings (`DELIVERY_MODE`, `TIMEZONE`, the per-day rates, template
names) go under the **Variables** tab, or omit them to use the defaults.

`workflow_dispatch` is enabled, so you can trigger a run manually — with a
dry-run toggle and a log-level selector — from the Actions tab.

### Cost

A run takes 1–3 minutes. At ~90 minutes a month it sits well inside the 2,000
free Linux minutes a private repository gets on the Free plan.

---

## WhatsApp

**Not yet wired up.** Run in `console` mode until the Business account exists.
When you get there, three things matter:

**You need two approved templates, not one.** Echoes never receives an inbound
message, so there is never an open 24-hour service window. Every message it
sends is business-initiated and must use a pre-approved template — the daily
quotes *and* the failure alert.

**Template parameters cannot contain newlines**, so the three lines must be
three separate variables. Suggested daily template body:

```
1. {{1}}
2. {{2}}
3. {{3}}
```

The alert template needs one variable. Both are closest to the **Utility**
category rather than Marketing, though Meta's reviewers make the final call.

**Meta rejects a parameter-count mismatch.** On a short-tail day — when a pool
does not divide evenly and the last day holds fewer quotes — the unused slots
are padded with an em dash so the send is still accepted.

Then set `DELIVERY_MODE=whatsapp` and fill in the four WhatsApp values.

---

## Layout

```
src/echoes/
├── config.py          resolve settings from .env or GitHub secrets
├── models.py          Quote, PoolSchedule, Playlist, SeenIndex
├── errors.py          fatal vs recoverable split
├── logging_setup.py   stdout logging, secret masking
├── cli.py             run / refresh / collect / show
├── collect/           Class 1  — Notion client, callout extraction
├── playlist/          Class 2  — state store, scheduler, service
├── deliver/           Class 3  — formatter, console and WhatsApp senders
└── pipeline/          daily orchestration, Class 2.3 failure safety
```

The conceptual classes from the design doc map onto packages directly. The
scheduler is pure — no I/O, no clock reads beyond what is passed in — which is
why the date arithmetic is directly testable.

---

## Design values

Preserved from the original specification. A change that violates one of these
needs an explicit justification.

```
Deterministic         >  clever
Scheduled             >  reactive
Quiet                 >  noisy
Transparent failures  >  silent failures
Conceptual clarity    >  technical purity
```

The system succeeds when it becomes boring and dependable.

conda run -n echoes python scripts/dump_quotes.py --days 7