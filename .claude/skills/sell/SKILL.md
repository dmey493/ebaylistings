---
name: sell
description: Sell an item on eBay from photos. Use when the user says "sell this", "/sell", "list this on eBay", or gives photo file paths (optionally with notes about the item). Creates an ebaylister job, drafts the listing, and tells the user what (if anything) is still needed before it can be published.
---

The user wants to sell something. Arguments: `$ARGUMENTS`

Interpret the arguments: every token that is an existing image file path is a photo; everything else is the seller's note about the item (condition, what's included, size, "works fine", ...). If no photo paths were given, ask for them once and stop: eBay needs real image FILES to upload, so a picture pasted into the chat is not enough. Photos usually arrive on this machine via phone sync (iCloud Photos, Google Photos/Drive, Dropbox, AirDrop).

Run every `ebaylister` command with the project's virtualenv active (`source .venv/bin/activate` in this repo if `ebaylister` is not on PATH).

## 1. Create the job

```
ebaylister job new <photo paths...> -n "<note, or omit -n if none>"
```

It prints the job id.

## 2. Draft the listing

Invoke the `sell-job` skill with that job id (use the Skill tool: `sell-job <job id>`). It looks at the photos, identifies the item, and writes the title / description / item specifics / price. If eBay is not connected yet the draft still gets written; category and comps are filled in at publish time.

## 3. Report readiness

Run `ebaylister doctor`. It prints a checklist and a numbered to-do list.

Then tell the user, in this order:
1. The draft: title, price, condition, and anything the identification was unsure about (they may want to add a note and re-run `/sell-job <id>`).
2. If the doctor says everything is ready: "Say **publish** and I will run `ebaylister publish <id>`, or edit first with `--price` / `--title`." Do not publish without the user saying so.
3. If not ready: the doctor's numbered to-do list, verbatim, plus "then run `ebaylister publish <id>`". Do not attempt to publish.
