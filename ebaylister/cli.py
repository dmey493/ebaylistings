"""Command-line entry point.

  ebaylister auth                       one-time: link your eBay account
  ebaylister setup                      one-time: pick business policies, create ship-from location
  ebaylister sell photo1.jpg ... [-n "note"] [--publish]
  ebaylister review [JOB_ID]            show a draft
  ebaylister publish JOB_ID             publish a reviewed draft
  ebaylister jobs                       list recent jobs
  ebaylister watch                      poll data/inbox/<folder>/ for new photo sets
  ebaylister serve                      run the phone-friendly web UI
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import time
from pathlib import Path

from .config import Settings, load_settings
from .models import Job


def _pipeline(s: Settings):
    from .pipeline import Pipeline

    return Pipeline(s)


def _print_job(job: Job) -> None:
    print(f"job {job.id}  status={job.status}")
    if job.identified:
        i = job.identified
        print(f"  identified : {i.product_name}  [{i.condition}, confidence {i.confidence:.0%}]")
        if i.uncertainties:
            print(f"  unsure     : {'; '.join(i.uncertainties)}")
    if job.category:
        print(f"  category   : {job.category.category_name} ({job.category.category_id})")
    if job.prices and job.prices.count:
        p = job.prices
        print(f"  comps      : {p.count} active, {p.currency} {p.low:.2f} / {p.median:.2f} / {p.high:.2f} (low/median/high)")
    if job.draft:
        d = job.draft
        print(f"  title      : {d.title}")
        print(f"  price      : {d.price:.2f}  ({d.price_rationale})")
        print(f"  condition  : {d.condition} - {d.condition_description}")
        if d.aspects:
            print("  specifics  : " + ", ".join(f"{a.name}={'/'.join(a.values)}" for a in d.aspects))
    if job.result:
        print(f"  LIVE       : {job.result.listing_url}")
    if job.error:
        print(f"  error      : {job.error}")


# ---------- commands ----------

def cmd_auth(s: Settings, args) -> int:
    from .ebay.auth import EbayAuth

    missing = s.missing_ebay_credentials()
    if missing:
        print(f"Set {', '.join(missing)} in .env first (see .env.example).", file=sys.stderr)
        return 2
    auth = EbayAuth(s)
    print("1. Open this URL, sign in to eBay, and click Agree:\n")
    print("   " + auth.consent_url() + "\n")
    print("2. You will land on your redirect page. Paste the full URL from the address bar (or just the code) here.")
    code = input("\n   code / URL: ").strip()
    auth.exchange_code(code)
    print(f"\nLinked. Tokens saved to {s.token_path} ({s.ebay_env}).")
    return 0


def cmd_setup(s: Settings, args) -> int:
    from .ebay import EbayClient
    from .ebay import inventory

    client = EbayClient(s)
    if not client.auth.has_user_token:
        print("Run `ebaylister auth` first.", file=sys.stderr)
        return 2

    policies = inventory.list_policies(client)
    chosen = {}
    for kind in ("fulfillment", "payment", "return"):
        items = policies[kind]
        if not items:
            print(
                f"\nNo {kind} policy found. Create one at eBay > Account > Business Policies "
                "(and opt in to business policies if prompted), then rerun setup."
            )
            return 2
        print(f"\n{kind.title()} policies:")
        for i, p in enumerate(items, 1):
            print(f"  {i}. {p['name']}  ({p['id']})")
        pick = 1 if len(items) == 1 else int(input(f"Choose {kind} policy [1-{len(items)}]: ") or 1)
        chosen[kind] = items[pick - 1]["id"]

    print("\nShip-from address (used to create the inventory location if it does not exist):")
    address = {
        "addressLine1": input("  street: ").strip(),
        "city": input("  city: ").strip(),
        "stateOrProvince": input("  state/province: ").strip(),
        "postalCode": input("  postal code: ").strip(),
        "country": (input("  country (ISO-2, default US): ").strip() or "US").upper(),
    }
    inventory.ensure_location(client, s.merchant_location_key, address)

    print("\nAdd these lines to your .env:\n")
    print(f"EBAY_FULFILLMENT_POLICY_ID={chosen['fulfillment']}")
    print(f"EBAY_PAYMENT_POLICY_ID={chosen['payment']}")
    print(f"EBAY_RETURN_POLICY_ID={chosen['return']}")
    print(f"EBAY_MERCHANT_LOCATION_KEY={s.merchant_location_key}")
    return 0


def cmd_sell(s: Settings, args) -> int:
    photos = [Path(p) for p in args.photos]
    for p in photos:
        if not p.is_file():
            print(f"not a file: {p}", file=sys.stderr)
            return 2
    job = _pipeline(s).run(photos, note=args.note or "", publish=True if args.publish else None)
    _print_job(job)
    if job.status == "awaiting_review":
        print(f"\nLooks right?  ebaylister publish {job.id}")
    return 0 if job.status != "failed" else 1


def cmd_review(s: Settings, args) -> int:
    from .storage import JobStore

    store = JobStore(s)
    job = store.load(args.job_id) if args.job_id else (store.list(1) or [None])[0]
    if job is None:
        print("no jobs yet")
        return 1
    _print_job(job)
    if args.json:
        print(job.model_dump_json(indent=2))
    elif job.draft:
        print("\n--- description ---\n" + job.draft.description_html)
    return 0


def cmd_publish(s: Settings, args) -> int:
    from .storage import JobStore

    pipe = _pipeline(s)
    job = JobStore(s).load(args.job_id)
    if args.price:
        job.draft.price = float(args.price)
    if args.title:
        job.draft.title = args.title[:80]
    job = pipe.publish(job)
    _print_job(job)
    return 0 if job.status == "published" else 1


def cmd_jobs(s: Settings, args) -> int:
    from .storage import JobStore

    for job in JobStore(s).list(args.limit):
        title = job.draft.title if job.draft else (job.identified.product_name if job.identified else "-")
        print(f"{job.id}  {job.status:16s} {title}")
    return 0


def cmd_watch(s: Settings, args) -> int:
    """Poll data/inbox/. Each sub-folder = one item; drop photos + optional note.txt in it.
    Handy when your phone auto-syncs photos to a folder (Dropbox / Drive / iCloud)."""
    from .storage import photos_in

    inbox = s.inbox_dir
    inbox.mkdir(parents=True, exist_ok=True)
    done = inbox / "_done"
    done.mkdir(exist_ok=True)
    pipe = _pipeline(s)
    print(f"Watching {inbox} - put each item's photos in its own sub-folder (Ctrl-C to stop).")
    while True:
        for folder in sorted(p for p in inbox.iterdir() if p.is_dir() and not p.name.startswith("_")):
            photos = photos_in(folder)
            if not photos:
                continue
            # wait until the folder has been quiet for a bit so we do not grab half-synced files
            newest = max(p.stat().st_mtime for p in photos)
            if time.time() - newest < args.settle:
                continue
            note_file = folder / "note.txt"
            note = note_file.read_text().strip() if note_file.is_file() else ""
            print(f"\n-> {folder.name}: {len(photos)} photo(s)")
            job = pipe.run(photos, note=note)
            _print_job(job)
            shutil.move(str(folder), str(done / f"{folder.name}-{job.id}"))
        time.sleep(args.interval)


def cmd_serve(s: Settings, args) -> int:
    import uvicorn

    from .web import create_app

    uvicorn.run(create_app(s), host=args.host, port=args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ebaylister", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--env-file", default=".env")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("auth", help="link your eBay account").set_defaults(fn=cmd_auth)
    sub.add_parser("setup", help="choose business policies and create ship-from location").set_defaults(fn=cmd_setup)

    sp = sub.add_parser("sell", help="photos -> draft (-> publish)")
    sp.add_argument("photos", nargs="+")
    sp.add_argument("-n", "--note", help="anything you know: 'works fine, no charger', 'size M', ...")
    sp.add_argument("--publish", action="store_true", help="publish immediately without review")
    sp.set_defaults(fn=cmd_sell)

    sp = sub.add_parser("review", help="show a draft")
    sp.add_argument("job_id", nargs="?")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_review)

    sp = sub.add_parser("publish", help="publish a reviewed draft")
    sp.add_argument("job_id")
    sp.add_argument("--price", help="override the price")
    sp.add_argument("--title", help="override the title")
    sp.set_defaults(fn=cmd_publish)

    sp = sub.add_parser("jobs", help="list recent jobs")
    sp.add_argument("--limit", type=int, default=20)
    sp.set_defaults(fn=cmd_jobs)

    sp = sub.add_parser("watch", help="poll data/inbox for new photo folders")
    sp.add_argument("--interval", type=float, default=10.0)
    sp.add_argument("--settle", type=float, default=20.0, help="seconds a folder must be idle before it is picked up")
    sp.set_defaults(fn=cmd_watch)

    sp = sub.add_parser("serve", help="run the web UI")
    sp.add_argument("--host", default="0.0.0.0")
    sp.add_argument("--port", type=int, default=8000)
    sp.set_defaults(fn=cmd_serve)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    settings = load_settings(Path(args.env_file))
    try:
        return args.fn(settings, args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
