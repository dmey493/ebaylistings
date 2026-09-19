"""HTTP plumbing shared by the API wrappers: auth headers, JSON, error mapping."""

from __future__ import annotations

from typing import Any

import requests

from ..config import Settings
from .auth import EbayAuth


class EbayError(RuntimeError):
    def __init__(self, status: int, message: str, errors: list[dict] | None = None):
        super().__init__(f"eBay API {status}: {message}")
        self.status = status
        self.errors = errors or []


def _describe(r: requests.Response) -> tuple[str, list[dict]]:
    try:
        body = r.json()
    except ValueError:
        return r.text[:500], []
    errors = body.get("errors") if isinstance(body, dict) else None
    if errors:
        msgs = []
        for e in errors:
            bits = [str(e.get("errorId", "")), e.get("message", ""), e.get("longMessage", "")]
            params = e.get("parameters") or []
            if params:
                bits.append("; ".join(f"{p.get('name')}={p.get('value')}" for p in params))
            msgs.append(" ".join(b for b in bits if b))
        return " | ".join(msgs), errors
    return str(body)[:500], []


class EbayClient:
    def __init__(self, settings: Settings, auth: EbayAuth | None = None, session: requests.Session | None = None):
        self.s = settings
        self.http = session or requests.Session()
        self.auth = auth or EbayAuth(settings, self.http)

    def _headers(self, *, user: bool, extra: dict | None = None) -> dict:
        token = self.auth.user_token() if user else self.auth.app_token()
        h = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "X-EBAY-C-MARKETPLACE-ID": self.s.ebay_marketplace_id,
            "Accept-Language": "en-US",
            "Content-Language": "en-US",
        }
        if extra:
            h.update(extra)
        return h

    def request(
        self,
        method: str,
        path: str,
        *,
        user: bool = True,
        base: str | None = None,
        json: Any = None,
        params: dict | None = None,
        headers: dict | None = None,
        files: dict | None = None,
        ok: tuple[int, ...] = (200, 201, 204),
    ) -> requests.Response:
        url = (base or self.s.api_base) + path
        h = self._headers(user=user, extra=headers)
        if json is not None:
            h.setdefault("Content-Type", "application/json")
        r = self.http.request(method, url, headers=h, json=json, params=params, files=files, timeout=60)
        if r.status_code not in ok:
            msg, errors = _describe(r)
            raise EbayError(r.status_code, msg, errors)
        return r

    def get_json(self, path: str, **kw) -> dict:
        r = self.request("GET", path, **kw)
        return r.json() if r.content else {}
