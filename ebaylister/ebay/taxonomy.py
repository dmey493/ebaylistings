"""Taxonomy API: find the right leaf category and its required item specifics."""

from __future__ import annotations

from functools import lru_cache

from ..models import CategoryPick
from .client import EbayClient


@lru_cache(maxsize=8)
def _tree_id(client: EbayClient, marketplace_id: str) -> str:
    data = client.get_json(
        "/commerce/taxonomy/v1/get_default_category_tree_id", user=False, params={"marketplace_id": marketplace_id}
    )
    return str(data["categoryTreeId"])


def suggest_categories(client: EbayClient, query: str, limit: int = 5) -> list[dict]:
    tree = _tree_id(client, client.s.ebay_marketplace_id)
    data = client.get_json(f"/commerce/taxonomy/v1/category_tree/{tree}/get_category_suggestions", user=False, params={"q": query})
    out = []
    for s in data.get("categorySuggestions", [])[:limit]:
        cat = s.get("category", {})
        ancestors = s.get("categoryTreeNodeAncestors", [])
        path = " > ".join(a.get("categoryName", "") for a in reversed(ancestors)) if ancestors else ""
        out.append({"id": str(cat.get("categoryId")), "name": cat.get("categoryName", ""), "path": path})
    return out


def aspects_for_category(client: EbayClient, category_id: str) -> tuple[list[str], list[str]]:
    tree = _tree_id(client, client.s.ebay_marketplace_id)
    data = client.get_json(
        f"/commerce/taxonomy/v1/category_tree/{tree}/get_item_aspects_for_category",
        user=False,
        params={"category_id": category_id},
    )
    required, recommended = [], []
    for a in data.get("aspects", []):
        name = a.get("localizedAspectName")
        c = a.get("aspectConstraint", {})
        if not name:
            continue
        if c.get("aspectRequired"):
            required.append(name)
        elif c.get("aspectUsage") == "RECOMMENDED":
            recommended.append(name)
    return required, recommended


def pick_category(client: EbayClient, query: str) -> CategoryPick:
    suggestions = suggest_categories(client, query)
    if not suggestions:
        raise LookupError(f"eBay returned no category suggestions for {query!r}")
    top = suggestions[0]
    required, recommended = aspects_for_category(client, top["id"])
    return CategoryPick(
        category_id=top["id"],
        category_name=top["name"],
        path=top["path"],
        required_aspects=required,
        recommended_aspects=recommended[:15],
    )
