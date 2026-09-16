# International Motorsports Daily Ads

This repository is the shared handoff point for the International Motorsports daily social-ad workflow:

**ChatGPT -> GitHub -> Codex / Claude Code -> publishing platform**

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
