from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import logging
from pathlib import Path
from threading import Lock
from urllib.parse import urlparse

import requests

from app.config import settings
from app.domain.meal_images import is_placeholder_meal_image_url


logger = logging.getLogger(__name__)


class MealImageCache:
    def __init__(
        self,
        cache_dir: str | Path,
        public_base_url: str,
        max_bytes: int,
        max_workers: int = 2,
    ):
        self.cache_dir = Path(cache_dir)
        self.public_base_url = public_base_url.rstrip("/")
        self.max_bytes = max_bytes
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="meal-image")
        self._scheduled: set[str] = set()
        self._lock = Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.public_base_url)

    def public_url(self, source_url: str | None) -> str | None:
        if is_placeholder_meal_image_url(source_url):
            return None
        if not self.public_base_url or not self._allowed_source(source_url):
            return source_url
        key = self.key_for(source_url)
        self.schedule(source_url)
        return f"{self.public_base_url}/media/meals/{key}"

    def schedule(self, source_url: str) -> bool:
        if not self.enabled:
            return False
        if is_placeholder_meal_image_url(source_url):
            return False
        if not self._allowed_source(source_url):
            logger.warning("허용되지 않은 학식 이미지 URL 무시: %s", source_url)
            return False
        key = self.key_for(source_url)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        if self.image_path(key).exists():
            return False
        with self._lock:
            if key in self._scheduled:
                return False
            self._scheduled.add(key)
        try:
            self._write_metadata(key, {"source_url": source_url, "content_type": None})
            self._executor.submit(self._download, key, source_url)
        except Exception:
            with self._lock:
                self._scheduled.discard(key)
            raise
        return True

    def resolve(self, key: str) -> tuple[Path | None, str | None, str | None]:
        if not _valid_key(key):
            return None, None, None
        metadata = self._read_metadata(key)
        source_url = metadata.get("source_url") if metadata else None
        if source_url and not self._allowed_source(source_url):
            source_url = None
        path = self.image_path(key)
        if path.exists():
            return path, str(metadata.get("content_type") or "image/jpeg"), source_url
        if source_url:
            self.schedule(source_url)
        return None, None, source_url

    def key_for(self, source_url: str) -> str:
        return hashlib.sha256(source_url.encode()).hexdigest()

    def image_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.bin"

    def _metadata_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def _download(self, key: str, source_url: str) -> None:
        temporary = self.cache_dir / f"{key}.tmp"
        try:
            response = requests.get(
                source_url,
                stream=True,
                timeout=(settings.MEAL_HTTP_CONNECT_TIMEOUT_SECONDS, settings.MEAL_HTTP_READ_TIMEOUT_SECONDS),
                headers={"User-Agent": "EfooMealImageCache/1.0"},
            )
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if not content_type.startswith("image/"):
                raise ValueError(f"이미지가 아닌 응답: {content_type or 'unknown'}")
            written = 0
            with temporary.open("wb") as output:
                for chunk in response.iter_content(64 * 1024):
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > self.max_bytes:
                        raise ValueError("이미지 최대 크기 초과")
                    output.write(chunk)
            if written == 0:
                raise ValueError("빈 이미지 응답")
            temporary.replace(self.image_path(key))
            self._write_metadata(key, {"source_url": source_url, "content_type": content_type})
            logger.info("학식 이미지 캐시 완료: key=%s bytes=%s", key, written)
        except Exception:
            logger.exception("학식 이미지 캐시 실패: source=%s", source_url)
            temporary.unlink(missing_ok=True)
        finally:
            with self._lock:
                self._scheduled.discard(key)

    def _write_metadata(self, key: str, metadata: dict) -> None:
        path = self._metadata_path(key)
        existing = self._read_metadata(key)
        if existing and existing.get("content_type") and not metadata.get("content_type"):
            metadata["content_type"] = existing["content_type"]
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)

    def _read_metadata(self, key: str) -> dict:
        try:
            return json.loads(self._metadata_path(key).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _allowed_source(self, source_url: str) -> bool:
        if is_placeholder_meal_image_url(source_url):
            return False
        parsed = urlparse(source_url)
        hostname = (parsed.hostname or "").lower()
        return parsed.scheme in {"http", "https"} and (hostname == "hanyang.ac.kr" or hostname.endswith(".hanyang.ac.kr"))


def _valid_key(key: str) -> bool:
    return len(key) == 64 and all(character in "0123456789abcdef" for character in key)


meal_image_cache = MealImageCache(
    cache_dir=settings.MEAL_IMAGE_CACHE_DIR,
    public_base_url=settings.PUBLIC_BASE_URL,
    max_bytes=settings.MEAL_IMAGE_MAX_BYTES,
)


def public_meal_image_url(source_url: str | None) -> str | None:
    return meal_image_cache.public_url(source_url)
