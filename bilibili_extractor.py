from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

from curl_cffi import requests

from instagram_extractor import DEFAULT_PROXY, normalize_proxy


BILIBILI_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_BVID_RE = re.compile(r"(?<![A-Za-z0-9])(BV[A-Za-z0-9]{10})(?![A-Za-z0-9])", re.IGNORECASE)
_BILIBILI_HOSTS = ("bilibili.com", "b23.tv")
_QUALITY_LABELS = {
    64: "720P",
    32: "480P",
    16: "360P",
}


class BilibiliError(RuntimeError):
    pass


class BilibiliURLInvalid(BilibiliError):
    pass


class BilibiliRequestError(BilibiliError):
    pass


class BilibiliMediaNotFound(BilibiliError):
    pass


@dataclass
class BilibiliMediaItem:
    kind: str
    url: str
    width: int | None = None
    height: int | None = None
    duration: int | None = None
    format: str | None = None
    quality: str | None = None


@dataclass
class BilibiliResult:
    media_id: str
    original_url: str
    author: str | None
    author_name: str | None
    author_avatar: str | None
    caption: str | None
    cover_url: str | None
    media: list[BilibiliMediaItem]
    stats: dict[str, int]
    source: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _is_bilibili_host(host: str) -> bool:
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in _BILIBILI_HOSTS)


def normalize_bilibili_url(url: str) -> str:
    value = url.strip()
    if not value:
        raise BilibiliURLInvalid("哔哩哔哩链接不能为空")
    if "://" not in value:
        value = f"https://{value.lstrip('/')}"
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not _is_bilibili_host(host):
        raise BilibiliURLInvalid("不是有效的哔哩哔哩链接")
    return value


def extract_bvid(url: str) -> str | None:
    match = _BVID_RE.search(url)
    if not match:
        return None
    value = match.group(1)
    return f"BV{value[2:]}"


def _https_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    if value.startswith("https://"):
        return value
    if value.startswith("http://"):
        return f"https://{value[7:]}"
    if value.startswith("//"):
        return f"https:{value}"
    return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _scaled_dimensions(page: dict[str, Any], quality: int) -> tuple[int | None, int | None]:
    dimension = page.get("dimension")
    if not isinstance(dimension, dict):
        return None, None
    width = _int_or_none(dimension.get("width"))
    height = _int_or_none(dimension.get("height"))
    edge = _QUALITY_LABELS.get(quality)
    target = int(edge[:-1]) if edge else None
    if not width or not height or not target:
        return width, height
    current = height if width >= height else width
    if current <= target:
        return width, height
    scale = target / current
    return round(width * scale / 2) * 2, round(height * scale / 2) * 2


class BilibiliExtractor:
    VIEW_API = "https://api.bilibili.com/x/web-interface/view"
    PLAYURL_API = "https://api.bilibili.com/x/player/playurl"

    def __init__(
        self,
        url: str,
        proxy: str | None = DEFAULT_PROXY,
        timeout: float = 30,
    ) -> None:
        self.url = normalize_bilibili_url(url)
        self.proxy = normalize_proxy(proxy)
        self.timeout = timeout
        self.proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None
        self.s = requests.Session(proxies=self.proxies)
        self.referer = self.url

    def request_headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": self.referer,
            "User-Agent": BILIBILI_USER_AGENT,
        }

    def _request(self, url: str, **kwargs: Any) -> Any:
        kwargs.setdefault("headers", self.request_headers())
        kwargs.setdefault("impersonate", "chrome124")
        kwargs.setdefault("timeout", self.timeout)
        kwargs.setdefault("allow_redirects", True)
        try:
            response = self.s.get(url, **kwargs)
        except Exception as exc:
            raise BilibiliRequestError(f"哔哩哔哩请求失败: {exc}") from exc
        if not 200 <= response.status_code < 300:
            raise BilibiliRequestError(
                f"哔哩哔哩返回 HTTP {response.status_code}: {url}"
            )
        return response

    def _request_api(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        response = self._request(url, params=params)
        try:
            payload = json.loads(response.text)
        except (TypeError, json.JSONDecodeError) as exc:
            raise BilibiliRequestError("哔哩哔哩接口返回了无效 JSON") from exc
        if not isinstance(payload, dict):
            raise BilibiliRequestError("哔哩哔哩接口返回格式无效")
        code = _int_or_none(payload.get("code"))
        if code != 0:
            message = payload.get("message") or payload.get("msg") or "未知错误"
            if code == -404:
                raise BilibiliMediaNotFound(str(message))
            raise BilibiliRequestError(f"哔哩哔哩接口错误 {code}: {message}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise BilibiliRequestError("哔哩哔哩接口缺少 data")
        return data

    def _resolve_bvid(self) -> str:
        bvid = extract_bvid(self.url)
        if bvid:
            self.referer = self.url
            return bvid

        host = (urlparse(self.url).hostname or "").lower()
        if host == "b23.tv" or host.endswith(".b23.tv"):
            response = self._request(self.url)
            final_url = str(response.url)
            final_host = (urlparse(final_url).hostname or "").lower()
            if not _is_bilibili_host(final_host):
                raise BilibiliRequestError("哔哩哔哩短链跳转到了非哔哩哔哩域名")
            bvid = extract_bvid(final_url)
            if bvid:
                self.referer = final_url
                return bvid
        raise BilibiliURLInvalid("链接中没有找到 BV 号")

    def _page_number(self) -> int:
        values = parse_qs(urlparse(self.referer).query).get("p")
        page = _int_or_none(values[0]) if values else 1
        return page if page and page > 0 else 1

    def _fetch_complete_mp4(self, bvid: str, cid: int) -> tuple[dict[str, Any], str]:
        last_reason = "没有返回 durl"
        for requested_quality in (64, 32, 16):
            data = self._request_api(
                self.PLAYURL_API,
                {
                    "bvid": bvid,
                    "cid": cid,
                    "qn": requested_quality,
                    "fnval": 0,
                    "fourk": 1,
                },
            )
            durl = data.get("durl")
            if not isinstance(durl, list) or not durl:
                last_reason = "没有返回 durl"
                continue
            if len(durl) != 1 or not isinstance(durl[0], dict):
                last_reason = "只返回了分段 durl"
                continue
            media_url = _https_url(durl[0].get("url"))
            response_format = str(data.get("format") or "").lower()
            path = urlparse(media_url or "").path.lower()
            if media_url and (response_format.startswith("mp4") or path.endswith(".mp4")):
                return data, media_url
            last_reason = f"返回格式不是 MP4: {response_format or 'unknown'}"
        raise BilibiliMediaNotFound(f"该视频没有可用的完整 durl MP4（{last_reason}）")

    def extract(self) -> BilibiliResult:
        bvid = self._resolve_bvid()
        view = self._request_api(self.VIEW_API, {"bvid": bvid})
        pages = view.get("pages")
        if not isinstance(pages, list) or not pages:
            raise BilibiliMediaNotFound("该视频没有可用分P信息")
        page_number = self._page_number()
        if page_number > len(pages) or not isinstance(pages[page_number - 1], dict):
            raise BilibiliMediaNotFound(f"该视频没有第 {page_number} P")
        page = pages[page_number - 1]
        cid = _int_or_none(page.get("cid"))
        if not cid:
            raise BilibiliMediaNotFound("该视频缺少 cid")

        canonical_url = f"https://www.bilibili.com/video/{bvid}"
        if page_number > 1:
            canonical_url = f"{canonical_url}?p={page_number}"
        self.referer = canonical_url
        play, media_url = self._fetch_complete_mp4(bvid, cid)
        quality = _int_or_none(play.get("quality")) or 0
        width, height = _scaled_dimensions(page, quality)

        owner = view.get("owner")
        owner = owner if isinstance(owner, dict) else {}
        stat = view.get("stat")
        stat = stat if isinstance(stat, dict) else {}
        stats: dict[str, int] = {}
        for source_key, target_key in (
            ("view", "playCount"),
            ("like", "diggCount"),
            ("reply", "commentCount"),
            ("share", "shareCount"),
        ):
            count = _int_or_none(stat.get(source_key))
            if count is not None:
                stats[target_key] = count

        duration_ms = _int_or_none(play.get("timelength"))
        return BilibiliResult(
            media_id=bvid,
            original_url=canonical_url,
            author=str(owner.get("mid")) if owner.get("mid") is not None else None,
            author_name=str(owner.get("name") or "") or None,
            author_avatar=_https_url(owner.get("face")),
            caption=str(view.get("title") or "") or None,
            cover_url=_https_url(view.get("pic")),
            media=[
                BilibiliMediaItem(
                    kind="video",
                    url=media_url,
                    width=width,
                    height=height,
                    duration=round(duration_ms / 1000) if duration_ms else None,
                    format="mp4",
                    quality=_QUALITY_LABELS.get(quality, f"清晰度 {quality}"),
                )
            ],
            stats=stats,
            source="playurl-durl",
        )

    def cookies(self) -> dict[str, str]:
        try:
            return self.s.cookies.get_dict()
        except Exception:
            return {}


def extract_bilibili(
    url: str,
    proxy: str | None = DEFAULT_PROXY,
    timeout: float = 30,
) -> BilibiliResult:
    return BilibiliExtractor(url, proxy=proxy, timeout=timeout).extract()


__all__ = [
    "BILIBILI_USER_AGENT",
    "BilibiliError",
    "BilibiliExtractor",
    "BilibiliMediaItem",
    "BilibiliMediaNotFound",
    "BilibiliRequestError",
    "BilibiliResult",
    "BilibiliURLInvalid",
    "extract_bilibili",
    "extract_bvid",
    "normalize_bilibili_url",
]
