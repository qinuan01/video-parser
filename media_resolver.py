from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import instagram_extractor as instagram
from tiktok_extractor import (
    TIKTOK_USER_AGENT,
    TikTokError,
    TikTokExtractor,
    TikTokMediaNotFound,
    TikTokURLInvalid,
)


class ResolverError(RuntimeError):
    pass


class InvalidMediaURL(ResolverError):
    pass


class MediaUnavailable(ResolverError):
    pass


class UpstreamMediaError(ResolverError):
    pass


@dataclass
class RequestContext:
    referer: str
    user_agent: str
    cookies: dict[str, str] = field(default_factory=dict)


@dataclass
class ResolvedItem:
    kind: str
    url: str
    width: int | None = None
    height: int | None = None
    duration: int | None = None
    format: str | None = None
    quality: str | None = None


@dataclass
class ResolvedResult:
    platform: str
    media_id: str
    original_url: str
    author: str | None
    author_name: str | None
    author_avatar: str | None
    caption: str | None
    thumbnail_url: str | None
    media: list[ResolvedItem]
    stats: dict[str, int]
    source: str
    context: RequestContext

    def public_metadata(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "media_id": self.media_id,
            "original_url": self.original_url,
            "author": self.author,
            "author_name": self.author_name,
            "caption": self.caption,
            "stats": self.stats,
            "source": self.source,
        }


def _normalize_input_url(url: str) -> str:
    value = url.strip()
    if not value:
        raise InvalidMediaURL("链接不能为空")
    if "://" not in value:
        value = f"https://{value.lstrip('/')}"
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise InvalidMediaURL("链接格式无效")
    return value


def detect_platform(url: str) -> str:
    value = _normalize_input_url(url)
    host = (urlparse(value).hostname or "").lower()
    if host == "instagram.com" or host.endswith(".instagram.com"):
        return "instagram"
    if host == "tiktok.com" or host.endswith(".tiktok.com"):
        return "tiktok"
    raise InvalidMediaURL("仅支持 Instagram 和 TikTok 链接")


def _cookie_dict(session: Any) -> dict[str, str]:
    try:
        return session.cookies.get_dict()
    except Exception:
        return {}


def _resolve_instagram(url: str, proxy: str | None, timeout: float) -> ResolvedResult:
    extractor = instagram.ins(url, proxy=proxy, timeout=timeout)
    try:
        result = extractor.extract()
    except instagram.InstagramURLInvalid as exc:
        raise InvalidMediaURL(str(exc)) from exc
    except instagram.InstagramMediaNotFound as exc:
        raise MediaUnavailable(str(exc)) from exc
    except instagram.InstagramError as exc:
        raise UpstreamMediaError(str(exc)) from exc

    items: list[ResolvedItem] = []
    if result.video_urls:
        items.extend(
            ResolvedItem(kind="video", url=media_url, format="mp4")
            for media_url in result.video_urls
        )
    else:
        items.extend(
            ResolvedItem(kind="image", url=media_url, format="image")
            for media_url in result.image_urls
        )
    if not items:
        raise MediaUnavailable("Instagram 没有返回可用媒体")

    return ResolvedResult(
        platform="instagram",
        media_id=result.media_id or result.shortcode,
        original_url=url,
        author=result.username,
        author_name=None,
        author_avatar=None,
        caption=result.caption,
        thumbnail_url=result.image_urls[0] if result.image_urls else None,
        media=items,
        stats={},
        source=result.source,
        context=RequestContext(
            referer=extractor.url,
            user_agent=instagram.USER_AGENT,
            cookies=_cookie_dict(extractor.s),
        ),
    )


def _resolve_tiktok(url: str, proxy: str | None, timeout: float) -> ResolvedResult:
    try:
        extractor = TikTokExtractor(url, proxy=proxy, timeout=timeout)
        result = extractor.extract()
    except TikTokURLInvalid as exc:
        raise InvalidMediaURL(str(exc)) from exc
    except TikTokMediaNotFound as exc:
        raise MediaUnavailable(str(exc)) from exc
    except TikTokError as exc:
        raise UpstreamMediaError(str(exc)) from exc

    return ResolvedResult(
        platform="tiktok",
        media_id=result.media_id,
        original_url=url,
        author=result.author,
        author_name=result.author_name,
        author_avatar=result.author_avatar,
        caption=result.caption,
        thumbnail_url=result.cover_url,
        media=[
            ResolvedItem(
                kind=item.kind,
                url=item.url,
                width=item.width,
                height=item.height,
                duration=item.duration,
                format=item.format,
                quality=item.quality,
            )
            for item in result.media
        ],
        stats=result.stats,
        source=result.source,
        context=RequestContext(
            referer=extractor.referer,
            user_agent=TIKTOK_USER_AGENT,
            cookies=extractor.cookies(),
        ),
    )


def resolve_media(
    url: str,
    proxy: str | None = instagram.DEFAULT_PROXY,
    timeout: float = 30,
) -> ResolvedResult:
    normalized = _normalize_input_url(url)
    platform = detect_platform(normalized)
    if platform == "instagram":
        return _resolve_instagram(normalized, proxy, timeout)
    return _resolve_tiktok(normalized, proxy, timeout)


__all__ = [
    "InvalidMediaURL",
    "MediaUnavailable",
    "RequestContext",
    "ResolvedItem",
    "ResolvedResult",
    "ResolverError",
    "UpstreamMediaError",
    "detect_platform",
    "resolve_media",
]
