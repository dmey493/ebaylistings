import pytest

from ebaylister.ebay import browse, inventory, media, taxonomy
from ebaylister.ebay.client import EbayError
from tests.conftest import FakeResponse


def test_upload_image_returns_hosted_url(ebay, session, photo):
    session.on("POST", "/commerce/media/v1_beta/image/create_from_file", FakeResponse(201, headers={"Location": "https://apim.sandbox.ebay.com/commerce/media/v1_beta/image/IMG123"}))
    session.on("GET", "/commerce/media/v1_beta/image/IMG123", FakeResponse(200, {"imageUrl": "https://i.ebayimg.com/x.jpg"}))
    assert media.upload_image(ebay, photo) == "https://i.ebayimg.com/x.jpg"
    post = session.calls[0]
    assert post["url"].startswith("https://apim.sandbox.ebay.com")
    assert "image" in post["files"]
    assert post["headers"]["Authorization"] == "Bearer user-tok"


def test_upload_rejects_oversized(ebay, tmp_path):
    big = tmp_path / "big.jpg"
    big.write_bytes(b"0" * (media.MAX_BYTES + 1))
    with pytest.raises(ValueError):
        media.upload_image(ebay, big)


def test_pick_category_collects_required_aspects(ebay, session):
    taxonomy._tree_id.cache_clear()
    session.on("GET", "/get_default_category_tree_id", FakeResponse(200, {"categoryTreeId": "0"}))
    session.on("GET", "/get_category_suggestions", FakeResponse(200, {"categorySuggestions": [
        {"category": {"categoryId": "112529", "categoryName": "Headphones"},
         "categoryTreeNodeAncestors": [{"categoryName": "Portable Audio"}, {"categoryName": "Consumer Electronics"}]},
    ]}))
    session.on("GET", "/get_item_aspects_for_category", FakeResponse(200, {"aspects": [
        {"localizedAspectName": "Brand", "aspectConstraint": {"aspectRequired": True}},
        {"localizedAspectName": "Color", "aspectConstraint": {"aspectRequired": False, "aspectUsage": "RECOMMENDED"}},
        {"localizedAspectName": "Features", "aspectConstraint": {"aspectRequired": False, "aspectUsage": "OPTIONAL"}},
    ]}))
    pick = taxonomy.pick_category(ebay, "wireless headphones")
    assert pick.category_id == "112529"
    assert pick.path == "Consumer Electronics > Portable Audio"
    assert pick.required_aspects == ["Brand"]
    assert pick.recommended_aspects == ["Color"]
    # taxonomy uses the application token, not the user token
    assert session.calls[0]["headers"]["Authorization"] == "Bearer app-tok"


def test_price_comps_trims_outliers_and_survives_errors(ebay, session):
    items = [{"title": f"t{i}", "price": {"value": str(v), "currency": "USD"}} for i, v in enumerate([1, 100, 110, 120, 130, 140, 150, 9999])]
    session.on("GET", "/buy/browse/v1/item_summary/search", FakeResponse(200, {"itemSummaries": items}))
    stats = browse.price_comps(ebay, "thing", "123")
    assert stats.count == 8
    assert stats.low == 100 and stats.high == 150
    assert stats.median == 125
    assert session.calls[0]["params"]["category_ids"] == "123"

    session.rules.clear()
    session.on("GET", "/buy/browse/v1/item_summary/search", FakeResponse(500, text="boom"))
    assert browse.price_comps(ebay, "thing").count == 0


def test_publish_listing_three_steps(ebay, session, draft):
    session.on("PUT", "/sell/inventory/v1/inventory_item/", FakeResponse(204))
    session.on("GET", "/sell/inventory/v1/offer", FakeResponse(404, {"errors": [{"errorId": 25710, "message": "not found"}]}))
    session.on("POST", "/sell/inventory/v1/offer/OFF1/publish", FakeResponse(200, {"listingId": "3350012345"}))
    session.on("POST", "/sell/inventory/v1/offer", FakeResponse(201, {"offerId": "OFF1"}))

    result = inventory.publish_listing(ebay, "SONY-JOB1", draft, "112529", ["https://i.ebayimg.com/x.jpg"])
    assert result.listing_id == "3350012345"
    assert result.listing_url == "https://sandbox.ebay.com/itm/3350012345"

    put = session.calls[0]
    assert put["method"] == "PUT" and put["url"].endswith("/inventory_item/SONY-JOB1")
    assert put["headers"]["Content-Language"] == "en-US"
    assert put["json"]["product"]["aspects"] == {"Brand": ["Sony"], "Model": ["WH-1000XM4"]}
    assert put["json"]["condition"] == "USED_GOOD"
    assert put["json"]["availability"]["shipToLocationAvailability"]["quantity"] == 1

    offer = [c for c in session.calls if c["method"] == "POST" and c["url"].endswith("/offer")][0]
    body = offer["json"]
    assert body["pricingSummary"]["price"] == {"value": "149.99", "currency": "USD"}
    assert body["listingPolicies"] == {"fulfillmentPolicyId": "F1", "paymentPolicyId": "P1", "returnPolicyId": "R1"}
    assert body["merchantLocationKey"] == "home"
    assert body["categoryId"] == "112529"


def test_publish_reuses_existing_offer(ebay, session, draft):
    session.on("PUT", "/sell/inventory/v1/inventory_item/", FakeResponse(204))
    session.on("GET", "/sell/inventory/v1/offer", FakeResponse(200, {"offers": [{"offerId": "OLD"}]}))
    session.on("PUT", "/sell/inventory/v1/offer/OLD", FakeResponse(204))
    session.on("POST", "/sell/inventory/v1/offer/OLD/publish", FakeResponse(200, {"listingId": "1"}))
    result = inventory.publish_listing(ebay, "SKU", draft, "1", [])
    assert result.offer_id == "OLD"
    assert not any(c["method"] == "POST" and c["url"].endswith("/offer") for c in session.calls)


def test_error_messages_are_readable(ebay, session):
    session.on("POST", "/offer/X/publish", FakeResponse(400, {"errors": [{"errorId": 25002, "message": "A user error has occurred", "longMessage": "Missing Brand", "parameters": [{"name": "aspect", "value": "Brand"}]}]}))
    with pytest.raises(EbayError) as ei:
        ebay.request("POST", "/sell/inventory/v1/offer/X/publish")
    assert "Missing Brand" in str(ei.value) and "aspect=Brand" in str(ei.value)
