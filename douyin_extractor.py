from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

from curl_cffi import requests


DEFAULT_PROXY = os.getenv(
    "DOUYIN_PROXY", os.getenv("MEDIA_PROXY", "http://127.0.0.1:2080")
)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
MOBILE_USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Mobile Safari/537.36"
)

_DOUYIN_URL_RE = re.compile(
    r"(?<![a-z0-9.-])"
    r"(?:(?:https?://)?(?:[a-z0-9-]+\.)*(?:douyin\.com|iesdouyin\.com))"
    r"/[^\s<>\"']+",
    re.IGNORECASE,
)
_AWEME_PATH_RE = re.compile(r"/(?:share/)?(?:video|note)/(\d+)(?:/|$)")
_ROUTER_DATA_RE = re.compile(
    r"window\._ROUTER_DATA\s*=\s*(.*?)</script>", re.DOTALL
)
_TRAILING_PUNCTUATION = ".,;!?，。；！？、）)]}》】」』"
_DOUYIN_HOSTS = ("douyin.com", "iesdouyin.com")


class DouyinError(RuntimeError):
    """Base exception for Douyin extraction failures."""


class DouyinURLInvalid(DouyinError):
    pass


class DouyinRequestError(DouyinError):
    pass


class DouyinMediaNotFound(DouyinError):
    pass


@dataclass
class DouyinMediaItem:
    kind: str
    url: str
    width: int | None = None
    height: int | None = None
    duration: int | None = None
    format: str | None = None
    quality: str | None = None


@dataclass
class DouyinResult:
    media_id: str
    share_url: str
    real_url: str
    author: str | None
    author_name: str | None
    author_avatar: str | None
    caption: str | None
    cover_url: str | None
    media: list[DouyinMediaItem]
    stats: dict[str, int]
    source: str

    @property
    def primary_url(self) -> str | None:
        return self.media[0].url if self.media else None

    @property
    def video_urls(self) -> list[str]:
        return [item.url for item in self.media if item.kind == "video"]

    @property
    def image_urls(self) -> list[str]:
        return [item.url for item in self.media if item.kind == "image"]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _is_douyin_host(host: str) -> bool:
    return any(
        host == suffix or host.endswith(f".{suffix}")
        for suffix in _DOUYIN_HOSTS
    )


def normalize_proxy(proxy: str | None) -> str | None:
    if proxy is None:
        return None
    value = proxy.strip()
    if not value:
        return None
    if "://" not in value:
        value = f"http://{value}"
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https", "socks4", "socks5", "socks5h"}:
        raise DouyinURLInvalid(f"Unsupported proxy scheme: {parsed.scheme}")
    if not parsed.hostname or not parsed.port:
        raise DouyinURLInvalid(f"Invalid proxy: {proxy}")
    return value


def extract_douyin_url(text_or_url: str) -> str:
    """Extract the first Douyin URL from a URL or a complete share message."""
    if not isinstance(text_or_url, str) or not text_or_url.strip():
        raise DouyinURLInvalid("抖音链接或分享文案不能为空")

    match = _DOUYIN_URL_RE.search(text_or_url.strip())
    if not match:
        raise DouyinURLInvalid("分享文案中没有找到抖音链接")

    url = match.group(0).rstrip(_TRAILING_PUNCTUATION)
    if "://" not in url:
        url = f"https://{url}"
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not _is_douyin_host(host):
        raise DouyinURLInvalid("不是有效的抖音链接")
    return url


def extract_aweme_id(url: str) -> str | None:
    parsed = urlparse(url)
    match = _AWEME_PATH_RE.search(parsed.path)
    if match:
        return match.group(1)
    query = parse_qs(parsed.query)
    for key in ("aweme_id", "modal_id", "item_id"):
        value = query.get(key, [None])[0]
        if value and str(value).isdigit():
            return str(value)
    return None


def _desktop_headers() -> dict[str, str]:
    return {
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "User-Agent": USER_AGENT,
    }


def _mobile_headers() -> dict[str, str]:
    return {
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "User-Agent": MOBILE_USER_AGENT,
    }


def resolve_douyin_url(
    text_or_url: str,
    *,
    session: Any | None = None,
    timeout: float = 30,
) -> tuple[str, Any, str]:
    """Extract a share URL and follow redirects; real_url is response.url."""
    share_url = extract_douyin_url(text_or_url)
    client = session or requests.Session()
    try:
        response = client.get(
            share_url,
            headers=_desktop_headers(),
            impersonate="chrome124",
            allow_redirects=True,
            timeout=timeout,
        )
    except Exception as exc:
        raise DouyinRequestError(f"抖音链接跳转失败: {exc}") from exc

    real_url = str(response.url)
    parsed = urlparse(real_url)
    if not _is_douyin_host((parsed.hostname or "").lower()):
        raise DouyinRequestError("抖音短链跳转到了非抖音域名")
    if not (extract_aweme_id(real_url) or extract_aweme_id(share_url)):
        raise DouyinURLInvalid(
            f"跳转后没有找到视频 ID（HTTP {response.status_code}）"
        )
    return share_url, response, real_url


def _first_url(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    urls = value.get("url_list")
    if not isinstance(urls, list):
        return None
    for url in urls:
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            return url
    return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _parse_router_data(html_text: str) -> dict[str, Any]:
    match = _ROUTER_DATA_RE.search(html_text)
    if not match:
        raise DouyinRequestError("抖音分享页没有返回 _ROUTER_DATA")
    source = match.group(1).strip()
    if source.endswith(";"):
        source = source[:-1].rstrip()
    try:
        router_data = json.loads(source)
    except json.JSONDecodeError as exc:
        raise DouyinRequestError("抖音分享页的 _ROUTER_DATA 无效") from exc

    loader_data = router_data.get("loaderData")
    if not isinstance(loader_data, dict):
        raise DouyinRequestError("抖音分享页缺少 loaderData")
    video_info: dict[str, Any] | None = None
    for route_name, route_data in loader_data.items():
        if not route_name.endswith("/page") or not isinstance(route_data, dict):
            continue
        candidate = route_data.get("videoInfoRes")
        if isinstance(candidate, dict):
            video_info = candidate
            break
    if video_info is None:
        raise DouyinRequestError("抖音分享页缺少 videoInfoRes")
    if video_info.get("status_code") not in {None, 0}:
        raise DouyinMediaNotFound(
            str(video_info.get("status_msg") or "抖音作品不可用")
        )
    items = video_info.get("item_list")
    if not isinstance(items, list) or not items or not isinstance(items[0], dict):
        filters = video_info.get("filter_list")
        suffix = f": {filters}" if filters else ""
        raise DouyinMediaNotFound(f"抖音作品没有返回媒体信息{suffix}")
    return items[0]


def _cover_url(item: dict[str, Any], image_urls: list[str]) -> str | None:
    video = item.get("video")
    if isinstance(video, dict):
        for key in ("cover_original_scale", "cover", "origin_cover"):
            url = _first_url(video.get(key))
            if url:
                return url
    return image_urls[0] if image_urls else None


def _item_to_result(
    item: dict[str, Any],
    *,
    aweme_id: str,
    share_url: str,
    real_url: str,
) -> DouyinResult:
    media: list[DouyinMediaItem] = []
    image_urls: list[str] = []
    images = item.get("images")
    if isinstance(images, list) and images:
        for image in images:
            if not isinstance(image, dict):
                continue
            url = _first_url(image)
            if not url:
                continue
            image_urls.append(url)
            media.append(
                DouyinMediaItem(
                    kind="image",
                    url=url,
                    width=_int_or_none(image.get("width")),
                    height=_int_or_none(image.get("height")),
                    format="image",
                )
            )
    else:
        video = item.get("video")
        video = video if isinstance(video, dict) else {}
        play_url: str | None = None
        bit_rates = video.get("bit_rate")
        if isinstance(bit_rates, list) and bit_rates:
            first_rate = bit_rates[0]
            if isinstance(first_rate, dict):
                play_url = _first_url(first_rate.get("play_addr"))
        play_url = play_url or _first_url(video.get("play_addr"))
        if play_url:
            duration_ms = _int_or_none(video.get("duration") or item.get("duration"))
            media.append(
                DouyinMediaItem(
                    kind="video",
                    url=play_url.replace("/playwm/", "/play/"),
                    width=_int_or_none(video.get("width")),
                    height=_int_or_none(video.get("height")),
                    duration=round(duration_ms / 1000) if duration_ms else None,
                    format="mp4",
                    quality=str(video.get("ratio") or "original"),
                )
            )
    if not media:
        raise DouyinMediaNotFound("抖音作品没有可用媒体")

    author_data = item.get("author")
    author_data = author_data if isinstance(author_data, dict) else {}
    author = str(
        author_data.get("unique_id") or author_data.get("short_id") or ""
    ) or None
    author_name = str(author_data.get("nickname") or "") or None
    author_avatar = _first_url(author_data.get("avatar_thumb"))

    statistics = item.get("statistics")
    statistics = statistics if isinstance(statistics, dict) else {}
    stats: dict[str, int] = {}
    for source_key, target_key in (
        ("play_count", "playCount"),
        ("digg_count", "diggCount"),
        ("comment_count", "commentCount"),
        ("share_count", "shareCount"),
    ):
        count = _int_or_none(statistics.get(source_key))
        if count is not None:
            stats[target_key] = count

    caption = item.get("desc")
    return DouyinResult(
        media_id=aweme_id,
        share_url=share_url,
        real_url=real_url,
        author=author,
        author_name=author_name,
        author_avatar=author_avatar,
        caption=caption if isinstance(caption, str) else None,
        cover_url=_cover_url(item, image_urls),
        media=media,
        stats=stats,
        source="router-data",
    )


class DouyinExtractor:
    def __init__(
        self,
        text_or_url: str,
        proxy: str | None = DEFAULT_PROXY,
        timeout: float = 30,
    ) -> None:
        self.original_text = text_or_url
        self.share_url = extract_douyin_url(text_or_url)
        self.proxy = normalize_proxy(proxy)
        self.timeout = timeout
        self.impersonate = "chrome124"
        self.proxies = (
            {"http": self.proxy, "https": self.proxy} if self.proxy else None
        )
        self.s = requests.Session(proxies=self.proxies)
        self.response: Any | None = None
        self.real_url: str | None = None
        self.realurl: str | None = None
        self.aweme_id = extract_aweme_id(self.share_url)
        self.referer = self.share_url
        self.result: DouyinResult | None = None

    def _request(
        self,
        method: str,
        url: str,
        *,
        raise_for_status: bool = True,
        **kwargs: Any,
    ) -> Any:
        kwargs.setdefault("timeout", self.timeout)
        kwargs.setdefault("allow_redirects", True)
        kwargs.setdefault("impersonate", self.impersonate)
        try:
            response = self.s.request(method, url, **kwargs)
        except Exception as exc:
            raise DouyinRequestError(f"Request failed for {url}: {exc}") from exc
        if raise_for_status and not 200 <= response.status_code < 300:
            raise DouyinRequestError(
                f"Douyin returned HTTP {response.status_code} for {url}"
            )
        return response

    def _resolve_real_url(self) -> None:
        response = self._request(
            "GET",
            self.share_url,
            raise_for_status=False,
            headers=_desktop_headers(),
        )
        real_url = str(response.url)
        parsed = urlparse(real_url)
        if not _is_douyin_host((parsed.hostname or "").lower()):
            raise DouyinRequestError("抖音短链跳转到了非抖音域名")
        aweme_id = extract_aweme_id(real_url) or extract_aweme_id(self.share_url)
        if not aweme_id:
            raise DouyinURLInvalid(
                f"跳转后没有找到视频 ID（HTTP {response.status_code}）"
            )
        self.response = response
        self.real_url = real_url
        self.realurl = real_url
        self.aweme_id = aweme_id
        self.referer = real_url

    def _fetch_item(self) -> dict[str, Any]:
        if not self.aweme_id:
            raise DouyinURLInvalid("无法从抖音链接提取视频 ID")
        page_url = f"https://www.douyin.com/share/video/{self.aweme_id}/"
        response = self._request("GET", page_url, headers=_mobile_headers())
        return _parse_router_data(response.text)

    def extract(self) -> DouyinResult:
        self._resolve_real_url()
        if not self.aweme_id or not self.real_url:
            raise DouyinURLInvalid("无法从抖音链接提取视频 ID")
        item = self._fetch_item()
        result = _item_to_result(
            item,
            aweme_id=self.aweme_id,
            share_url=self.share_url,
            real_url=self.real_url,
        )
        self.result = result
        return result

    def cookies(self) -> dict[str, str]:
        try:
            return self.s.cookies.get_dict()
        except Exception:
            return {}


def extract_douyin(
    text_or_url: str,
    proxy: str | None = DEFAULT_PROXY,
    timeout: float = 30,
) -> DouyinResult:
    return DouyinExtractor(text_or_url, proxy=proxy, timeout=timeout).extract()


__all__ = [
    "DEFAULT_PROXY",
    "MOBILE_USER_AGENT",
    "USER_AGENT",
    "DouyinError",
    "DouyinExtractor",
    "DouyinMediaItem",
    "DouyinMediaNotFound",
    "DouyinRequestError",
    "DouyinResult",
    "DouyinURLInvalid",
    "extract_aweme_id",
    "extract_douyin",
    "extract_douyin_url",
    "normalize_proxy",
    "resolve_douyin_url",
]
