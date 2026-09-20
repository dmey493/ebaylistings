"""Settings loaded from environment variables (and an optional .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader: KEY=VALUE lines, '#' comments, no interpolation.

    Values already present in the environment win over the file.
    """
    if not path.is_file():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.split("  #", 1)[0].split("\t#", 1)[0].strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    anthropic_model: str = "claude-opus-5"

    ebay_env: str = "sandbox"
    ebay_client_id: str = ""
    ebay_client_secret: str = ""
    ebay_runame: str = ""
    ebay_marketplace_id: str = "EBAY_US"

    merchant_location_key: str = "home"
    fulfillment_policy_id: str = ""
    payment_policy_id: str = ""
    return_policy_id: str = ""

    data_dir: Path = field(default_factory=lambda: Path("./data"))
    auto_publish: bool = False

    # Which "brain" identifies the item and writes the listing:
    #   claude-code - runs `claude -p` (headless Claude Code) with the sell-job skill; covered by a Claude plan
    #   api         - calls the Messages API directly with ANTHROPIC_API_KEY (pay-as-you-go)
    brain: str = "claude-code"
    claude_cmd: str = "claude"
    repo_dir: Path = field(default_factory=lambda: Path(__file__).resolve().parent.parent)

    # ---- derived ----
    @property
    def is_sandbox(self) -> bool:
        return self.ebay_env.lower() != "production"

    @property
    def api_base(self) -> str:
        return "https://api.sandbox.ebay.com" if self.is_sandbox else "https://api.ebay.com"

    @property
    def apim_base(self) -> str:
        # The Media API (image upload) lives on a separate host.
        return "https://apim.sandbox.ebay.com" if self.is_sandbox else "https://apim.ebay.com"

    @property
    def auth_base(self) -> str:
        return "https://auth.sandbox.ebay.com" if self.is_sandbox else "https://auth.ebay.com"

    @property
    def listing_url_base(self) -> str:
        return "https://sandbox.ebay.com/itm/" if self.is_sandbox else "https://www.ebay.com/itm/"

    @property
    def token_path(self) -> Path:
        return self.data_dir / f"ebay_tokens.{self.ebay_env}.json"

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def inbox_dir(self) -> Path:
        return self.data_dir / "inbox"

    def missing_ebay_credentials(self) -> list[str]:
        missing = []
        if not self.ebay_client_id:
            missing.append("EBAY_CLIENT_ID")
        if not self.ebay_client_secret:
            missing.append("EBAY_CLIENT_SECRET")
        if not self.ebay_runame:
            missing.append("EBAY_RUNAME")
        return missing

    def missing_seller_defaults(self) -> list[str]:
        missing = []
        if not self.fulfillment_policy_id:
            missing.append("EBAY_FULFILLMENT_POLICY_ID")
        if not self.payment_policy_id:
            missing.append("EBAY_PAYMENT_POLICY_ID")
        if not self.return_policy_id:
            missing.append("EBAY_RETURN_POLICY_ID")
        return missing


def load_settings(dotenv: Path | None = None) -> Settings:
    _load_dotenv(dotenv or Path(".env"))
    env = os.environ
    return Settings(
        anthropic_model=env.get("EBAYLISTER_MODEL", "claude-opus-5"),
        ebay_env=env.get("EBAY_ENV", "sandbox"),
        ebay_client_id=env.get("EBAY_CLIENT_ID", ""),
        ebay_client_secret=env.get("EBAY_CLIENT_SECRET", ""),
        ebay_runame=env.get("EBAY_RUNAME", ""),
        ebay_marketplace_id=env.get("EBAY_MARKETPLACE_ID", "EBAY_US"),
        merchant_location_key=env.get("EBAY_MERCHANT_LOCATION_KEY", "home"),
        fulfillment_policy_id=env.get("EBAY_FULFILLMENT_POLICY_ID", ""),
        payment_policy_id=env.get("EBAY_PAYMENT_POLICY_ID", ""),
        return_policy_id=env.get("EBAY_RETURN_POLICY_ID", ""),
        data_dir=Path(env.get("EBAYLISTER_DATA_DIR", "./data")).resolve(),
        auto_publish=_bool(env.get("EBAYLISTER_AUTO_PUBLISH"), False),
        brain=env.get("EBAYLISTER_BRAIN", "claude-code").strip().lower(),
        claude_cmd=env.get("EBAYLISTER_CLAUDE_CMD", "claude"),
        repo_dir=Path(env.get("EBAYLISTER_REPO_DIR", str(Path(__file__).resolve().parent.parent))),
    )
