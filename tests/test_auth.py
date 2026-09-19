import json
import urllib.parse

from ebaylister.ebay.auth import EbayAuth
from tests.conftest import FakeResponse


def test_consent_url_uses_runame_and_scopes(settings, session):
    auth = EbayAuth(settings, session)
    url = auth.consent_url()
    parsed = urllib.parse.urlparse(url)
    q = urllib.parse.parse_qs(parsed.query)
    assert parsed.netloc == "auth.sandbox.ebay.com"
    assert q["redirect_uri"] == ["My-RuName"]
    assert q["response_type"] == ["code"]
    assert "https://api.ebay.com/oauth/api_scope/sell.inventory" in q["scope"][0]


def test_extract_code_from_redirect_url():
    url = "https://example.com/cb?state=x&code=v%5E1.1%23i%5E1%23abc&expires_in=299"
    assert EbayAuth.extract_code(url) == "v^1.1#i^1#abc"
    assert EbayAuth.extract_code("  rawcode ") == "rawcode"


def test_exchange_and_refresh_persist_tokens(settings, session):
    session.on("POST", "/identity/v1/oauth2/token", FakeResponse(200, {
        "access_token": "A1", "expires_in": 7200, "refresh_token": "R1", "refresh_token_expires_in": 100000,
    }))
    auth = EbayAuth(settings, session)
    auth.exchange_code("thecode")
    saved = json.loads(settings.token_path.read_text())
    assert saved["refresh_token"] == "R1"
    call = session.calls[-1]
    assert call["headers"]["Authorization"].startswith("Basic ")
    assert call["data"]["grant_type"] == "authorization_code"
    assert call["data"]["redirect_uri"] == "My-RuName"

    # force expiry -> refresh grant is used
    auth.tokens.access_expires_at = 0
    session.rules.clear()
    session.on("POST", "/identity/v1/oauth2/token", FakeResponse(200, {"access_token": "A2", "expires_in": 7200}))
    assert auth.user_token() == "A2"
    assert session.calls[-1]["data"]["grant_type"] == "refresh_token"


def test_app_token_uses_client_credentials(settings, session):
    session.on("POST", "/identity/v1/oauth2/token", FakeResponse(200, {"access_token": "APP", "expires_in": 7200}))
    auth = EbayAuth(settings, session)
    assert auth.app_token() == "APP"
    assert session.calls[-1]["data"]["grant_type"] == "client_credentials"
