"""Phone-friendly web UI: open it on your phone, tap 'Take photos', add a note, submit.
Drafting runs in the background; the job page refreshes until the draft is ready,
then shows a Publish button (or publishes automatically when EBAYLISTER_AUTO_PUBLISH=true)."""

from __future__ import annotations

import html
import tempfile
import threading
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from .config import Settings
from .models import Job
from .storage import JobStore

_STYLE = """
<style>
 body{font-family:-apple-system,system-ui,sans-serif;max-width:640px;margin:0 auto;padding:16px;background:#f6f6f6;color:#111}
 .card{background:#fff;border-radius:12px;padding:16px;margin:12px 0;box-shadow:0 1px 3px rgba(0,0,0,.08)}
 button,.btn{display:block;width:100%;padding:14px;border:0;border-radius:10px;font-size:17px;background:#0654ba;color:#fff;text-align:center;text-decoration:none;margin-top:12px}
 button.secondary,.btn.secondary{background:#e5e5e5;color:#111}
 input[type=file],textarea,input[type=text],input[type=number]{width:100%;box-sizing:border-box;padding:10px;font-size:16px;border:1px solid #ccc;border-radius:8px;margin:6px 0}
 .thumbs img{width:31%;margin:1%;border-radius:8px;object-fit:cover;aspect-ratio:1}
 .status{font-weight:600}.fail{color:#b00}.ok{color:#0a7}
 small{color:#666}
</style>"""


def _page(title: str, body: str, refresh: int | None = None) -> HTMLResponse:
    meta = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    return HTMLResponse(
        f"<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        f"{meta}<title>{html.escape(title)}</title>{_STYLE}</head><body>{body}</body></html>"
    )


def _job_html(job: Job, s: Settings) -> str:
    e = html.escape
    parts = [f"<div class=card><div class=status>Status: {e(job.status)}</div>"]
    if job.error:
        parts.append(f"<p class=fail>{e(job.error)}</p>")
    parts.append("</div>")
    if job.identified:
        i = job.identified
        parts.append(
            f"<div class=card><b>{e(i.product_name)}</b><br><small>{e(i.condition)} - confidence {i.confidence:.0%}</small>"
            f"<p>{e(i.condition_notes)}</p>"
            + (f"<p><small>Unsure about: {e('; '.join(i.uncertainties))}</small></p>" if i.uncertainties else "")
            + "</div>"
        )
    if job.prices and job.prices.count:
        p = job.prices
        parts.append(
            f"<div class=card><small>{p.count} comparable active listings: {e(p.currency)} "
            f"{p.low:.2f} / <b>{p.median:.2f}</b> / {p.high:.2f} (low / median / high)</small></div>"
        )
    if job.draft:
        d = job.draft
        specifics = "".join(f"<li>{e(a.name)}: {e(', '.join(a.values))}</li>" for a in d.aspects)
        parts.append(
            f"<div class=card><h3>{e(d.title)}</h3>"
            f"<p><b>{e(job.prices.currency if job.prices else 'USD')} {d.price:.2f}</b> <small>{e(d.price_rationale)}</small></p>"
            f"<p><small>{e(job.category.category_name if job.category else '')}</small></p>"
            f"<ul>{specifics}</ul><div>{d.description_html}</div></div>"
        )
    if job.status == "awaiting_review":
        parts.append(
            f"<form class=card method=post action='/jobs/{e(job.id)}/publish'>"
            f"<label>Title<input type=text name=title maxlength=80 value='{e(job.draft.title)}'></label>"
            f"<label>Price<input type=number step=0.01 name=price value='{job.draft.price:.2f}'></label>"
            f"<button type=submit>Publish to eBay ({e(s.ebay_env)})</button></form>"
        )
    if job.result:
        parts.append(f"<div class=card ok><a class=btn href='{e(job.result.listing_url)}'>View listing</a></div>")
    if job.photos:
        parts.append("<div class='card thumbs'>" + "".join(f"<img src='/jobs/{e(job.id)}/photo/{i}'>" for i in range(len(job.photos))) + "</div>")
    parts.append("<a class='btn secondary' href='/'>Sell another</a>")
    return "".join(parts)


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(title="ebaylister")
    store = JobStore(settings)
    lock = threading.Lock()

    def pipeline():
        # Built lazily so importing the web app never needs API keys.
        from .pipeline import Pipeline

        if not hasattr(app.state, "pipeline"):
            with lock:
                if not hasattr(app.state, "pipeline"):
                    app.state.pipeline = Pipeline(settings, store=store)
        return app.state.pipeline

    def run_draft(job_id: str) -> None:
        pipe = pipeline()
        job = pipe.draft(store.load(job_id))
        if job.status == "awaiting_review" and settings.auto_publish:
            pipe.publish(job)

    @app.get("/", response_class=HTMLResponse)
    def index():
        recent = "".join(
            f"<li><a href='/jobs/{html.escape(j.id)}'>{html.escape(j.draft.title if j.draft else (j.identified.product_name if j.identified else j.id))}</a> "
            f"<small>{html.escape(j.status)}</small></li>"
            for j in store.list(10)
        )
        body = (
            "<h2>Sell something</h2>"
            "<form class=card method=post action='/sell' enctype=multipart/form-data>"
            "<label>Photos<input type=file name=photos accept='image/*' capture=environment multiple required></label>"
            "<label>Anything I should know? <small>(optional)</small><textarea name=note rows=3 placeholder='works fine, no charger, size M...'></textarea></label>"
            "<button type=submit>Create listing</button></form>"
            + (f"<div class=card><b>Recent</b><ul>{recent}</ul></div>" if recent else "")
        )
        return _page("Sell", body)

    @app.post("/sell")
    async def sell(background: BackgroundTasks, photos: list[UploadFile] = File(...), note: str = Form("")):
        tmp = Path(tempfile.mkdtemp(prefix="ebaylister-"))
        paths = []
        for i, up in enumerate(photos, 1):
            suffix = Path(up.filename or "").suffix.lower() or ".jpg"
            p = tmp / f"{i:02d}{suffix}"
            p.write_bytes(await up.read())
            paths.append(p)
        if not paths:
            raise HTTPException(400, "no photos")
        job = store.create(paths, note)
        background.add_task(run_draft, job.id)
        return RedirectResponse(f"/jobs/{job.id}", status_code=303)

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_page(job_id: str):
        try:
            job = store.load(job_id)
        except FileNotFoundError:
            raise HTTPException(404)
        busy = job.status in {"queued", "identifying", "drafting", "publishing"}
        return _page(job.draft.title if job.draft else "Working...", _job_html(job, settings), refresh=4 if busy else None)

    @app.post("/jobs/{job_id}/publish")
    def publish(job_id: str, background: BackgroundTasks, title: str = Form(""), price: float | None = Form(None)):
        job = store.load(job_id)
        if job.status != "awaiting_review" or job.draft is None:
            raise HTTPException(409, "job is not awaiting review")
        if title.strip():
            job.draft.title = title.strip()[:80]
        if price:
            job.draft.price = price
        job.status = "publishing"
        store.save(job)
        background.add_task(lambda: pipeline().publish(job))
        return RedirectResponse(f"/jobs/{job.id}", status_code=303)

    @app.get("/jobs/{job_id}/photo/{idx}")
    def photo(job_id: str, idx: int):
        from fastapi.responses import FileResponse

        job = store.load(job_id)
        if idx < 0 or idx >= len(job.photos):
            raise HTTPException(404)
        return FileResponse(job.photos[idx])

    @app.get("/api/jobs/{job_id}")
    def job_json(job_id: str):
        return store.load(job_id).model_dump(mode="json")

    return app
