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
  publishing.py            platform adapters (none wired yet - see below)
  cli.py                   the orchestrator
scripts/Register-Schedule.ps1   the 9:30 AM Windows task
tests/                     80 tests
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
| `production.mode` | `validate_only` or `produce` — see *Who makes the creative* |
| `publishing.platform` | `none` until a platform is chosen |

Secrets never go in this file. They go in `.env`, which is gitignored. Copy `.env.example` and fill in only what you need.

## Who makes the creative

**There is a contradiction between this spec and the instructions I was given, and I have not guessed which way it should go.**

- This README, step 3, says *"Codex or Claude Code validates the unit, **creates the graphic and matching description**"*, with ChatGPT preparing a *brief* in step 1.
- The instruction I was given says ChatGPT creates the graphic and the description.

The system supports both, switched by one setting:

| `production.mode` | Behaviour |
| --- | --- |
| `validate_only` *(current default)* | ChatGPT supplies `graphic.png` and `description.txt` in the package. This system verifies the unit against the live listing, checks the graphic is 1080x1080, checks the copy agrees with the listing, and promotes or blocks. |
| `produce` | This system downloads the exact-unit photo, composes the 1080x1080 graphic and writes the description from verified listing data. Requires `assets/brand/logo.png`. |

Say which you want and it is a one-line change. Until then it runs in `validate_only`, the safer of the two.

## Daily operation

The scheduled task runs this each morning:

```bash
python ims-ads.py run
```

It reads live inventory in discount order, skips units that are already advertised or have no photo of themselves, downloads the exact unit's photo, validates everything, and files the package into `ready/` or `issues/`.

```bash
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

80 tests, no network, no live site, running against a temporary tree — safe to run at any time. They cover the four areas that matter most:

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

Everything that does not require credentials is built, tested, and working end to end. These are the open items, shortest path first:

### 1. Which platform publishes — *blocking automatic publishing*

No platform is wired, because none has been chosen. Currently `publishing.platform = "none"`, which is fully supported: the workflow stops at `ready/` and you record the publish yourself.

**To supply:** pick one — GoHighLevel Social Planner, Meta Graph API, or something else. Adapters are stubbed in `src/ims_ads/publishing.py`; each names the exact environment variables it needs.

### 2. Publishing credentials — *blocking automatic publishing*

Once a platform is chosen, put its variables in `.env` (names are in `.env.example`). For GoHighLevel that is `GHL_PRIVATE_INTEGRATION_TOKEN` and `GHL_LOCATION_ID`, from a Private Integration with the `social-media-posting.write` and `medias.write` scopes.

### 3. `assets/brand/logo.png` — *only needed for `production.mode = "produce"`*

A transparent PNG of the International Motorsports logo, about 1000px wide. There is a vector at `LOGOS/im-logo.svg` on this machine; it needs exporting to PNG because the composer cannot read SVG. Not needed at all while the mode is `validate_only`.

### 4. The creative-ownership decision — *see "Who makes the creative"*

One word: does ChatGPT supply the graphic and description, or does this system build them?

Nothing above is guessed at, and nothing is stubbed in a way that could silently publish something wrong: every unimplemented path raises with the exact reason and the exact fix.
