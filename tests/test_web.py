from fastapi.testclient import TestClient

from ebaylister.storage import JobStore
from ebaylister.web import create_app


def test_upload_creates_job_and_renders_pages(settings, monkeypatch):
    app = create_app(settings)
    calls = []

    class FakePipeline:
        def draft(self, job):
            calls.append(("draft", job.id))
            job.status = "awaiting_review"
            return job

        def publish(self, job):
            calls.append(("publish", job.id))
            job.status = "published"
            return job

    app.state.pipeline = FakePipeline()
    client = TestClient(app)

    r = client.get("/")
    assert r.status_code == 200 and "capture=environment" in r.text

    r = client.post("/sell", files=[("photos", ("a.jpg", b"\xff\xd8x", "image/jpeg"))], data={"note": "hi"}, follow_redirects=False)
    assert r.status_code == 303
    job_id = r.headers["location"].rsplit("/", 1)[-1]
    assert calls == [("draft", job_id)]

    job = JobStore(settings).load(job_id)
    assert job.note == "hi" and len(job.photos) == 1
    assert client.get(f"/jobs/{job_id}/photo/0").content == b"\xff\xd8x"
    assert client.get(f"/api/jobs/{job_id}").json()["note"] == "hi"


def test_publish_endpoint_applies_overrides(settings, draft, category):
    app = create_app(settings)
    seen = {}

    class FakePipeline:
        def publish(self, job):
            seen["title"], seen["price"] = job.draft.title, job.draft.price
            job.status = "published"
            return job

    app.state.pipeline = FakePipeline()
    store = JobStore(settings)
    job = store.create([], "")
    job.draft, job.category, job.status = draft, category, "awaiting_review"
    store.save(job)

    client = TestClient(app)
    r = client.post(f"/jobs/{job.id}/publish", data={"title": "New Title", "price": "99.5"}, follow_redirects=False)
    assert r.status_code == 303
    assert seen == {"title": "New Title", "price": 99.5}

    # cannot publish twice
    assert client.post(f"/jobs/{job.id}/publish", data={}).status_code == 409
