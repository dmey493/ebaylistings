"""Thin, dependency-light client for the eBay REST APIs this project needs."""

from .auth import EbayAuth
from .client import EbayClient, EbayError

__all__ = ["EbayAuth", "EbayClient", "EbayError"]
