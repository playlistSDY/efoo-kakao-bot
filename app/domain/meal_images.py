from __future__ import annotations

from urllib.parse import urljoin, urlparse


PLACEHOLDER_IMAGE_MARKERS = (
    "no-img",
    "no_image",
    "no-image",
    "noimage",
    "default-img",
    "default_image",
    "placeholder",
)


def normalize_meal_image_url(value: str | None, base_url: str) -> str:
    url = (value or "").strip()
    if not url:
        return ""
    absolute_url = urljoin(f"{base_url.rstrip('/')}/", url)
    return "" if is_placeholder_meal_image_url(absolute_url) else absolute_url


def is_placeholder_meal_image_url(value: str | None) -> bool:
    if not value:
        return True
    path = urlparse(value).path.lower()
    filename = path.rsplit("/", 1)[-1]
    return any(marker in filename for marker in PLACEHOLDER_IMAGE_MARKERS)
