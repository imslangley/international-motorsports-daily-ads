# International Motorsports Daily Ads

This repository is the shared handoff point for the International Motorsports daily social-ad workflow:

**ChatGPT -> GitHub -> Codex / Claude Code -> publishing platform**

International Motorsports Motorcycle Co. — 778-653-5702 — https://www.internationalmotorsports.com

---

## Daily 9:30 AM process

1. By **9:30 AM Pacific Time**, ChatGPT selects a current, eligible in-stock unit and prepares an ad brief in `inbox/`.
2. Check `ad-history.csv`, `published/`, `inbox/`, and `ready/` to prevent duplicates.
3. Codex or Claude Code validates the unit, creates the graphic and matching description, and records blockers in `issues/`.
4. When every requirement passes review, move the complete ad package to `ready/`.
5. After publishing, move the package to `published/`, update `ad-history.csv`, and write the result to `logs/`.
6. Move retired or superseded material to `archive/` without deleting its history.

## Content requirements

### Exact unit image

- Use an image of the **exact advertised unit** from the dealership's current inventory listing.
- It must match the listing's year, make, model, trim, colour, and stock or VIN identifier.
- Do not substitute stock photography, a similar unit, or an AI-generated vehicle image.
- If the exact-unit image cannot be verified, stop production and add a dated note in `issues/`.

### 1080x1080 graphic

- The final social graphic must be exactly **1080 x 1080 pixels**.
- Keep branding, offer details, and required disclaimers readable and inside safe margins.

### Matching description

- Every graphic must have a description matching the same exact unit and offer.
- Year, make, model, trim, colour, price or promotion, stock/VIN, URL, and call to action must agree with the graphic and live listing.
- Never reuse copy naming a different unit or containing stale pricing.

## Duplicate protection

1. Search `ad-history.csv` by stock number, VIN, inventory URL, and normalized year/make/model/trim.
2. Check `inbox/`, `ready/`, and `published/` for the same unit or campaign.
3. Treat an active or published record as a duplicate unless a deliberate rerun is documented in `issues/` with approval and a new campaign date.
4. Add the history row immediately after publishing.

## Publishing states

| Location | State | Meaning |
| --- | --- | --- |
| `inbox/` | Draft | New brief or source package awaiting validation and production. |
| `ready/` | Approved | Exact-unit image, 1080x1080 graphic, matching description, and duplicate check are complete. |
| `published/` | Published | Live or scheduled ad package retained as the source of record. |
| `issues/` | Blocked | A mismatch, duplicate, stale offer, or other issue requires action. |
| `archive/` | Archived | Retired or superseded material kept for audit history. |
| `logs/` | Log | Dated automation and publishing results. |

Only items in `ready/` may be published. A publish is not complete until the package is in `published/` and `ad-history.csv` is updated.

## Ad package naming

Use `YYYY-MM-DD_stock-number_year-make-model-trim`. Each package should contain the listing reference, exact-unit image, final 1080x1080 graphic, matching description, and approval notes.

---
---

# Implementation

## What is built

```
ims-ads.py                 launcher - run everything through this
config/config.json         all settings; no secrets
.env.example               environment variable NAMES only
src/ims_ads/
  core.py                  paths, config, logging, the Unit model
  inventory.py             reads the live site (Playwright + real Chrome)
  dedupe.py                the four-identifier duplicate check
  media.py                 exact-unit photo rules + 1080x1080 composer
  copywriting.py           description building and agreement checks
  validation.py            every rule that gates ready/
  packaging.py             package layout, state machine, ad-history.csv
  publishing.py            platform adapters (DA, GHL, Meta)
  intake.py                takes in what Codex pushes, verifies vs the site
  dealership_accelerator.py  DA publishing (UI-driven; no API exists)
  cli.py                   the orchestrator
scripts/Register-Schedule.ps1   the 9:30 AM Windows task
tests/                     96 tests
```

A package on disk looks like:

```
ready/2026-09-16_2024-lxp-orioli_2024-mv-agusta-lxp-orioli/
    listing.json          what the live listing said, verbatim
    source-image.jpg      the exact unit's own photo
    graphic.png           the final 1080x1080 social graphic
    description.txt       the matching description
    validation.txt        every check and its result
    approval.md           dated audit trail of every state change
```

## Setup

```bash
python -m pip install -r requirements.txt
python -m playwright install chromium
```

Python 3.10+ and Google Chrome. Then confirm the environment:

```bash
python ims-ads.py doctor
```

`doctor` prints exactly what is present and what is missing. Nothing else needs configuring to run a dry run.

## Configuration

Everything lives in `config/config.json`. The settings worth knowing:

| Key | Meaning |
| --- | --- |
| `inventory.search_url` | The in-stock, new, sorted-by-discount search page |
| `inventory.headless` | **Keep `false`.** See *Why a real browser* below |
| `inventory.retry_attempts` / `retry_backoff_ms` | The site answers bursts with HTTP 403; the job backs off and retries |
| `selection.max_candidates_to_inspect` | How far down the discount list to look for an eligible unit (default 12) |
| `selection.min_savings_dollars` | Units discounted by less than this are skipped |
| `selection.require_exact_unit_image` | Skip units whose listing has no photo of themselves |
| `image.reject_filename_patterns` | Filenames treated as generated or placeholder, never as a unit photo |
| `production.mode` | `validate_only` (ChatGPT supplies the creative) or `produce` — see *The confirmed execution path* |
| `publishing.platform` | `dealership_accelerator`. Set to `none` to stop at `ready/` and record publishes by hand |

Secrets never go in this file. They go in `.env`, which is gitignored. Copy `.env.example` and fill in only what you need.

## The confirmed execution path

```
09:30  ChatGPT      creates the ad graphic (1080x1080) and the description
       Codex        commits the package to inbox/ and pushes to this repo
       ims-ads sync pulls it, reads that unit's OWN live listing, and verifies
                    the creative against the site - not against the claim
                    -> ready/   (everything agrees)
                    -> issues/  (anything does not, with a dated note)
       you          review and publish to Dealership Accelerator
```

`production.mode` is `validate_only`: ChatGPT supplies `graphic.png` and
`description.txt`; this system never invents either. Set it to `produce` to have this
system read the site and build the package itself instead (needs `assets/brand/logo.png`).

### What Codex pushes

A folder in `inbox/` named `YYYY-MM-DD_stock-number_year-make-model-trim`, containing:

| File | Required | Notes |
| --- | --- | --- |
| `graphic.png` | yes | exactly 1080x1080 |
| `description.txt` | yes | **must contain the unit's inventory URL** - that is how the unit is identified |
| `brief.json` | optional | `{"inventory_url": "..."}` - the most reliable identifier of all |

Intake resolves the unit in this order: `brief.json`, then the inventory URL inside
`description.txt`, then the stock number in the folder name matched against live
inventory. If none of the three identifies a real listing, the package goes to
`issues/` and **no unit is assumed**.

Once the unit is resolved, its live listing is read and `listing.json` is written from
the site. Every subsequent check compares the creative against that, so stale pricing
or a description naming a different motorcycle is caught rather than published.

## Daily operation

The scheduled task runs this each morning:

```bash
python ims-ads.py sync
```

It pulls whatever Codex pushed, verifies each package against its unit's own live listing, and files it into `ready/` or `issues/`. It publishes nothing.

`run` is the alternative for `production.mode = "produce"`: it reads live inventory in discount order, skips units already advertised or with no photo of themselves, downloads the exact unit's photo, and builds the package here.

```bash
python ims-ads.py sync
python ims-ads.py run
python ims-ads.py da-login
python ims-ads.py status
python ims-ads.py validate
python ims-ads.py promote NAME
python ims-ads.py publish NAME --post-url https://...
python ims-ads.py archive NAME
```

Add `--dry-run` to any command. On `run` it does the entire workflow — live inventory, selection, photo download, validation — in a temporary folder that is deleted afterwards, so nothing is filed and nothing is published.

To schedule it, from PowerShell in this folder:

```powershell
.\scripts\Register-Schedule.ps1
```

Use `-DryRun` to schedule a dry run for the first week, and `-Unregister` to remove it. **Task Scheduler fires on local time**; the dealership is in Pacific, so 09:30 local is 9:30 AM Pacific. On a machine in another zone, adjust `-Time`.

## The morning routine

**Before 9:15 AM Pacific, tell Codex: _"do today's ad"_.** That is the only human
step. Codex reads [`AGENTS.md`](AGENTS.md), picks the next eligible unit, builds the
package and pushes it to `inbox/`.

At 9:30 the scheduled task runs `sync`, and repeats every 30 minutes until noon so a
late push is still caught. Each morning it writes a plain-English summary to:

```
logs/daily-report-YYYY-MM-DD.log
```

That file is the one to read. It says either *"Nothing from Codex yet today"*, or, for
each package, the unit, the price, every failed check, and the result.

### The dry-run week

For the first week the task runs with `--dry-run`. It still pulls from GitHub and
still validates every package fully against the live listing — on a throwaway copy —
but files nothing: packages stay in `inbox/` and the report says *WOULD BE READY TO
POST* or *WOULD BE BLOCKED*. Because nothing is recorded, Codex will see the earlier
packages in `inbox/` and move on to the next unit each day, which exercises more of
the inventory.

**Before switching to live,** clear `inbox/` of the test-week packages, then:

```powershell
.\scripts\Register-Schedule.ps1
```

### A safety stop worth knowing about

Codex has write access to `main`, and this job runs whatever code is on `main`. So if
a pull ever changes `src/`, `config/`, `scripts/`, `tests/` or `ims-ads.py`, that
morning's run **stops** and the report says why, rather than executing unreviewed
changes — for example a push that set `armed` to `true`. Codex is instructed to only
ever add folders under `inbox/`. If a change is intended, run once with
`--accept-code-changes`.

## Recovery

| Situation | What to do |
| --- | --- |
| `HTTP 403 after N attempts` | The site rate-limited the run. It retries with backoff automatically; if it persists, wait a few minutes and re-run, or raise `inventory.retry_backoff_ms`. |
| `no priced results appeared` | The site markup changed. The `.result-saleprice` selector in `src/ims_ads/inventory.py` is the thing to check. |
| Package stuck in `issues/` | Read the dated `*_ISSUE.md` note — it says exactly which checks failed. Fix the source, move the package back to `inbox/`, then `python ims-ads.py promote NAME`. |
| Genuine unit flagged as duplicate | Document the approval in `issues/`, then re-run with `--allow-rerun`. |
| `no exact-unit photo` for many units | Those listings only carry manufacturer catalog images. Upload a real photo of the unit to its listing and it becomes eligible automatically — nothing here needs changing. |
| Wrong package was published | Nothing is ever deleted. Move it to `archive/` with a note; the `ad-history.csv` row stays as the record. |
| Need to re-read the site for a package | Remove the package from `inbox/` and re-run. The tool itself never destroys source files. |

Every run appends to `logs/YYYY-MM-DD.log`, including the reason for every skipped unit and every failed check.

## Testing

```bash
python -m pytest
```

96 tests, no network, no live site, running against a temporary tree — safe to run at any time. They cover the four areas that matter most:

- **Duplicate protection** — each of the four identifiers independently, live-folder detection, archived units not blocking, `--allow-rerun`, and the case where two units both lack a VIN and must not match each other on the blank.
- **Publishing-state transitions** — the legal state machine, `inbox` never skipping straight to `published`, refusal to overwrite, dry-run leaving no trace, and history written on publish.
- **Listing validation** — every required field, price arithmetic, stale listings, in-stock status, exact-unit image rules, and description/listing agreement.
- **The 1080x1080 requirement** — exact size passes, every near-miss fails, and the composer emits a 1080 square from any source aspect ratio.

## Why a real browser

`requests` gets **HTTP 403** on both the search page and the unit pages — the site blocks plain HTTP clients. Playwright driving the installed Chrome works.

Two behaviours were found the hard way and are worth knowing before changing `inventory.py`:

1. **The price nodes are attached but not visible.** The results render several view variants and hide all but one, so `wait_for_selector` must use `state="attached"`. Waiting for visibility times out on a page that is fully loaded.
2. **Each unit page is opened in a fresh browser context.** The site only emits its schema.org `Product` JSON-LD on a clean session — reuse the context that loaded the search page and every unit comes back with no structured data at all, silently. That JSON-LD is where the stock number, brand, price and in-stock flag come from, so this is not optional.

---

## What is still missing

Everything that does not require your sign-in is built, tested and working. Two items remain:

### 1. Dealership Accelerator page details — *blocking automatic publishing*

DA has no posting API, so the adapter drives its UI with a real browser. It needs a
signed-in session and the composer's page structure — and it **refuses to click
anything while any selector is unset**, rather than guessing at a control in a live
marketing tool.

**Shortest path:**

1. Open `config/dealership-accelerator.json` and paste in two URLs from your address
   bar: the DA sign-in page, and the page where you compose a post.
2. Run `python ims-ads.py da-login`. A browser opens. **You** sign in — nothing is
   typed for you, and no credential is ever written into this repo. The session is
   saved to a profile under your home directory, outside the tree.
3. Tell me it's open and the remaining selectors can be read off the real composer.

Until then `python ims-ads.py doctor` lists every gap, and publishing stays manual.

### 2. `assets/brand/logo.png` — *only if you switch to `produce` mode*

A transparent PNG of the IM logo, about 1000px wide, exported from `LOGOS/im-logo.svg`
(the composer cannot read SVG). Not needed while ChatGPT supplies the graphic.

---

**A note on Dealership Accelerator, recorded once and not laboured.** It is the only
option here without an API, so posting means browser automation: it needs Chrome open
and signed in at post time, and LeadVenture can change the page without warning. You
chose it knowing that, and it is built. If a morning ever fails for that reason, the
package is already sitting complete in `ready/` and can be posted by hand in a minute —
nothing is lost.
