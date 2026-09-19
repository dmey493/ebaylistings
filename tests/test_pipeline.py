from pathlib import Path

from ebaylister import pipeline as pl
from ebaylister.models import PublishResult
from ebaylister.pipeline import Pipeline
from ebaylister.storage import JobStore


class FakeWriter:
    def __init__(self, identified, draft):
        self._i, self._d = identified, draft
        self.notes = []

    def identify(self, photos, note=""):
        assert all(Path(p).is_file() for p in photos)
        self.notes.append(note)
        return self._i

    def draft(self, item, category, prices, note=""):
        return self._d


def _patch_ebay(monkeypatch, category, prices, publish_result=None, fail_publish=False):
    monkeypatch.setattr(pl.taxonomy, "pick_category", lambda client, q: category)
    monkeypatch.setattr(pl.browse, "price_comps", lambda client, q, cat=None: prices)
    monkeypatch.setattr(pl.media, "upload_images", lambda client, paths: [f"https://i.ebayimg.com/{p.name}" for p in paths])

    def fake_publish(client, sku, draft, category_id, image_urls, currency="USD"):
        if fail_publish:
            raise RuntimeError("eBay said no")
        return PublishResult(sku=sku, offer_id="O1", listing_id="L1", listing_url="https://sandbox.ebay.com/itm/L1", image_urls=image_urls)

    monkeypatch.setattr(pl.inventory, "publish_listing", fake_publish)


def test_run_stops_at_review_by_default(settings, photo, identified, category, prices, draft, monkeypatch):
    _patch_ebay(monkeypatch, category, prices)
    writer = FakeWriter(identified, draft)
    pipe = Pipeline(settings, writer=writer, ebay=object())
    job = pipe.run([photo], note="no box")
    assert job.status == "awaiting_review"
    assert writer.notes == ["no box"]
    assert job.draft.title.startswith("Sony")
    assert job.result is None
    # persisted
    assert JobStore(settings).load(job.id).status == "awaiting_review"
    assert Path(job.photos[0]).is_file()


def test_run_publishes_when_asked(settings, photo, identified, category, prices, draft, monkeypatch):
    _patch_ebay(monkeypatch, category, prices)
    pipe = Pipeline(settings, writer=FakeWriter(identified, draft), ebay=object())
    job = pipe.run([photo], publish=True)
    assert job.status == "published"
    assert job.result.listing_url.endswith("/itm/L1")
    assert job.result.sku.startswith("SONY-")


def test_auto_publish_setting(settings, photo, identified, category, prices, draft, monkeypatch):
    _patch_ebay(monkeypatch, category, prices)
    settings.auto_publish = True
    job = Pipeline(settings, writer=FakeWriter(identified, draft), ebay=object()).run([photo])
    assert job.status == "published"


def test_failures_are_recorded_not_raised(settings, photo, identified, category, prices, draft, monkeypatch):
    _patch_ebay(monkeypatch, category, prices, fail_publish=True)
    pipe = Pipeline(settings, writer=FakeWriter(identified, draft), ebay=object())
    job = pipe.run([photo], publish=True)
    assert job.status == "failed"
    assert "eBay said no" in job.error
    # a failed publish keeps the draft so it can be retried after fixing the cause
    assert job.draft is not None


def test_publish_requires_seller_defaults(settings, photo, identified, category, prices, draft, monkeypatch):
    _patch_ebay(monkeypatch, category, prices)
    settings.return_policy_id = ""
    pipe = Pipeline(settings, writer=FakeWriter(identified, draft), ebay=object())
    job = pipe.run([photo])
    import pytest

    with pytest.raises(RuntimeError, match="EBAY_RETURN_POLICY_ID"):
        pipe.publish(job)
