# Instructions for Codex

You have one job in this repository: **when asked for today's ad, add one ad package
to `inbox/` and push it to `main`.** A validator on the dealership's PC picks it up,
checks every figure against the live website, and files it for posting. You never
post anything.

When you are told *"do today's ad"* (or similar), follow this file exactly.

---

## Deadline

Push by **9:15 AM Pacific**. The validator runs at 9:30 and repeats every 30 minutes
until noon, so a late push is still caught — but aim for 9:15.

## Steps

1. **Pick the unit.** Open
   <https://www.internationalmotorsports.com/search/inventory/availability/In%20Stock/usage/New/sort/discount>.
   Work down the list from the top (highest discount first).

2. **Skip anything already taken.** A unit is taken if its stock number, VIN, listing
   URL, or year/make/model/trim already appears in any of:
   - `ad-history.csv`
   - any folder under `inbox/`, `ready/` or `published/`

   Also skip any unit whose listing has **no real photo of that exact unit** — only
   manufacturer catalog images, or an image named like `ChatGPTImage…`. Say which
   units you skipped and why.

3. **Copy the facts from that unit's own page, exactly.** Year, make, model, trim,
   colour, stock number, the *On Sale* price, the *Was* price, the *Save* amount, and
   the listing URL. Copy them — never recall, round, or estimate.

4. **Make `graphic.png`** — exactly **1080 × 1080**, white background.
   - Use that unit's own photo from that unit's own listing. **Never** a similar unit,
     a manufacturer catalog shot, or a generated motorcycle.
   - Show year / make / model, the sale price, and the savings.
   - Footer: the International Motorsports logo, `Call or Text 778-653-5702`,
     `WE SHIP EVERYWHERE`.

5. **Write `description.txt`** using only the figures from step 3.
   - Must contain: the listing URL, `778-653-5702`, `International Motorsports`,
     `WE SHIP EVERYWHERE`, `Peace & Grease Since 2003`.
   - Under 1200 characters.
   - Never state stock levels or "only N left" for new units. Never invent a spec,
     promotion, finance rate, or date.

6. **Write `brief.json`**: `{"inventory_url": "<the listing URL from step 3>"}`

7. **Commit only those three files** to

   ```
   inbox/<YYYY-MM-DD>_<stock-number>_<year-make-model-trim>/
   ```

   with the commit message `Add ad package: <year> <make> <model> <trim>` and push to
   `main`.

A complete worked example is in [`docs/example-package/`](docs/example-package/).
Match its shape.

---

## Hard rules

- **Only ever add files under `inbox/`.** Do not edit, move or delete anything else —
  not `src/`, `config/`, `scripts/`, `tests/`, `ad-history.csv`, or other packages.
  The validator refuses to run if a pull changes code or configuration, so any such
  edit stops that morning's ad entirely.
- **Never fill a gap by guessing.** If a figure is not on the listing, or the unit has
  no real photo, move to the next unit and say why.
- **One package per request.**

## Practical note

The dealership website returns HTTP 403 to plain HTTP clients (`curl`, `requests`).
Read it through a real browser.
