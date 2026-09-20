---
name: sell-job
description: Turn the photos in an ebaylister job into an eBay listing draft (identify the item from the photos, then write title/description/specifics/price). Use when asked to run, draft, or process an ebaylister job by id, e.g. "/sell-job 20260919-101500-ab12".
---

You are drafting an eBay listing for a private seller. The job id is: `$ARGUMENTS`

Everything below is done with `ebaylister job ...` commands (Bash), the Read tool (to look at photos) and the Write tool (to save your JSON). Do not ask questions; the seller is not watching. If something is truly impossible, stop and say why - the Python side records the failure.

## Step 1 - load the job

Run `ebaylister job show $ARGUMENTS`. It prints JSON with `photos` (file paths), `note` (what the seller told us, may be empty) and the job folder path is the parent of those photos.

## Step 2 - look at every photo

Use the Read tool on each photo path. Read labels, model numbers, packaging text, and look for wear, scratches, missing parts, whether the box/accessories are present.

## Step 3 - write the identification

Run `ebaylister schema identified` once to get the exact JSON schema, then Write the JSON to `<job folder>/identified.json`. Rules:

- Identify as precisely as the photos allow. Never invent a brand, model, size, or feature you cannot see or infer with good reason; put doubts in `uncertainties`.
- `condition` is one of the eBay enums in the schema. Be honest; downgrade for visible wear, scratches, missing parts, or no box. eBay buyers open disputes over undisclosed flaws.
- `category_search_query`: 2-6 generic words for category lookup (e.g. `wireless over-ear headphones`).
- `comps_search_query`: what a buyer would type to find this exact item (e.g. `Sony WH-1000XM4`).
- Use the seller's note as ground truth for things you cannot see (works / doesn't, size, what's included).

Then run `ebaylister job identified $ARGUMENTS <job folder>/identified.json`. It stores your identification and prints JSON with:

- `category`: the eBay category chosen, with `required_aspects` and `recommended_aspects` (item specifics names)
- `comps`: comparable active listings (`low`, `median`, `high`, `samples`), or `count: 0` if none were found

If it prints an error, fix your JSON and run it again.

## Step 4 - write the listing

Run `ebaylister schema draft` once for the exact schema, then Write the JSON to `<job folder>/draft.json`. Rules:

- `title`: at most 80 characters. Lead with brand + model + the attribute buyers search for. Title case. No hype words, no ALL CAPS, no punctuation spam, no emojis.
- `aspects`: fill EVERY name in `required_aspects`, using the exact aspect names given; add `recommended_aspects` you can determine. If a required value truly cannot be known, use `Unbranded` for Brand, `Does not apply` for MPN/UPC-style aspects, otherwise `Unknown`.
- `price`: a competitive Buy-It-Now price. Anchor on the comps' `median`, adjust for this item's condition and completeness. With no comps, use what you know of the item's typical resale value. `price_rationale` is one sentence.
- `description_html`: short HTML (`<p>`, `<ul>`/`<li>`, `<b>`). Sections: what it is, condition (repeat every flaw), what's included. Do not mention shipping or returns (business policies cover that). Do not claim anything that contradicts `uncertainties`.
- `condition_description`: plain text, the flaws and wear in 1-3 sentences.
- `condition` must match the identification unless the note changes it.

Then run `ebaylister job draft $ARGUMENTS <job folder>/draft.json`. It validates the draft (including required item specifics), stores it, and prints a summary. If it prints an error, fix the JSON and run it again.

## Done

The job is now `awaiting_review`. Do NOT publish; the seller (or the auto-publish setting) handles that. Finish with a two-line summary: the title and the price.
