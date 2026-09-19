from pathlib import Path

from ebaylister.config import load_settings


def test_dotenv_and_derived_urls(tmp_path, monkeypatch):
    for k in ("EBAY_ENV", "EBAY_CLIENT_ID", "EBAYLISTER_AUTO_PUBLISH"):
        monkeypatch.delenv(k, raising=False)
    env = tmp_path / ".env"
    env.write_text("EBAY_ENV=production  # live\nEBAY_CLIENT_ID='abc'\nEBAYLISTER_AUTO_PUBLISH=true\n# comment\n")
    s = load_settings(env)
    assert s.ebay_env == "production"
    assert s.ebay_client_id == "abc"
    assert s.auto_publish is True
    assert not s.is_sandbox
    assert s.api_base == "https://api.ebay.com"
    assert s.apim_base == "https://apim.ebay.com"
    assert s.auth_base == "https://auth.ebay.com"


def test_sandbox_defaults(tmp_path, monkeypatch):
    for k in ("EBAY_ENV", "EBAY_CLIENT_ID", "EBAYLISTER_AUTO_PUBLISH"):
        monkeypatch.delenv(k, raising=False)
    s = load_settings(tmp_path / "missing.env")
    assert s.is_sandbox
    assert s.api_base == "https://api.sandbox.ebay.com"
    assert "EBAY_CLIENT_ID" in s.missing_ebay_credentials()
    assert s.token_path == Path(s.data_dir) / "ebay_tokens.sandbox.json"
