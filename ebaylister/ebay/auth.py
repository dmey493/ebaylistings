"""eBay OAuth 2.0.

Two token kinds are used:

* **User token** (authorization-code grant) - acts as *your* seller account.
  Needed for Media, Inventory and Account APIs. Access tokens last ~2 hours;
  the refresh token lasts ~18 months and is persisted to disk.
* **Application token** (client-credentials grant) - no user context.
  Enough for Taxonomy and Browse (category lookup, price comps).

Docs: https://developer.ebay.com/api-docs/static/oauth-tokens.html
"""

from __future__ import annotations

import base64
import json
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

import requests

from ..config import Settings

# Scopes the seller must consent to. Keep this list stable: changing it
# invalidates existing refresh tokens (eBay ties the refresh token to scopes).
USER_SCOPES = [
    "https://api.ebay.com/oauth/api_scope",
    "https://api.ebay.com/oauth/api_scope/sell.inventory",
    "https://api.ebay.com/oauth/api_scope/sell.account",
    "https://api.ebay.com/oauth/api_scope/sell.fulfillment",
]
APP_SCOPES = ["https://api.ebay.com/oauth/api_scope"]

_SKEW_SECONDS = 120


@dataclass
class TokenSet:
    access_token: str = ""
    access_expires_at: float = 0.0
    refresh_token: str = ""
    refresh_expires_at: float = 0.0
    app_token: str = ""
    app_expires_at: float = 0.0

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: dict) -> "TokenSet":
        return cls(**{k: d.get(k, v) for k, v in cls().__dict__.items()})


class AuthError(RuntimeError):
    pass


class EbayAuth:
    def __init__(self, settings: Settings, session: requests.Session | None = None):
        self.s = settings
        self.http = session or requests.Session()
        self.tokens = self._load()

    # ---------- persistence ----------
    def _load(self) -> TokenSet:
        p: Path = self.s.token_path
        if p.is_file():
            return TokenSet.from_dict(json.loads(p.read_text()))
        return TokenSet()

    def _save(self) -> None:
        p: Path = self.s.token_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.tokens.to_dict(), indent=2))
        try:
            p.chmod(0o600)
        except OSError:
            pass

    # ---------- helpers ----------
    @property
    def _basic_auth(self) -> str:
        raw = f"{self.s.ebay_client_id}:{self.s.ebay_client_secret}".encode()
        return "Basic " + base64.b64encode(raw).decode()

    @property
    def token_url(self) -> str:
        return f"{self.s.api_base}/identity/v1/oauth2/token"

    def _token_request(self, form: dict) -> dict:
        r = self.http.post(
            self.token_url,
            headers={"Authorization": self._basic_auth, "Content-Type": "application/x-www-form-urlencoded"},
            data=form,
            timeout=30,
        )
        if r.status_code >= 400:
            raise AuthError(f"eBay token endpoint returned {r.status_code}: {r.text[:500]}")
        return r.json()

    # ---------- user consent (authorization code grant) ----------
    def consent_url(self, state: str = "ebaylister") -> str:
        """URL to open in a browser; the seller signs in and grants the scopes."""
        q = {
            "client_id": self.s.ebay_client_id,
            "redirect_uri": self.s.ebay_runame,  # eBay expects the RuName here, not a raw URL
            "response_type": "code",
            "scope": " ".join(USER_SCOPES),
            "state": state,
            "prompt": "login",
        }
        return f"{self.s.auth_base}/oauth2/authorize?{urllib.parse.urlencode(q)}"

    @staticmethod
    def extract_code(redirect_url_or_code: str) -> str:
        """Accept either the bare code or the full redirect URL eBay sent the browser to."""
        value = redirect_url_or_code.strip()
        if "code=" in value:
            parsed = urllib.parse.urlparse(value)
            code = urllib.parse.parse_qs(parsed.query).get("code", [""])[0]
            if code:
                return code
        return value

    def exchange_code(self, code: str) -> TokenSet:
        now = time.time()
        data = self._token_request(
            {"grant_type": "authorization_code", "code": self.extract_code(code), "redirect_uri": self.s.ebay_runame}
        )
        self.tokens.access_token = data["access_token"]
        self.tokens.access_expires_at = now + float(data.get("expires_in", 7200))
        self.tokens.refresh_token = data["refresh_token"]
        self.tokens.refresh_expires_at = now + float(data.get("refresh_token_expires_in", 47304000))
        self._save()
        return self.tokens

    def _refresh(self) -> None:
        if not self.tokens.refresh_token:
            raise AuthError("No eBay user token on file. Run `ebaylister auth` first.")
        if self.tokens.refresh_expires_at and time.time() > self.tokens.refresh_expires_at:
            raise AuthError("eBay refresh token expired. Run `ebaylister auth` again.")
        now = time.time()
        data = self._token_request(
            {"grant_type": "refresh_token", "refresh_token": self.tokens.refresh_token, "scope": " ".join(USER_SCOPES)}
        )
        self.tokens.access_token = data["access_token"]
        self.tokens.access_expires_at = now + float(data.get("expires_in", 7200))
        self._save()

    def user_token(self) -> str:
        if not self.tokens.access_token or time.time() > self.tokens.access_expires_at - _SKEW_SECONDS:
            self._refresh()
        return self.tokens.access_token

    @property
    def has_user_token(self) -> bool:
        return bool(self.tokens.refresh_token)

    # ---------- application token (client credentials) ----------
    def app_token(self) -> str:
        if not self.tokens.app_token or time.time() > self.tokens.app_expires_at - _SKEW_SECONDS:
            now = time.time()
            data = self._token_request({"grant_type": "client_credentials", "scope": " ".join(APP_SCOPES)})
            self.tokens.app_token = data["access_token"]
            self.tokens.app_expires_at = now + float(data.get("expires_in", 7200))
            self._save()
        return self.tokens.app_token
