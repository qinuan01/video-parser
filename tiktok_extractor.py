from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from typing import Any, Iterator
from urllib.parse import urlparse

from curl_cffi import requests

from instagram_extractor import DEFAULT_PROXY, normalize_proxy


TIKTOK_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_VIDEO_ID_RE = re.compile(r"/video/(\d+)(?:/|$)")


class TikTokError(RuntimeError):
    pass


class TikTokURLInvalid(TikTokError):
    pass


class TikTokRequestError(TikTokError):
    pass


class TikTokMediaNotFound(TikTokError):
    pass


@dataclass
class TikTokMediaItem:
    kind: str
    url: str
    width: int | None = None
    height: int | None = None
    duration: int | None = None
    format: str | None = None
    quality: str | None = None


@dataclass
class TikTokResult:
    media_id: str
    author: str | None
    author_name: str | None
    author_avatar: str | None
    caption: str | None
    cover_url: str | None
    media: list[TikTokMediaItem]
    stats: dict[str, int]
    source: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _TikTokScriptParser(HTMLParser):
    SCRIPT_IDS = {
        "__UNIVERSAL_DATA_FOR_REHYDRATION__",
        "SIGI_STATE",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.scripts: dict[str, str] = {}
        self._active_id: str | None = None
        self._parts: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag.lower() != "script":
            return
        script_id = dict(attrs).get("id")
        if script_id in self.SCRIPT_IDS:
            self._active_id = script_id
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._active_id:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "script" or not self._active_id:
            return
        self.scripts[self._active_id] = "".join(self._parts).strip()
        self._active_id = None
        self._parts = []


def _is_tiktok_host(host: str) -> bool:
    return host == "tiktok.com" or host.endswith(".tiktok.com")


def normalize_tiktok_url(url: str) -> str:
    value = url.strip()
    if not value:
        raise TikTokURLInvalid("TikTok URL is empty")
    if "://" not in value:
        value = f"https://{value.lstrip('/')}"
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not _is_tiktok_host(host):
        raise TikTokURLInvalid("Not a TikTok URL")
    return value


def _walk_json(root: Any) -> Iterator[Any]:
    stack = [root]
    while stack:
        value = stack.pop()
        yield value
        if isinstance(value, dict):
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)


def _parse_json_scripts(html_text: str) -> dict[str, Any]:
    parser = _TikTokScriptParser()
    parser.feed(html_text)
    parser.close()
    payloads: dict[str, Any] = {}
    for script_id, text in parser.scripts.items():
        try:
            payloads[script_id] = json.loads(text)
        except json.JSONDecodeError:
            continue
    return payloads


def _find_item(payloads: dict[str, Any], video_id: str | None) -> tuple[dict[str, Any], str]:
    universal = payloads.get("__UNIVERSAL_DATA_FOR_REHYDRATION__")
    if isinstance(universal, dict):
        scope = universal.get("__DEFAULT_SCOPE__")
        detail = scope.get("webapp.video-detail") if isinstance(scope, dict) else None
        if isinstance(detail, dict) and detail.get("statusCode") not in {None, 0}:
            message = detail.get("statusMsg") or "TikTok media is unavailable"
            raise TikTokMediaNotFound(str(message))
        item_info = detail.get("itemInfo") if isinstance(detail, dict) else None
        item = item_info.get("itemStruct") if isinstance(item_info, dict) else None
        if isinstance(item, dict) and (not video_id or str(item.get("id")) == video_id):
            return item, "universal"

    sigi = payloads.get("SIGI_STATE")
    if isinstance(sigi, dict):
        item_module = sigi.get("ItemModule")
        if isinstance(item_module, dict):
            item = item_module.get(video_id) if video_id else None
            if isinstance(item, dict):
                return item, "sigi"

    for payload in payloads.values():
        for value in _walk_json(payload):
            if not isinstance(value, dict):
                continue
            if video_id and str(value.get("id")) != video_id:
                continue
            if isinstance(value.get("video"), dict) or isinstance(
                value.get("imagePost"), dict
            ):
                return value, "recursive"
    raise TikTokMediaNotFound("TikTok did not return public media data")


def _first_url(value: Any) -> str | None:
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return value
    if not isinstance(value, dict):
        return None
    for key in ("urlList", "UrlList", "urls"):
        urls = value.get(key)
        if isinstance(urls, list):
            for url in urls:
                if isinstance(url, str) and url.startswith(("http://", "https://")):
                    return url
    return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _item_to_result(item: dict[str, Any], source: str) -> TikTokResult:
    video_id = str(item.get("id") or "")
    if not video_id:
        raise TikTokMediaNotFound("TikTok media ID is missing")

    media: list[TikTokMediaItem] = []
    video = item.get("video")
    cover_url: str | None = None
    if isinstance(video, dict):
        play_url = _first_url(video.get("playAddr")) or _first_url(
            video.get("PlayAddrStruct")
        )
        if not play_url:
            bitrate_info = video.get("bitrateInfo")
            candidates: list[tuple[int, str]] = []
            if isinstance(bitrate_info, list):
                for entry in bitrate_info:
                    if not isinstance(entry, dict):
                        continue
                    codec = str(entry.get("CodecType") or "").lower()
                    if codec and codec != "h264":
                        continue
                    url = _first_url(entry.get("PlayAddr"))
                    if url:
                        candidates.append((_int_or_none(entry.get("Bitrate")) or 0, url))
            if candidates:
                play_url = max(candidates, key=lambda candidate: candidate[0])[1]
        if play_url:
            media.append(
                TikTokMediaItem(
                    kind="video",
                    url=play_url,
                    width=_int_or_none(video.get("width")),
                    height=_int_or_none(video.get("height")),
                    duration=_int_or_none(video.get("duration")),
                    format=str(video.get("format") or "mp4"),
                    quality=str(video.get("definition") or video.get("ratio") or ""),
                )
            )
        cover_url = (
            _first_url(video.get("cover"))
            or _first_url(video.get("originCover"))
            or _first_url(video.get("dynamicCover"))
        )

    image_post = item.get("imagePost")
    if isinstance(image_post, dict):
        images = image_post.get("images")
        if isinstance(images, list):
            for image in images:
                if not isinstance(image, dict):
                    continue
                image_url = _first_url(image.get("imageURL")) or _first_url(
                    image.get("displayImage")
                )
                if not image_url:
                    continue
                media.append(
                    TikTokMediaItem(
                        kind="image",
                        url=image_url,
                        width=_int_or_none(image.get("imageWidth")),
                        height=_int_or_none(image.get("imageHeight")),
                        format="image",
                    )
                )
            if media and not cover_url:
                cover_url = media[0].url

    if not media:
        raise TikTokMediaNotFound("TikTok metadata contains no playable media")

    author_data = item.get("author")
    author = author_data.get("uniqueId") if isinstance(author_data, dict) else None
    author_name = author_data.get("nickname") if isinstance(author_data, dict) else None
    author_avatar = None
    if isinstance(author_data, dict):
        author_avatar = _first_url(author_data.get("avatarLarger")) or _first_url(
            author_data.get("avatarMedium")
        )

    stats_data = item.get("stats")
    stats: dict[str, int] = {}
    if isinstance(stats_data, dict):
        for key in ("playCount", "diggCount", "commentCount", "shareCount"):
            number = _int_or_none(stats_data.get(key))
            if number is not None:
                stats[key] = number

    return TikTokResult(
        media_id=video_id,
        author=author if isinstance(author, str) else None,
        author_name=author_name if isinstance(author_name, str) else None,
        author_avatar=author_avatar,
        caption=item.get("desc") if isinstance(item.get("desc"), str) else None,
        cover_url=cover_url,
        media=media,
        stats=stats,
        source=source,
    )


class TikTokExtractor:
    def __init__(
        self,
        url: str,
        proxy: str | None = DEFAULT_PROXY,
        timeout: float = 30,
    ) -> None:
        self.url = normalize_tiktok_url(url)
        self.proxy = normalize_proxy(proxy)
        self.timeout = timeout
        self.proxies = (
            {"http": self.proxy, "https": self.proxy} if self.proxy else None
        )
        self.s = requests.Session(proxies=self.proxies)
        self.referer = self.url

    def request_headers(self) -> dict[str, str]:
        return {
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "User-Agent": TIKTOK_USER_AGENT,
        }

    def cookies(self) -> dict[str, str]:
        try:
            return self.s.cookies.get_dict()
        except Exception:
            return {}

    def extract(self) -> TikTokResult:
        try:
            response = self.s.get(
                self.url,
                headers=self.request_headers(),
                impersonate="chrome124",
                timeout=self.timeout,
                allow_redirects=True,
            )
        except Exception as exc:
            raise TikTokRequestError(f"TikTok request failed: {exc}") from exc

        if response.status_code != 200:
            raise TikTokRequestError(
                f"TikTok returned HTTP {response.status_code}"
            )

        final_url = str(response.url)
        final_host = (urlparse(final_url).hostname or "").lower()
        if not _is_tiktok_host(final_host):
            raise TikTokRequestError("TikTok redirected to an unexpected host")
        self.referer = final_url

        match = _VIDEO_ID_RE.search(urlparse(final_url).path) or _VIDEO_ID_RE.search(
            urlparse(self.url).path
        )
        if not match:
            raise TikTokURLInvalid("Expected a TikTok /video/ URL")
        video_id = match.group(1)
        payloads = _parse_json_scripts(response.text)
        item, source = _find_item(payloads, video_id)
        return _item_to_result(item, source)


def extract_tiktok(
    url: str,
    proxy: str | None = DEFAULT_PROXY,
    timeout: float = 30,
) -> TikTokResult:
    return TikTokExtractor(url, proxy=proxy, timeout=timeout).extract()


__all__ = [
    "TikTokError",
    "TikTokExtractor",
    "TikTokMediaItem",
    "TikTokMediaNotFound",
    "TikTokRequestError",
    "TikTokResult",
    "TikTokURLInvalid",
    "extract_tiktok",
    "normalize_tiktok_url",
]
