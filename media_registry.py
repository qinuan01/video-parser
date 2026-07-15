from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import PurePath
from urllib.parse import urlparse

from media_resolver import RequestContext


ALLOWED_MEDIA_HOSTS = ("aweme.snssdk.com",)
ALLOWED_MEDIA_HOST_SUFFIXES = (
    ".cdninstagram.com",
    ".fbcdn.net",
    ".instagram.com",
    ".tiktok.com",
    ".tiktokcdn.com",
    ".tiktokcdn-us.com",
    ".tiktokcdn-eu.com",
    ".tiktokcdn-in.com",
    ".tiktokv.com",
    ".tiktokv.us",
    ".tiktokv.eu",
    ".byteoversea.com",
    ".byteoversea.net",
    ".ibyteimg.com",
    ".muscdn.com",
    ".douyin.com",
    ".douyinpic.com",
    ".douyinvod.com",
    ".zjcdn.com",
    ".bilivideo.com",
    ".hdslb.com",
    ".akamaized.net",
)


def is_allowed_media_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host:
        return False
    return host in ALLOWED_MEDIA_HOSTS or any(
        host.endswith(suffix) for suffix in ALLOWED_MEDIA_HOST_SUFFIXES
    )


def safe_filename(value: str, fallback: str) -> str:
    name = PurePath(value).name.strip() or fallback
    cleaned = "".join(
        character
        for character in name
        if character.isalnum() or character in {"-", "_", "."}
    )
    return cleaned[:100] or fallback


@dataclass(frozen=True)
class MediaSource:
    token: str
    url: str
    kind: str
    platform: str
    filename: str
    context: RequestContext
    expires_at: float


class MediaRegistry:
    def __init__(self, ttl_seconds: int = 15 * 60) -> None:
        self.ttl_seconds = ttl_seconds
        self._entries: dict[str, MediaSource] = {}
        self._lock = threading.RLock()

    def register(
        self,
        *,
        url: str,
        kind: str,
        platform: str,
        filename: str,
        context: RequestContext,
    ) -> MediaSource:
        if not is_allowed_media_url(url):
            raise ValueError(f"上游媒体地址不在允许范围内: {url}")
        token = secrets.token_urlsafe(24)
        source = MediaSource(
            token=token,
            url=url,
            kind=kind,
            platform=platform,
            filename=safe_filename(filename, "media.bin"),
            context=context,
            expires_at=time.time() + self.ttl_seconds,
        )
        with self._lock:
            self._cleanup_locked()
            self._entries[token] = source
        return source

    def get(self, token: str) -> MediaSource:
        with self._lock:
            self._cleanup_locked()
            source = self._entries.get(token)
            if source is None:
                raise KeyError(token)
            return source

    def _cleanup_locked(self) -> None:
        now = time.time()
        expired = [
            token for token, source in self._entries.items() if source.expires_at <= now
        ]
        for token in expired:
            self._entries.pop(token, None)


__all__ = [
    "MediaRegistry",
    "MediaSource",
    "is_allowed_media_url",
    "safe_filename",
]
