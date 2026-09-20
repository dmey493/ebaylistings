"""On-disk job store: data/jobs/<job_id>/{job.json, photos/*}. No database needed."""

from __future__ import annotations

import secrets
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings
from .models import Job

PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic"}


class JobStore:
    def __init__(self, settings: Settings):
        self.root: Path = settings.jobs_dir.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _dir(self, job_id: str) -> Path:
        return self.root / job_id

    def new_id(self) -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        return f"{stamp}-{secrets.token_hex(2)}"

    def create(self, photos: list[Path], note: str = "") -> Job:
        job = Job(id=self.new_id(), note=note)
        pdir = self._dir(job.id) / "photos"
        pdir.mkdir(parents=True, exist_ok=True)
        for i, src in enumerate(photos, 1):
            ext = src.suffix.lower() or ".jpg"
            dst = pdir / f"{i:02d}{ext}"
            shutil.copyfile(src, dst)
            job.photos.append(str(dst))
        self.save(job)
        return job

    def save(self, job: Job) -> None:
        d = self._dir(job.id)
        d.mkdir(parents=True, exist_ok=True)
        (d / "job.json").write_text(job.model_dump_json(indent=2))

    def load(self, job_id: str) -> Job:
        p = self._dir(job_id) / "job.json"
        if not p.is_file():
            raise FileNotFoundError(f"No job {job_id}")
        return Job.model_validate_json(p.read_text())

    def list(self, limit: int = 50) -> list[Job]:
        jobs = []
        for d in sorted(self.root.iterdir(), reverse=True):
            if (d / "job.json").is_file():
                jobs.append(self.load(d.name))
            if len(jobs) >= limit:
                break
        return jobs


def photos_in(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in PHOTO_EXTS)
