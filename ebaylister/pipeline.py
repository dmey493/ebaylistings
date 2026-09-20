"""Orchestration: photos -> identification -> category + comps -> draft -> (review) -> publish.

The "brain" (identify + write) is pluggable:
  * claude-code: a headless Claude Code run (`claude -p`) executes the `sell-job` skill in
    .claude/skills/, which calls back into `ebaylister job ...` to store its results.
    This runs on a Claude subscription plan - no API key.
  * api: direct Messages API calls via ListingWriter (needs ANTHROPIC_API_KEY).
Everything eBay-side is identical for both.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path

from .config import Settings
from .ebay import EbayClient
from .ebay import browse, inventory, media, taxonomy
from .models import CategoryPick, IdentifiedItem, Job, ListingDraft, PriceStats, PublishResult
from .storage import JobStore

log = logging.getLogger("ebaylister")

# Tools the headless Claude Code run may use without prompting: look at photos,
# write its JSON files into the job folder, and call back into this CLI.
CLAUDE_CODE_ALLOWED_TOOLS = "Read,Write,Bash(ebaylister *)"


def _sku_for(job: Job) -> str:
    base = re.sub(r"[^A-Za-z0-9]+", "-", (job.identified.brand or "item") if job.identified else "item").strip("-")[:20]
    return f"{base}-{job.id}".upper()[:50]


class Pipeline:
    def __init__(self, settings: Settings, store: JobStore | None = None, writer=None, ebay: EbayClient | None = None):
        self.s = settings
        self.store = store or JobStore(settings)
        self._writer = writer
        self._ebay = ebay

    # Both clients are built lazily so that e.g. `ebaylister job new` never needs credentials.
    @property
    def ebay(self) -> EbayClient:
        if self._ebay is None:
            self._ebay = EbayClient(self.s)
        return self._ebay

    @property
    def writer(self):
        if self._writer is None:
            from .identify import ListingWriter

            self._writer = ListingWriter(self.s)
        return self._writer

    # ---------- individual steps (also driven externally by the sell-job skill) ----------

    def _lookup_category_and_comps(self, job: Job) -> None:
        job.category = taxonomy.pick_category(self.ebay, job.identified.category_search_query)
        job.prices = browse.price_comps(self.ebay, job.identified.comps_search_query, job.category.category_id)

    def set_identified(self, job: Job, item: IdentifiedItem) -> Job:
        """Store the identification, then look up category, required aspects and comps.

        If the eBay app keys are not configured yet, drafting still proceeds with a
        placeholder category and no comps; the real lookup happens at publish time."""
        job.identified = item
        job.status = "drafting"
        job.error = None
        self.store.save(job)
        if self.s.missing_ebay_credentials():
            log.warning("[%s] eBay keys not configured; drafting without category/comps", job.id)
            job.category = CategoryPick(category_id="", category_name="(eBay not connected yet - category chosen at publish time)")
            job.prices = PriceStats(query=item.comps_search_query, count=0)
        else:
            self._lookup_category_and_comps(job)
        self.store.save(job)
        log.info("[%s] identified: %s (%.0f%%) -> %s", job.id, item.product_name, item.confidence * 100, job.category.category_name)
        return job

    def set_draft(self, job: Job, draft: ListingDraft) -> Job:
        if job.identified is None or job.category is None:
            raise ValueError("Identify the item before drafting")
        draft.title = draft.title.strip()[:80]
        job.draft = draft
        job.status = "awaiting_review"
        job.error = None
        self.store.save(job)
        currency = job.prices.currency if job.prices else "USD"
        log.info("[%s] draft ready: %r @ %s %.2f", job.id, draft.title, currency, draft.price)
        return job

    # ---------- brains ----------

    def _draft_with_api(self, job: Job) -> Job:
        job.status = "identifying"
        self.store.save(job)
        item = self.writer.identify([Path(p) for p in job.photos], job.note)
        self.set_identified(job, item)
        return self.set_draft(job, self.writer.draft(job.identified, job.category, job.prices, job.note))

    def _draft_with_claude_code(self, job: Job) -> Job:
        exe = shutil.which(self.s.claude_cmd) or self.s.claude_cmd
        if not Path(exe).exists() and shutil.which(exe) is None:
            raise RuntimeError(
                f"Claude Code binary {self.s.claude_cmd!r} not found. Install Claude Code, or set "
                "EBAYLISTER_CLAUDE_CMD, or use EBAYLISTER_BRAIN=api with an API key."
            )
        job.status = "identifying"
        self.store.save(job)
        cmd = [
            exe, "-p", f"/sell-job {job.id}",
            "--allowedTools", CLAUDE_CODE_ALLOWED_TOOLS,
            "--add-dir", str(self.s.data_dir.resolve()),  # photos/job files may live outside the repo
        ]
        log.info("[%s] running headless Claude Code: %s", job.id, " ".join(cmd))
        proc = subprocess.run(cmd, cwd=self.s.repo_dir, capture_output=True, text=True, timeout=1800)
        (self.store.root / job.id / "claude-code.log").write_text(
            f"$ {' '.join(cmd)}\nexit={proc.returncode}\n\n--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
        )
        fresh = self.store.load(job.id)  # the skill wrote its results through `ebaylister job ...`
        if fresh.status != "awaiting_review":
            tail = (proc.stderr or proc.stdout).strip()[-600:]
            raise RuntimeError(
                f"Claude Code run ended with status {fresh.status!r} (exit {proc.returncode}). "
                f"See {self.store.root / job.id / 'claude-code.log'}. {tail}"
            )
        return fresh

    # ---- step 1: photos -> reviewed draft ----
    def draft(self, job: Job) -> Job:
        try:
            if self.s.brain == "api":
                job = self._draft_with_api(job)
            elif self.s.brain == "claude-code":
                job = self._draft_with_claude_code(job)
            else:
                raise ValueError(f"Unknown EBAYLISTER_BRAIN={self.s.brain!r} (use claude-code or api)")
        except Exception as e:  # keep the failure with the job so the UI/CLI can show it
            log.exception("[%s] drafting failed", job.id)
            job.status = "failed"
            job.error = f"{type(e).__name__}: {e}"
        self.store.save(job)
        return job

    # ---- step 2: publish an approved draft ----
    def publish(self, job: Job) -> Job:
        if job.draft is None or job.category is None:
            raise ValueError("Job has no draft to publish")
        missing = self.s.missing_seller_defaults()
        if missing:
            raise RuntimeError(f"Seller defaults not configured: {', '.join(missing)}. Run `ebaylister setup`.")
        try:
            job.status = "publishing"
            job.error = None
            self.store.save(job)
            if not job.category.category_id:  # drafted before eBay was connected
                self._lookup_category_and_comps(job)
                missing = [a for a in job.category.required_aspects if a not in {x.name for x in job.draft.aspects if x.values}]
                if missing:
                    raise RuntimeError(
                        f"eBay category {job.category.category_name!r} requires item specifics the draft lacks: "
                        f"{', '.join(missing)}. Re-run drafting now that eBay is connected: /sell-job {job.id}"
                    )
            image_urls = media.upload_images(self.ebay, [Path(p) for p in job.photos])
            currency = job.prices.currency if job.prices and job.prices.count else "USD"
            result: PublishResult = inventory.publish_listing(
                self.ebay, _sku_for(job), job.draft, job.category.category_id, image_urls, currency=currency
            )
            job.result = result
            job.status = "published"
            log.info("[%s] published %s", job.id, result.listing_url)
        except Exception as e:
            log.exception("[%s] publishing failed", job.id)
            job.status = "failed"
            job.error = f"{type(e).__name__}: {e}"
        self.store.save(job)
        return job

    # ---- convenience ----
    def run(self, photos: list[Path], note: str = "", publish: bool | None = None) -> Job:
        job = self.store.create(photos, note)
        job = self.draft(job)
        if job.status == "awaiting_review" and (self.s.auto_publish if publish is None else publish):
            job = self.publish(job)
        return job
