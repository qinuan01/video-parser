from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from typing import Any, Iterator
from urllib.parse import urlparse

from curl_cffi import requests


DEFAULT_PROXY = os.getenv("INSTAGRAM_PROXY", "http://127.0.0.1:2080")
INSTAGRAM_APP_ID = "936619743392459"
POST_ROOT_DOC_ID = "27130156389949648"
POST_ROOT_FRIENDLY_NAME = "PolarisLoggedOutDesktopWWWPostRootContentQuery"
SHORTCODE_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_SHORTCODE_PATH_RE = re.compile(
    r"^/(?:reel|reels|p|tv)/([A-Za-z0-9_-]+)(?:/|$)", re.IGNORECASE
)
_LSD_RE = re.compile(r'\["LSD",\[\],\{"token":"([^"]+)"')


class InstagramError(RuntimeError):
    """Base exception for extraction failures."""


class InstagramURLInvalid(InstagramError):
    pass


class InstagramRequestError(InstagramError):
    pass


class InstagramMediaNotFound(InstagramError):
    pass


@dataclass
class MediaResult:
    shortcode: str
    media_id: str | None
    video_urls: list[str]
    image_urls: list[str]
    dash_manifest: str | None
    username: str | None
    caption: str | None
    source: str

    @property
    def primary_url(self) -> str | None:
        if self.video_urls:
            return self.video_urls[0]
        if self.image_urls:
            return self.image_urls[0]
        return None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _InstagramHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.json_scripts: list[str] = []
        self.meta: dict[str, str] = {}
        self._json_script_depth = 0
        self._script_parts: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attributes = {key.lower(): value for key, value in attrs}
        if tag.lower() == "meta":
            key = (attributes.get("property") or attributes.get("name") or "").lower()
            content = attributes.get("content")
            if key and content:
                self.meta[key] = content
            return

        if tag.lower() == "script" and (
            attributes.get("type") or ""
        ).lower() == "application/json":
            self._json_script_depth += 1
            if self._json_script_depth == 1:
                self._script_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "script" or not self._json_script_depth:
            return
        self._json_script_depth -= 1
        if not self._json_script_depth:
            text = "".join(self._script_parts).strip()
            if text:
                self.json_scripts.append(text)
            self._script_parts = []

    def handle_data(self, data: str) -> None:
        if self._json_script_depth:
            self._script_parts.append(data)


def normalize_proxy(proxy: str | None) -> str | None:
    if proxy is None:
        return None
    proxy = proxy.strip()
    if not proxy:
        return None
    if "://" not in proxy:
        proxy = f"http://{proxy}"
    parsed = urlparse(proxy)
    if parsed.scheme not in {"http", "https", "socks4", "socks5", "socks5h"}:
        raise InstagramURLInvalid(f"Unsupported proxy scheme: {parsed.scheme}")
    if not parsed.hostname or not parsed.port:
        raise InstagramURLInvalid(f"Invalid proxy: {proxy}")
    return proxy


def extract_shortcode(url: str) -> str:
    value = url.strip()
    if not value:
        raise InstagramURLInvalid("Instagram URL is empty")
    if "://" not in value:
        value = f"https://{value.lstrip('/')}"

    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if host != "instagram.com" and not host.endswith(".instagram.com"):
        raise InstagramURLInvalid(f"Not an Instagram URL: {url}")

    match = _SHORTCODE_PATH_RE.match(parsed.path)
    if not match:
        raise InstagramURLInvalid(
            "Expected an Instagram /reel/, /reels/, /p/, or /tv/ URL"
        )
    return match.group(1)


def shortcode_to_media_id(shortcode: str) -> str:
    value = 0
    for character in shortcode:
        try:
            digit = SHORTCODE_ALPHABET.index(character)
        except ValueError as exc:
            raise InstagramURLInvalid(f"Invalid shortcode: {shortcode}") from exc
        value = value * 64 + digit
    return str(value)


def _walk_json(root: Any) -> Iterator[Any]:
    stack = [root]
    while stack:
        value = stack.pop()
        yield value
        if isinstance(value, dict):
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)


def _parse_html_payloads(html_text: str) -> tuple[list[Any], dict[str, str]]:
    parser = _InstagramHTMLParser()
    parser.feed(html_text)
    parser.close()

    payloads: list[Any] = []
    for script in parser.json_scripts:
        try:
            payload = json.loads(script)
            if isinstance(payload, str) and payload[:1] in {"{", "["}:
                payload = json.loads(payload)
            payloads.append(payload)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    return payloads, parser.meta


def _find_media(payloads: list[Any], shortcode: str) -> dict[str, Any] | None:
    candidates: list[tuple[int, dict[str, Any]]] = []
    for payload in payloads:
        for value in _walk_json(payload):
            if not isinstance(value, dict):
                continue
            code = value.get("code") or value.get("shortcode")
            if code != shortcode:
                continue

            score = 0
            if isinstance(value.get("video_versions"), list):
                score += 100
            if isinstance(value.get("carousel_media"), list):
                score += 80
            if isinstance(value.get("image_versions2"), dict):
                score += 40
            if value.get("video_url"):
                score += 100
            if score:
                candidates.append((score, value))

    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def _extract_lsd(payloads: list[Any], html_text: str) -> str | None:
    for payload in payloads:
        for value in _walk_json(payload):
            if not isinstance(value, list) or not value or value[0] != "LSD":
                continue
            for item in value[1:]:
                if isinstance(item, dict) and isinstance(item.get("token"), str):
                    return item["token"]

    match = _LSD_RE.search(html_text)
    return match.group(1) if match else None


def _is_http_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _append_unique(target: list[str], value: Any) -> None:
    if _is_http_url(value) and value not in target:
        target.append(value)


def _best_image_url(media: dict[str, Any]) -> str | None:
    image_versions = media.get("image_versions2")
    if isinstance(image_versions, dict):
        candidates = image_versions.get("candidates")
        if isinstance(candidates, list):
            for candidate in candidates:
                if isinstance(candidate, dict) and _is_http_url(candidate.get("url")):
                    return candidate["url"]
    if _is_http_url(media.get("display_url")):
        return media["display_url"]
    return None


def _media_to_result(
    media: dict[str, Any], shortcode: str, source: str
) -> MediaResult:
    video_urls: list[str] = []
    image_urls: list[str] = []

    items: list[dict[str, Any]] = [media]
    carousel = media.get("carousel_media")
    if isinstance(carousel, list):
        items.extend(item for item in carousel if isinstance(item, dict))

    for item in items:
        versions = item.get("video_versions")
        if isinstance(versions, list):
            for version in versions:
                if isinstance(version, dict):
                    _append_unique(video_urls, version.get("url"))
        _append_unique(video_urls, item.get("video_url"))

        image_url = _best_image_url(item)
        if image_url:
            _append_unique(image_urls, image_url)

    user = media.get("user")
    username = user.get("username") if isinstance(user, dict) else None
    caption_data = media.get("caption")
    caption = caption_data.get("text") if isinstance(caption_data, dict) else None
    media_id = media.get("pk") or media.get("id")
    dash_manifest = media.get("video_dash_manifest") or media.get("dash_manifest")

    return MediaResult(
        shortcode=shortcode,
        media_id=str(media_id) if media_id is not None else None,
        video_urls=video_urls,
        image_urls=image_urls,
        dash_manifest=dash_manifest if isinstance(dash_manifest, str) else None,
        username=username if isinstance(username, str) else None,
        caption=caption if isinstance(caption, str) else None,
        source=source,
    )


class ins:
    """Instagram public post extractor compatible with the old `ins(...)._start()` API."""

    def __init__(
        self,
        url: str,
        proxy: str | None = DEFAULT_PROXY,
        timeout: float = 30,
    ) -> None:
        self.original_url = url
        self.shortcode = extract_shortcode(url)
        self.url = f"https://www.instagram.com/reel/{self.shortcode}/"
        self.media_id = shortcode_to_media_id(self.shortcode)
        self.proxy = normalize_proxy(proxy)
        self.timeout = timeout
        self.impersonate = "chrome124"
        self.proxies = (
            {"http": self.proxy, "https": self.proxy} if self.proxy else None
        )
        self.s = requests.Session(proxies=self.proxies)
        self.video_url: str | None = None
        self.video_urls: list[str] = []
        self.photo_url: list[str] = []
        self.result: MediaResult | None = None

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
            raise InstagramRequestError(f"Request failed for {url}: {exc}") from exc

        if raise_for_status and not 200 <= response.status_code < 300:
            raise InstagramRequestError(
                f"Instagram returned HTTP {response.status_code} for {url}"
            )
        return response

    def _page_headers(self) -> dict[str, str]:
        return {
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "User-Agent": USER_AGENT,
        }

    def _fetch_page(self) -> tuple[str, list[Any], dict[str, str]]:
        response = self._request("GET", self.url, headers=self._page_headers())
        content_type = response.headers.get("content-type", "").lower()
        if "text/html" not in content_type:
            raise InstagramRequestError(
                f"Unexpected Instagram content type: {content_type or 'unknown'}"
            )
        payloads, meta = _parse_html_payloads(response.text)
        return response.text, payloads, meta

    def _csrf_token(self) -> str | None:
        try:
            token = self.s.cookies.get("csrftoken")
            return token if isinstance(token, str) else None
        except Exception:
            return None

    def _bootstrap_tokens(
        self, page_html: str, page_payloads: list[Any]
    ) -> tuple[str, str]:
        lsd = _extract_lsd(page_payloads, page_html)
        csrf = self._csrf_token()
        if lsd and csrf:
            return lsd, csrf

        response = self._request(
            "GET", "https://www.instagram.com/", headers=self._page_headers()
        )
        payloads, _ = _parse_html_payloads(response.text)
        lsd = lsd or _extract_lsd(payloads, response.text)
        csrf = csrf or self._csrf_token()
        if not lsd or not csrf:
            raise InstagramRequestError(
                "Instagram did not return the required LSD/CSRF bootstrap tokens"
            )
        return lsd, csrf

    def _graphql_headers(self, lsd: str, csrf: str) -> dict[str, str]:
        return {
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://www.instagram.com",
            "Referer": self.url,
            "User-Agent": USER_AGENT,
            "X-ASBD-ID": "359341",
            "X-CSRFToken": csrf,
            "X-FB-Friendly-Name": POST_ROOT_FRIENDLY_NAME,
            "X-FB-LSD": lsd,
            "X-IG-App-ID": INSTAGRAM_APP_ID,
            "X-IG-WWW-Claim": "0",
            "X-Requested-With": "XMLHttpRequest",
        }

    def _fetch_graphql(
        self, page_html: str, page_payloads: list[Any]
    ) -> dict[str, Any]:
        lsd, csrf = self._bootstrap_tokens(page_html, page_payloads)
        common_headers = {
            "Accept": "*/*",
            "Origin": "https://www.instagram.com",
            "User-Agent": USER_AGENT,
            "X-ASBD-ID": "359341",
            "X-IG-App-ID": INSTAGRAM_APP_ID,
            "X-IG-WWW-Claim": "0",
        }

        # Instagram currently performs this logged-out content gate request before GraphQL.
        self._request(
            "GET",
            "https://i.instagram.com/api/v1/web/get_ruling_for_content/",
            raise_for_status=False,
            headers=common_headers,
            params={"content_type": "MEDIA", "target_id": self.media_id},
        )

        form = {
            "lsd": lsd,
            "fb_api_caller_class": "RelayModern",
            "fb_api_req_friendly_name": POST_ROOT_FRIENDLY_NAME,
            "server_timestamps": "true",
            "variables": json.dumps(
                {"media_id": self.media_id}, separators=(",", ":")
            ),
            "doc_id": POST_ROOT_DOC_ID,
        }
        response = self._request(
            "POST",
            "https://www.instagram.com/api/graphql",
            headers=self._graphql_headers(lsd, csrf),
            data=form,
        )

        text = response.text.lstrip()
        if text.startswith("for (;;);"):
            text = text[len("for (;;);") :]
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise InstagramRequestError("Instagram GraphQL returned invalid JSON") from exc

        if isinstance(payload, dict) and payload.get("errors"):
            error = payload["errors"][0]
            message = error.get("message") if isinstance(error, dict) else str(error)
            raise InstagramRequestError(f"Instagram GraphQL error: {message}")
        if not isinstance(payload, dict):
            raise InstagramRequestError("Instagram GraphQL returned an invalid payload")
        return payload

    def extract(self) -> MediaResult:
        page_html, page_payloads, meta = self._fetch_page()
        media = _find_media(page_payloads, self.shortcode)
        source = "page"

        if media is None:
            graphql_payload = self._fetch_graphql(page_html, page_payloads)
            media = _find_media([graphql_payload], self.shortcode)
            source = "graphql"

        if media is not None:
            result = _media_to_result(media, self.shortcode, source)
        else:
            og_video = meta.get("og:video:secure_url") or meta.get("og:video")
            if not _is_http_url(og_video):
                raise InstagramMediaNotFound(
                    f"No public media data found for shortcode {self.shortcode}"
                )
            result = MediaResult(
                shortcode=self.shortcode,
                media_id=self.media_id,
                video_urls=[og_video],
                image_urls=[],
                dash_manifest=None,
                username=None,
                caption=None,
                source="og:video",
            )

        if not result.video_urls and not result.image_urls:
            raise InstagramMediaNotFound(
                f"Instagram returned metadata but no media URL for {self.shortcode}"
            )

        self.result = result
        self.video_urls = result.video_urls
        self.video_url = result.video_urls[0] if result.video_urls else None
        self.photo_url = result.image_urls
        return result

    def _start(self) -> str | list[str]:
        result = self.extract()
        if result.video_urls:
            return result.video_urls[0] if len(result.video_urls) == 1 else result.video_urls
        return result.image_urls[0] if len(result.image_urls) == 1 else result.image_urls


def extract_instagram(
    url: str,
    proxy: str | None = DEFAULT_PROXY,
    timeout: float = 30,
) -> MediaResult:
    return ins(url, proxy=proxy, timeout=timeout).extract()


__all__ = [
    "DEFAULT_PROXY",
    "InstagramError",
    "InstagramMediaNotFound",
    "InstagramRequestError",
    "InstagramURLInvalid",
    "MediaResult",
    "extract_instagram",
    "extract_shortcode",
    "ins",
    "normalize_proxy",
    "shortcode_to_media_id",
]
