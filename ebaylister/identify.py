"""The two Claude calls: (1) look at the photos and identify the item,
(2) turn that plus eBay's category/aspects/comps into a finished listing."""

from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path

import anthropic

from .config import Settings
from .models import CategoryPick, IdentifiedItem, ListingDraft, PriceStats

FALLBACK_BETAS = ["server-side-fallback-2026-07-01"]

IDENTIFY_SYSTEM = """You help a private seller list second-hand items on eBay.
You will be shown the seller's own photos of ONE item, plus an optional note from the seller.
Identify the item as precisely as the photos allow. Read any labels, model numbers, or packaging text.
Assess condition honestly from what is visible - eBay buyers open disputes over undisclosed flaws.
Never invent a brand, model, size, or feature you cannot see or infer with good reason; put doubts in `uncertainties`."""

DRAFT_SYSTEM = """You write eBay listings for a private seller. You are given: the item identification from the photos,
the seller's note, the eBay category with its REQUIRED and recommended item specifics, and prices of comparable active listings.
Write a listing that is accurate, buyer-friendly, and complete.

Rules:
- Title: at most 80 characters. Lead with brand + model + the attribute buyers search for. Title case. No hype words, no punctuation spam.
- Fill EVERY required item specific. Use the exact aspect names given. If a required value truly cannot be known, use "Unbranded" for Brand, "Does not apply" for MPN/UPC-style aspects, otherwise "Unknown".
- Price: a competitive Buy-It-Now price. Anchor on the comps' median, adjust for this item's condition and completeness. If there are no comps, use your knowledge of the item's typical resale value.
- Description: short HTML (<p>, <ul>/<li>, <b>). Sections: what it is, condition (repeat every flaw), what's included, shipping/returns line is NOT needed (policies handle it).
- Do not claim anything that contradicts the identification's uncertainties."""


def _image_block(path: Path) -> dict:
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    if mime not in {"image/jpeg", "image/png", "image/gif", "image/webp"}:
        mime = "image/jpeg"
    data = base64.standard_b64encode(path.read_bytes()).decode()
    return {"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}}


class ListingWriter:
    def __init__(self, settings: Settings, client: anthropic.Anthropic | None = None):
        self.s = settings
        self.client = client or anthropic.Anthropic()

    def _parse(self, *, system: str, content: list | str, output_format, effort: str):
        response = self.client.beta.messages.parse(
            model=self.s.anthropic_model,
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": content}],
            thinking={"type": "adaptive"},
            output_config={"effort": effort},
            output_format=output_format,
            betas=FALLBACK_BETAS,
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            detail = getattr(response, "stop_details", None)
            raise RuntimeError(f"Claude declined this request ({getattr(detail, 'category', None)}): {getattr(detail, 'explanation', '')}")
        if response.parsed_output is None:
            raise RuntimeError(f"Claude returned no structured output (stop_reason={response.stop_reason})")
        return response.parsed_output

    def identify(self, photos: list[Path], note: str = "") -> IdentifiedItem:
        if not photos:
            raise ValueError("At least one photo is required")
        content: list = [_image_block(p) for p in photos]
        text = f"{len(photos)} photo(s) of the item are attached."
        if note.strip():
            text += f"\n\nSeller's note: {note.strip()}"
        text += "\n\nIdentify the item and assess its condition."
        content.append({"type": "text", "text": text})
        return self._parse(system=IDENTIFY_SYSTEM, content=content, output_format=IdentifiedItem, effort="high")

    def draft(self, item: IdentifiedItem, category: CategoryPick, prices: PriceStats, note: str = "") -> ListingDraft:
        comps_text = (
            json.dumps(
                {
                    "query": prices.query,
                    "active_listings_found": prices.count,
                    "low": prices.low,
                    "median": prices.median,
                    "high": prices.high,
                    "currency": prices.currency,
                    "samples": [s.model_dump(exclude={"url"}) for s in prices.samples],
                },
                indent=2,
            )
            if prices.count
            else "No comparable active listings were found."
        )
        text = f"""## Identification (from photos)
{item.model_dump_json(indent=2)}

## Seller's note
{note.strip() or "(none)"}

## eBay category
{category.category_name} (id {category.category_id}){f' - {category.path}' if category.path else ''}
REQUIRED item specifics: {json.dumps(category.required_aspects)}
Recommended item specifics: {json.dumps(category.recommended_aspects)}

## Comparable active listings
{comps_text}

Write the listing."""
        draft = self._parse(system=DRAFT_SYSTEM, content=text, output_format=ListingDraft, effort="high")
        draft.title = draft.title.strip()[:80]
        return draft
