"""Browse API: pull comparable active listings for pricing guidance.

Sold/completed prices need the (restricted) Marketplace Insights API, so this
uses active Buy-It-Now listings, which are freely queryable with an app token.
"""

from __future__ import annotations

import statistics

from ..models import Comp, PriceStats
from .client import EbayClient


def price_comps(client: EbayClient, query: str, category_id: str | None = None, limit: int = 30) -> PriceStats:
    params = {"q": query, "limit": str(limit), "filter": "buyingOptions:{FIXED_PRICE}"}
    if category_id:
        params["category_ids"] = category_id
    try:
        data = client.get_json("/buy/browse/v1/item_summary/search", user=False, params=params)
    except Exception:  # comps are advisory; never fail the pipeline over them
        return PriceStats(query=query, count=0)

    comps: list[Comp] = []
    for it in data.get("itemSummaries", []):
        price = it.get("price") or {}
        try:
            value = float(price.get("value"))
        except (TypeError, ValueError):
            continue
        comps.append(
            Comp(
                title=it.get("title", ""),
                price=value,
                currency=price.get("currency", "USD"),
                condition=it.get("condition"),
                url=it.get("itemWebUrl"),
            )
        )
    if not comps:
        return PriceStats(query=query, count=0)

    values = sorted(c.price for c in comps)
    # Trim the extremes so a $1 "for parts" or a $9,999 troll listing does not skew things.
    if len(values) >= 8:
        cut = len(values) // 8
        values = values[cut : len(values) - cut]
    return PriceStats(
        query=query,
        count=len(comps),
        low=values[0],
        median=statistics.median(values),
        high=values[-1],
        currency=comps[0].currency,
        samples=comps[:10],
    )
