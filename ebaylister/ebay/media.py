"""Media API: upload photos so eBay hosts them (required for Inventory listings).

POST {apim}/commerce/media/v1_beta/image/create_from_file  (multipart, field "image")
  -> 201 with a Location header ending in the image id
GET  {apim}/commerce/media/v1_beta/image/{image_id}
  -> {"imageUrl": "https://i.ebayimg.com/..."}
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from .client import EbayClient

MAX_BYTES = 12 * 1024 * 1024  # eBay's documented per-image ceiling


def upload_image(client: EbayClient, path: Path) -> str:
    """Upload one image and return the eBay-hosted URL."""
    size = path.stat().st_size
    if size > MAX_BYTES:
        raise ValueError(f"{path.name} is {size / 1e6:.1f} MB; eBay allows at most 12 MB per image")
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    with path.open("rb") as fh:
        r = client.request(
            "POST",
            "/commerce/media/v1_beta/image/create_from_file",
            base=client.s.apim_base,
            files={"image": (path.name, fh, mime)},
            ok=(201,),
        )
    location = r.headers.get("Location", "")
    image_id = location.rstrip("/").split("/")[-1]
    if not image_id:
        raise RuntimeError("Media API did not return an image id in the Location header")
    meta = client.get_json(f"/commerce/media/v1_beta/image/{image_id}", base=client.s.apim_base)
    url = meta.get("imageUrl")
    if not url:
        raise RuntimeError(f"Media API returned no imageUrl for image {image_id}: {meta}")
    return url


def upload_images(client: EbayClient, paths: list[Path]) -> list[str]:
    return [upload_image(client, p) for p in paths]
