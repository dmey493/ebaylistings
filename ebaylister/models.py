"""Data models shared across the pipeline.

Two of these (IdentifiedItem, ListingDraft) double as the JSON schemas Claude
is asked to fill, so their field descriptions are written for the model.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

# eBay Inventory API condition enums (subset that covers typical used goods).
ConditionEnum = Literal[
    "NEW",
    "NEW_OTHER",
    "NEW_WITH_DEFECTS",
    "CERTIFIED_REFURBISHED",
    "SELLER_REFURBISHED",
    "LIKE_NEW",
    "USED_EXCELLENT",
    "USED_VERY_GOOD",
    "USED_GOOD",
    "USED_ACCEPTABLE",
    "FOR_PARTS_OR_NOT_WORKING",
]


class IdentifiedItem(BaseModel):
    """What Claude concludes from looking at the photos (pass 1)."""

    product_name: str = Field(description="Best full name of the item, e.g. 'Sony WH-1000XM4 Wireless Headphones'.")
    brand: str | None = Field(default=None, description="Brand or manufacturer if identifiable, else null.")
    model_or_part_number: str | None = Field(default=None, description="Model number / MPN / edition if visible or inferable, else null.")
    category_search_query: str = Field(description="Short phrase (2-6 words) to look up the right eBay category, e.g. 'wireless over-ear headphones'.")
    comps_search_query: str = Field(description="Query a buyer would type to find this exact item on eBay, e.g. 'Sony WH-1000XM4'.")
    condition: ConditionEnum = Field(description="eBay condition enum. Be honest; downgrade if you see wear, scratches, missing parts, or no box.")
    condition_notes: str = Field(description="1-3 sentences describing visible wear, damage, included accessories, and anything a buyer would want to know.")
    identifiable_text: list[str] = Field(default_factory=list, description="Any labels, serial/model strings, or packaging text visible in the photos.")
    key_features: list[str] = Field(default_factory=list, description="3-8 short bullet points a seller would list.")
    confidence: float = Field(ge=0, le=1, description="0-1: how sure you are of the identification.")
    uncertainties: list[str] = Field(default_factory=list, description="Things you could not determine from the photos (size, authenticity, functionality...).")


class Aspect(BaseModel):
    name: str
    values: list[str]


class ListingDraft(BaseModel):
    """The finished listing (pass 2). Maps almost 1:1 onto eBay Inventory API fields."""

    title: str = Field(description="eBay title, MAX 80 characters. Front-load brand + model + key attribute. No ALL CAPS, no emojis, no 'L@@K'.")
    description_html: str = Field(description="Buyer-facing description in simple HTML (<p>, <ul>, <li>, <b>). Include condition, what is included, and any flaws. Do not invent details.")
    condition: ConditionEnum
    condition_description: str = Field(description="Plain-text condition details shown under the condition badge (max 1000 chars).")
    aspects: list[Aspect] = Field(default_factory=list, description="Item specifics. Fill every REQUIRED aspect you were given; leave out any you cannot determine.")
    price: float = Field(gt=0, description="Buy-It-Now price in marketplace currency, informed by the comparable listings supplied.")
    price_rationale: str = Field(description="One sentence on how the price was chosen relative to comps.")
    quantity: int = Field(default=1, ge=1)


class Comp(BaseModel):
    title: str
    price: float
    currency: str
    condition: str | None = None
    url: str | None = None


class PriceStats(BaseModel):
    query: str
    count: int
    low: float | None = None
    median: float | None = None
    high: float | None = None
    currency: str = "USD"
    samples: list[Comp] = Field(default_factory=list)


class CategoryPick(BaseModel):
    category_id: str
    category_name: str
    path: str = ""
    required_aspects: list[str] = Field(default_factory=list)
    recommended_aspects: list[str] = Field(default_factory=list)


class PublishResult(BaseModel):
    sku: str
    offer_id: str
    listing_id: str
    listing_url: str
    image_urls: list[str]


class Job(BaseModel):
    """Persisted state for one 'sell this' request."""

    id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: Literal["queued", "identifying", "drafting", "awaiting_review", "publishing", "published", "failed"] = "queued"
    photos: list[str] = Field(default_factory=list)
    note: str = ""
    identified: IdentifiedItem | None = None
    category: CategoryPick | None = None
    prices: PriceStats | None = None
    draft: ListingDraft | None = None
    result: PublishResult | None = None
    error: str | None = None
