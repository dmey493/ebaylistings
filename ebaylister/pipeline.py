"""Orchestration: photos -> identification -> category + comps -> draft -> (review) -> publish."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from .config import Settings
from .ebay import EbayClient
from .ebay import browse, inventory, media, taxonomy
from .identify import ListingWriter
from .models import Job, PublishResult
from .storage import JobStore

log = logging.getLogger("ebaylister")


def _sku_for(job: Job) -> str:
    base = re.sub(r"[^A-Za-z0-9]+", "-", (job.identified.brand or "item") if job.identified else "item").strip("-")[:20]
    return f"{base}-{job.id}".upper()[:50]


class Pipeline:
    def __init__(self, settings: Settings, store: JobStore | None = None, writer: ListingWriter | None = None, ebay: EbayClient | None = None):
        self.s = settings
        self.store = store or JobStore(settings)
        self.writer = writer or ListingWriter(settings)
        self.ebay = ebay or EbayClient(settings)

    # ---- step 1: photos -> reviewed draft ----
    def draft(self, job: Job) -> Job:
        try:
            job.status = "identifying"
            self.store.save(job)
            job.identified = self.writer.identify([Path(p) for p in job.photos], job.note)
            log.info("[%s] identified: %s (%.0f%%)", job.id, job.identified.product_name, job.identified.confidence * 100)

            job.status = "drafting"
            self.store.save(job)
            job.category = taxonomy.pick_category(self.ebay, job.identified.category_search_query)
            job.prices = browse.price_comps(self.ebay, job.identified.comps_search_query, job.category.category_id)
            job.draft = self.writer.draft(job.identified, job.category, job.prices, job.note)
            job.status = "awaiting_review"
            log.info("[%s] draft ready: %r @ %s %.2f", job.id, job.draft.title, job.prices.currency, job.draft.price)
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
