# ebaylister

Photograph something, say "sell this", and it ends up listed on your eBay account.

```
phone photos + optional note
        │
        ▼
 1. Claude looks at the photos ─────► what it is, brand/model, condition, flaws
        │
        ▼
 2. eBay Taxonomy API ──────────────► the right leaf category + its REQUIRED item specifics
    eBay Browse API ────────────────► prices of comparable active listings
        │
        ▼
 3. Claude writes the listing ──────► 80-char title, HTML description, item specifics, price
        │
        ▼
 4. (you glance at it - or skip this with AUTO_PUBLISH)
        │
        ▼
 5. eBay Media API ─────────────────► your photos hosted by eBay
    eBay Inventory API ─────────────► inventory item → offer → publish  ⇒  live listing URL
```

There are three ways to trigger it, all backed by the same pipeline:

| Trigger | When to use it |
|---|---|
| `ebaylister serve` + open the page on your phone | The "take a picture, tap a button" flow. The file input opens the camera directly. |
| `ebaylister sell photo1.jpg photo2.jpg -n "no charger"` | From a laptop, or scripting. |
| `ebaylister watch` | Your phone already syncs photos to a folder (Dropbox / Drive / iCloud). Drop each item's photos into `data/inbox/<anything>/` and it gets picked up. |

By default every run stops at **awaiting_review** and shows you the draft (title, price, condition, specifics). Publishing is one tap / one command. Set `EBAYLISTER_AUTO_PUBLISH=true` once you trust it.

## Which Claude pays for the thinking

The eBay half is plain Python. The "look at the photos and write the listing" half can run two ways, chosen with `EBAYLISTER_BRAIN`:

| `EBAYLISTER_BRAIN` | How it works | What it costs |
|---|---|---|
| `claude-code` (default) | Each job spawns a headless Claude Code run (`claude -p "/sell-job <id>"`) that executes the skill in `.claude/skills/sell-job/`. The skill reads the photos, writes its JSON, and hands it back through `ebaylister job ...`. | Covered by your **Claude Pro/Max plan**. No API key. Needs Claude Code installed and logged in on the machine that runs `ebaylister`. |
| `api` | Direct Messages API calls from `ebaylister/identify.py` with structured outputs. | Pay-as-you-go **API billing** via `ANTHROPIC_API_KEY`. |

A Claude.ai plan does not include Messages API access, and Anthropic's terms do not allow using plan credentials from your own app, which is why the default goes through Claude Code. Both brains produce the same `job.json`, so the review page, `publish`, `watch` and `serve` behave identically.

You can also skip the Python trigger entirely and just talk to Claude Code in this repo:

```
ebaylister job new IMG_1.jpg IMG_2.jpg -n "no charger"     # prints the job id
claude "/sell-job <that id>"                               # or type /sell-job <id> inside an open session
ebaylister publish <that id>
```

## One-time setup

### 1. eBay developer app (free)

1. Create a developer account at https://developer.ebay.com and create an application keyset. You get an **App ID (Client ID)** and **Cert ID (Client Secret)** for both *Sandbox* and *Production*.
2. Under the keyset, open **User Tokens → Get a Token from eBay via Your Application** and add a redirect URL. eBay gives it an **RuName**; that RuName is what goes in `EBAY_RUNAME` (eBay wants the RuName, not the URL, as `redirect_uri`). For a personal tool the "auth accepted" page can be anything you can read the address bar of.
3. Start in **sandbox**: create a sandbox test seller at https://developer.ebay.com/sandbox and list there first. Flip `EBAY_ENV=production` when it works.

### 2. Seller account prerequisites

The Inventory API needs three **business policies** (shipping, payment, returns) and a **ship-from location** on the seller account. `ebaylister setup` lists your policies, lets you pick one of each, and creates the location. If you have never used business policies, opt in once at *My eBay → Account → Business Policies* and create a shipping / return policy there (payment policy is created for you with managed payments).

### 3. Install and configure

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env      # fill in EBAY_CLIENT_ID/SECRET/RUNAME (and ANTHROPIC_API_KEY only for the api brain)
ebaylister auth           # opens the eBay consent URL, paste the redirect back -> tokens saved in data/
ebaylister setup          # pick policies, create location; paste the printed IDs into .env
claude --version          # claude-code brain: Claude Code must be installed and logged in (`claude` then /login)
```

Run `ebaylister` commands with the virtualenv active (or install it on PATH); the headless Claude Code run calls `ebaylister job ...` from inside the skill.

For an always-on box (the `watch` or `serve` commands on a home server) where nobody runs `/login`, mint a long-lived token once with `claude setup-token` and export it as `CLAUDE_CODE_OAUTH_TOKEN`. It authenticates with your plan and is the supported way to run Claude Code unattended.

## Day-to-day

```bash
ebaylister serve                      # then open http://<your-machine>:8000 on your phone
ebaylister sell IMG_1.jpg IMG_2.jpg -n "works, light scratches, no box"
ebaylister review                     # show the latest draft
ebaylister publish <JOB_ID> [--price 120 --title "..."]
ebaylister jobs
```

Each request is a folder under `data/jobs/<id>/` with the photos and a `job.json` holding every intermediate step (identification, category, comps, draft, result, or the error), so nothing is lost if a step fails and you can re-run `publish` after fixing the cause.

## How the pieces map to code

| File | Role |
|---|---|
| `.claude/skills/sell-job/SKILL.md` | The `claude-code` brain: the instructions Claude Code follows for one job (look at photos → `identified.json` → `draft.json`). |
| `ebaylister/identify.py` | The `api` brain: the same two steps as direct Messages API calls with structured outputs. |
| `ebaylister/models.py` | The schemas. Field descriptions double as instructions to the model. |
| `ebaylister/ebay/auth.py` | OAuth: user consent URL, code exchange, refresh, application token, token file. |
| `ebaylister/ebay/media.py` | Photo upload (`create_from_file` → eBay-hosted URL). |
| `ebaylister/ebay/taxonomy.py` | Category suggestion + required/recommended item specifics. |
| `ebaylister/ebay/browse.py` | Comparable active listings → low/median/high with outliers trimmed. |
| `ebaylister/ebay/inventory.py` | Policies, location, and the inventory item → offer → publish sequence. |
| `ebaylister/pipeline.py` | Orchestration, brain selection, and job state transitions. `set_identified` / `set_draft` are the two hooks the skill calls back into. |
| `ebaylister/cli.py`, `ebaylister/web.py` | The two front doors. |

## Things to know

- **Prices are from active listings, not sold ones.** Sold-price data needs eBay's restricted Marketplace Insights API. The median of active Buy-It-Now comps is a reasonable anchor, and Claude adjusts for condition. Override the price at review time if you disagree.
- **Photos are yours.** The pipeline only ever uploads the photos you took. It does not pull images from the web; eBay's policy (and copyright) is against that, and buyers trust real photos more anyway.
- **Claude is told not to invent.** Anything it cannot see (size, authenticity, whether it powers on) lands in `uncertainties` and is kept out of the description. Put what you know in the note; it is passed to both Claude calls.
- **Costs.** With the `claude-code` brain, one Claude Code run per item counts against your plan's usage. With the `api` brain, two calls on `claude-opus-5` per item, a few cents each. eBay API calls are free; eBay's normal final-value fees apply to sales.
- **eBay endpoints were written from the API reference, not verified live from this environment** (developer.ebay.com was unreachable when this was built). The sandbox run in step 3 is where any drift will show up; every error is surfaced with eBay's own message and parameter names.
- **Refusal fallback is on (api brain).** The Messages API calls opt into Anthropic's server-side fallback so a safety-classifier decline on the primary model reruns on a fallback model instead of failing the job.

## Tests

```bash
pytest
```

The suite mocks both Claude and eBay and covers OAuth, each eBay wrapper, the publish sequence (including reuse of an existing offer), pipeline state transitions, and the web endpoints.
