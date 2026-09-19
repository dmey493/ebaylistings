"""Inventory + Account APIs: the three-step publish and its one-time prerequisites.

Publishing an item with the Inventory API is:
  1. PUT  /sell/inventory/v1/inventory_item/{sku}   - the product itself
  2. POST /sell/inventory/v1/offer                  - price, category, policies
  3. POST /sell/inventory/v1/offer/{offerId}/publish - goes live, returns listingId

One-time prerequisites on the seller account:
  * business policies (fulfillment / payment / return) - Account API
  * an inventory location (where items ship from)     - Inventory API
"""

from __future__ import annotations

from ..config import Settings
from ..models import ListingDraft, PublishResult
from .client import EbayClient, EbayError


# ---------- prerequisites ----------

def list_policies(client: EbayClient) -> dict[str, list[dict]]:
    mp = {"marketplace_id": client.s.ebay_marketplace_id}
    out: dict[str, list[dict]] = {}
    for kind, key in (("fulfillment", "fulfillmentPolicies"), ("payment", "paymentPolicies"), ("return", "returnPolicies")):
        data = client.get_json(f"/sell/account/v1/{kind}_policy", params=mp)
        out[kind] = [
            {"id": p.get(f"{kind}PolicyId"), "name": p.get("name"), "description": p.get("description", "")}
            for p in data.get(key, [])
        ]
    return out


def ensure_location(client: EbayClient, key: str, address: dict, name: str = "Home") -> None:
    """Create the merchant location if it does not exist. `address` needs at least
    addressLine1, city, stateOrProvince, postalCode, country (ISO-2)."""
    try:
        client.get_json(f"/sell/inventory/v1/location/{key}")
        return
    except EbayError as e:
        if e.status != 404:
            raise
    body = {
        "location": {"address": address},
        "locationTypes": ["WAREHOUSE"],
        "name": name,
        "merchantLocationStatus": "ENABLED",
    }
    client.request("POST", f"/sell/inventory/v1/location/{key}", json=body, ok=(204,))


# ---------- publishing ----------

def _inventory_item_body(draft: ListingDraft, image_urls: list[str]) -> dict:
    product = {
        "title": draft.title[:80],
        "description": draft.description_html,
        "imageUrls": image_urls[:24],
        "aspects": {a.name: a.values for a in draft.aspects if a.values},
    }
    body = {
        "product": product,
        "condition": draft.condition,
        "availability": {"shipToLocationAvailability": {"quantity": draft.quantity}},
    }
    if draft.condition_description and draft.condition != "NEW":
        body["conditionDescription"] = draft.condition_description[:1000]
    return body


def _offer_body(s: Settings, sku: str, draft: ListingDraft, category_id: str, currency: str) -> dict:
    return {
        "sku": sku,
        "marketplaceId": s.ebay_marketplace_id,
        "format": "FIXED_PRICE",
        "availableQuantity": draft.quantity,
        "categoryId": category_id,
        "listingDescription": draft.description_html,
        "merchantLocationKey": s.merchant_location_key,
        "pricingSummary": {"price": {"value": f"{draft.price:.2f}", "currency": currency}},
        "listingPolicies": {
            "fulfillmentPolicyId": s.fulfillment_policy_id,
            "paymentPolicyId": s.payment_policy_id,
            "returnPolicyId": s.return_policy_id,
        },
    }


def _existing_offer_id(client: EbayClient, sku: str) -> str | None:
    try:
        data = client.get_json("/sell/inventory/v1/offer", params={"sku": sku, "marketplace_id": client.s.ebay_marketplace_id})
    except EbayError as e:
        if e.status == 404:
            return None
        raise
    offers = data.get("offers") or []
    return offers[0].get("offerId") if offers else None


def publish_listing(
    client: EbayClient,
    sku: str,
    draft: ListingDraft,
    category_id: str,
    image_urls: list[str],
    currency: str = "USD",
) -> PublishResult:
    s = client.s
    client.request("PUT", f"/sell/inventory/v1/inventory_item/{sku}", json=_inventory_item_body(draft, image_urls), ok=(200, 201, 204))

    offer_body = _offer_body(s, sku, draft, category_id, currency)
    offer_id = _existing_offer_id(client, sku)
    if offer_id:
        client.request("PUT", f"/sell/inventory/v1/offer/{offer_id}", json=offer_body, ok=(200, 204))
    else:
        r = client.request("POST", "/sell/inventory/v1/offer", json=offer_body, ok=(201,))
        offer_id = r.json()["offerId"]

    r = client.request("POST", f"/sell/inventory/v1/offer/{offer_id}/publish", ok=(200,))
    listing_id = str(r.json()["listingId"])
    return PublishResult(
        sku=sku,
        offer_id=offer_id,
        listing_id=listing_id,
        listing_url=s.listing_url_base + listing_id,
        image_urls=image_urls,
    )
