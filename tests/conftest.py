import json
from pathlib import Path

import pytest

from ebaylister.config import Settings
from ebaylister.ebay.auth import EbayAuth, TokenSet
from ebaylister.ebay.client import EbayClient
from ebaylister.models import Aspect, CategoryPick, IdentifiedItem, ListingDraft, PriceStats


class FakeResponse:
    def __init__(self, status=200, body=None, headers=None, text=""):
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self.text = text or (json.dumps(body) if body is not None else "")
        self.content = self.text.encode()

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class FakeSession:
    """Records requests; answers from a list of (method, path_substring) -> FakeResponse rules."""

    def __init__(self):
        self.rules = []
        self.calls = []

    def on(self, method, path, response):
        self.rules.append((method.upper(), path, response))
        return self

    def request(self, method, url, headers=None, json=None, params=None, files=None, timeout=None):
        self.calls.append({"method": method.upper(), "url": url, "headers": headers, "json": json, "params": params, "files": files})
        for m, p, resp in self.rules:
            if m == method.upper() and p in url:
                return resp() if callable(resp) else resp
        raise AssertionError(f"unexpected {method} {url}")

    def post(self, url, headers=None, data=None, timeout=None):
        self.calls.append({"method": "POST", "url": url, "headers": headers, "data": data})
        for m, p, resp in self.rules:
            if m == "POST" and p in url:
                return resp
        raise AssertionError(f"unexpected POST {url}")


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        ebay_client_id="cid",
        ebay_client_secret="secret",
        ebay_runame="My-RuName",
        fulfillment_policy_id="F1",
        payment_policy_id="P1",
        return_policy_id="R1",
        data_dir=tmp_path / "data",
    )


@pytest.fixture
def session() -> FakeSession:
    return FakeSession()


@pytest.fixture
def ebay(settings, session) -> EbayClient:
    auth = EbayAuth(settings, session)
    auth.tokens = TokenSet(access_token="user-tok", access_expires_at=9e12, refresh_token="r", refresh_expires_at=9e12, app_token="app-tok", app_expires_at=9e12)
    return EbayClient(settings, auth=auth, session=session)


@pytest.fixture
def photo(tmp_path) -> Path:
    p = tmp_path / "item.jpg"
    p.write_bytes(b"\xff\xd8\xff\xe0fakejpeg")
    return p


@pytest.fixture
def identified() -> IdentifiedItem:
    return IdentifiedItem(
        product_name="Sony WH-1000XM4 Headphones",
        brand="Sony",
        model_or_part_number="WH-1000XM4",
        category_search_query="wireless headphones",
        comps_search_query="Sony WH-1000XM4",
        condition="USED_GOOD",
        condition_notes="Light scuffs on the headband.",
        confidence=0.9,
    )


@pytest.fixture
def category() -> CategoryPick:
    return CategoryPick(category_id="112529", category_name="Headphones", path="Consumer Electronics > Portable Audio", required_aspects=["Brand", "Model"])


@pytest.fixture
def prices() -> PriceStats:
    return PriceStats(query="Sony WH-1000XM4", count=12, low=120, median=160, high=200, currency="USD")


@pytest.fixture
def draft() -> ListingDraft:
    return ListingDraft(
        title="Sony WH-1000XM4 Wireless Noise Cancelling Headphones Black",
        description_html="<p>Good condition.</p>",
        condition="USED_GOOD",
        condition_description="Light scuffs on the headband.",
        aspects=[Aspect(name="Brand", values=["Sony"]), Aspect(name="Model", values=["WH-1000XM4"])],
        price=149.99,
        price_rationale="Slightly under the comps' median for a quick sale.",
    )
