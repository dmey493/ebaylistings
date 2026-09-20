import os
import stat
from pathlib import Path

import pytest

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


@pytest.fixture(autouse=True)
def _api_brain(settings):
    settings.brain = "api"


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

    with pytest.raises(RuntimeError, match="EBAY_RETURN_POLICY_ID"):
        pipe.publish(job)


def test_steps_are_reusable_externally(settings, photo, identified, category, prices, draft, monkeypatch):
    """The sell-job skill drives these two methods through `ebaylister job identified/draft`."""
    _patch_ebay(monkeypatch, category, prices)
    pipe = Pipeline(settings, ebay=object())
    job = pipe.store.create([photo], "note")
    job = pipe.set_identified(job, identified)
    assert job.status == "drafting" and job.category.category_id == "112529" and job.prices.median == 160
    job = pipe.set_draft(job, draft)
    assert job.status == "awaiting_review"
    assert JobStore(settings).load(job.id).draft.title == draft.title


def _fake_claude(tmp_path, body: str) -> Path:
    exe = tmp_path / "fake-claude"
    exe.write_text("#!/bin/sh\n" + body)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return exe


def test_claude_code_brain_runs_headless_claude(settings, photo, tmp_path, monkeypatch):
    settings.brain = "claude-code"
    settings.repo_dir = tmp_path
    # the fake `claude` records its args and does what the skill would do: mark the job awaiting_review
    log = tmp_path / "args.txt"
    settings.claude_cmd = str(_fake_claude(tmp_path, f"""
echo "$@" > {log}
jobid=$(echo "$2" | sed 's#/sell-job ##')
f="{settings.jobs_dir}/$jobid/job.json"
sed -i 's/"status": "identifying"/"status": "awaiting_review"/' "$f"
"""))
    pipe = Pipeline(settings, ebay=object())
    job = pipe.run([photo], note="hi")
    assert job.status == "awaiting_review", job.error
    args = log.read_text().split()
    assert args[:2] == ["-p", "/sell-job"] and args[2] == job.id
    assert "--allowedTools" in args and "Bash(ebaylister" in log.read_text()
    assert (settings.jobs_dir / job.id / "claude-code.log").is_file()


def test_claude_code_brain_reports_failed_run(settings, photo, tmp_path):
    settings.brain = "claude-code"
    settings.repo_dir = tmp_path
    settings.claude_cmd = str(_fake_claude(tmp_path, "echo 'skill blew up' >&2; exit 1"))
    job = Pipeline(settings, ebay=object()).run([photo])
    assert job.status == "failed"
    assert "skill blew up" in job.error and "exit 1" in job.error


def test_claude_code_brain_missing_binary(settings, photo):
    settings.brain = "claude-code"
    settings.claude_cmd = "definitely-not-a-real-binary-xyz"
    job = Pipeline(settings, ebay=object()).run([photo])
    assert job.status == "failed" and "EBAYLISTER_BRAIN=api" in job.error
