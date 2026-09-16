# What Codex must push

This folder is a worked example of one ad package. Copy its shape exactly.

**The graphic here is a layout reference built to show the structure — it is not a
real ad and carries a warning band so it can never be mistaken for one.** The real
graphic comes from ChatGPT.

## The folder

```
inbox/YYYY-MM-DD_stock-number_year-make-model-trim/
    graphic.png         required   exactly 1080 x 1080
    description.txt     required   must contain the unit's inventory URL
    brief.json          optional   the most reliable way to identify the unit
```

Real example, from a live listing:

```
inbox/2026-09-17_2024-LXP-ORIOLI_2024-mv-agusta-lxp-orioli/
```

Push it to `main`. The 09:30 job pulls it, reads that unit's own live listing, checks
the creative against the site, and files it to `ready/` or `issues/`.

## How the unit is identified

In this order:

1. **`brief.json`** — `{"inventory_url": "https://www.internationalmotorsports.com/inventory/...i"}`
2. **The inventory URL inside `description.txt`** — this is why the URL is mandatory
3. **The stock number in the folder name**, matched against live inventory

If none of the three resolves to a real listing, the package goes to `issues/` and
**no unit is assumed**. Including `brief.json` makes this unambiguous — recommended.

## What gets checked against the live site

Nothing here trusts the package. Every figure is compared with the unit's own listing:

| Check | Fails if |
| --- | --- |
| Graphic size | not exactly 1080 x 1080 |
| Year / make / model | the description names a different unit than the listing |
| Sale price | the price in the copy is not the listing's price |
| Savings | missing, or below the configured floor |
| Price arithmetic | `Was − On Sale` does not equal `You Save` |
| In stock | the listing is not `schema.org/InStock` |
| Listing freshness | the listing was read more than 24h ago |
| Duplicate | this unit already appears in history, `inbox/`, `ready/` or `published/` |
| Exact-unit photo | the listing has no photo of this unit (catalog images do not count) |
| Phone | `778-653-5702` missing |
| Required phrases | `International Motorsports` or `WE SHIP EVERYWHERE` missing |
| Inventory URL | missing from the description |

## Description rules

- Must contain the inventory URL, `778-653-5702`, `International Motorsports`,
  `WE SHIP EVERYWHERE`.
- Prices must match the listing exactly. Never round, never estimate.
- Never claim stock levels or availability counts for new units.
- Never invent a spec, model year, trim, colour, promotion or finance rate.
- Keep it under 1200 characters — Google Business Profile caps at 1500, other
  channels at 2200.
- `Peace & Grease Since 2003` is expected; its absence is a warning, not a blocker.

## Graphic rules

- Exactly 1080 x 1080, white background.
- The photo must be of the **exact advertised unit**, taken from that unit's listing.
  Never a similar unit, never manufacturer catalog photography, never an AI-generated
  motorcycle.
- Must show year, make, model, sale price, savings, and IM branding.
- Footer: the IM logo, `Call or Text 778-653-5702`, `WE SHIP EVERYWHERE`.

## Brief for ChatGPT

```
Build one daily ad package for International Motorsports.

1. Open https://www.internationalmotorsports.com/search/inventory/availability/In%20Stock/usage/New/sort/discount
2. Take the highest-discount unit that has a real photo of itself on its listing.
3. Open that unit's page and copy down, exactly: year, make, model, trim, colour,
   stock number, On Sale price, Was price, Save amount, and the listing URL.
4. Build a 1080x1080 white-background graphic using THAT unit's own photo from
   THAT listing. Never substitute a similar unit, a catalog photo, or a generated
   motorcycle. Show year/make/model, the sale price, the savings, IM branding, and
   a footer with the logo, "Call or Text 778-653-5702" and "WE SHIP EVERYWHERE".
5. Write description.txt using only the figures you copied in step 3. Include the
   listing URL, the phone number, "International Motorsports", "WE SHIP EVERYWHERE"
   and "Peace & Grease Since 2003". Under 1200 characters. Invent nothing.
6. Write brief.json: {"inventory_url": "<the listing URL from step 3>"}
7. Hand all three to Codex to commit to
   inbox/<today>_<stock-number>_<year-make-model-trim>/ and push to main.

If the top unit has no photo of itself, move to the next one and say which you
skipped and why. Do not fill any gap by guessing.
```
