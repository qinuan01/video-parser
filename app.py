from __future__ import annotations

import base64
import os
import threading
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse

from curl_cffi import requests as curl_requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import instagram_extractor as instagram
from media_registry import MediaRegistry, is_allowed_media_url
from media_resolver import (
    InvalidMediaURL,
    MediaUnavailable,
    ResolvedResult,
    UpstreamMediaError,
    resolve_media,
)


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DEFAULT_OUTBOUND_PROXY = os.getenv(
    "MEDIA_PROXY", os.getenv("INSTAGRAM_PROXY", instagram.DEFAULT_PROXY)
)
MEDIA_TOKEN_TTL = 15 * 60

STANDARD_B64_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
CUSTOM_B64_ALPHABET = "NOPQRSTUVWXYZABCDEFGHIJKLMnopqrstuvwxyzabcdefghijklm9876543210-_"
CUSTOM_ENCODE_MAP = str.maketrans(STANDARD_B64_ALPHABET, CUSTOM_B64_ALPHABET)
CUSTOM_DECODE_MAP = str.maketrans(CUSTOM_B64_ALPHABET, STANDARD_B64_ALPHABET)

app = FastAPI(
    title="Media Resolver",
    docs_url=None,
    redoc_url=None,
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

registry = MediaRegistry(ttl_seconds=MEDIA_TOKEN_TTL)
resolve_slots = threading.BoundedSemaphore(value=4)


class EncRequest(BaseModel):
    a: str


class ParseRequest(BaseModel):
    url: str


def custom_b64encode(data: str) -> str:
    encoded = base64.b64encode(data.encode()).decode()
    return encoded.translate(CUSTOM_ENCODE_MAP)


def custom_b64decode(data: str) -> str:
    decoded = data.translate(CUSTOM_DECODE_MAP)
    return base64.b64decode(decoded, validate=True).decode()


def _extension(kind: str, format_name: str | None, url: str) -> str:
    if kind == "video":
        return ".mp4"
    path_suffix = Path(urlparse(url).path).suffix.lower()
    if path_suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return path_suffix
    if format_name and format_name.lower() in {"jpg", "jpeg", "png", "webp"}:
        return f".{format_name.lower()}"
    return ".jpg"


def _register_asset(
    result: ResolvedResult,
    *,
    url: str | None,
    kind: str,
    filename: str,
) -> dict[str, str] | None:
    if not url:
        return None
    source = registry.register(
        url=url,
        kind=kind,
        platform=result.platform,
        filename=filename,
        context=result.context,
    )
    path = f"/api/media/{source.token}"
    return {
        "preview_url": path,
        "download_url": f"{path}?download=1",
        "direct_url": url,
    }


def serialize_result(result: ResolvedResult) -> dict[str, Any]:
    payload = result.public_metadata()
    thumb = _register_asset(
        result,
        url=result.thumbnail_url,
        kind="image",
        filename=f"{result.platform}-{result.media_id}-cover.jpg",
    )
    avatar = _register_asset(
        result,
        url=result.author_avatar,
        kind="image",
        filename=f"{result.platform}-{result.media_id}-avatar.jpg",
    )

    media_payload: list[dict[str, Any]] = []
    for index, item in enumerate(result.media, start=1):
        extension = _extension(item.kind, item.format, item.url)
        asset = _register_asset(
            result,
            url=item.url,
            kind=item.kind,
            filename=f"{result.platform}-{result.media_id}-{index}{extension}",
        )
        if not asset:
            continue
        media_payload.append(
            {
                "index": index,
                "kind": item.kind,
                "width": item.width,
                "height": item.height,
                "duration": item.duration,
                "format": item.format,
                "quality": item.quality,
                **asset,
            }
        )

    if not media_payload:
        raise MediaUnavailable("没有可注册的媒体资源")
    payload.update(
        {
            "thumbnail_url": thumb["preview_url"] if thumb else None,
            "author_avatar_url": avatar["preview_url"] if avatar else None,
            "media": media_payload,
            "expires_in": MEDIA_TOKEN_TTL,
        }
    )
    return payload


def _resolve(url: str) -> ResolvedResult:
    with resolve_slots:
        return resolve_media(url, proxy=DEFAULT_OUTBOUND_PROXY, timeout=30)


def run_task(url: str) -> list[str]:
    result = _resolve(url)
    return [item.url for item in result.media]


def _raise_api_error(exc: Exception) -> None:
    if isinstance(exc, InvalidMediaURL):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if isinstance(exc, MediaUnavailable):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, UpstreamMediaError):
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/parse")
def parse_media(request: ParseRequest) -> dict[str, Any]:
    try:
        return serialize_result(_resolve(request.url))
    except Exception as exc:
        _raise_api_error(exc)
        raise


@app.post("/start-task")
def start_task(request: EncRequest) -> JSONResponse:
    """Legacy endpoint: keep returning the old list of direct media URLs."""
    try:
        url = custom_b64decode(request.a)
        return JSONResponse(run_task(url))
    except Exception as exc:
        if isinstance(exc, InvalidMediaURL):
            return JSONResponse({"error": str(exc)}, status_code=400)
        if isinstance(exc, MediaUnavailable):
            return JSONResponse({"error": str(exc)}, status_code=404)
        if isinstance(exc, UpstreamMediaError):
            return JSONResponse({"error": str(exc)}, status_code=502)
        return JSONResponse({"error": str(exc)}, status_code=500)


def _proxy_headers(source: Any, request: Request) -> dict[str, str]:
    headers = {
        "Accept": "*/*",
        "Accept-Encoding": "identity",
        "Referer": source.context.referer,
        "User-Agent": source.context.user_agent,
    }
    range_header = request.headers.get("range")
    if range_header:
        headers["Range"] = range_header
    return headers


def _forward_headers(upstream: Any, source: Any, download: bool) -> dict[str, str]:
    headers: dict[str, str] = {"Cache-Control": "private, no-store"}
    for name in (
        "content-length",
        "content-range",
        "accept-ranges",
        "etag",
        "last-modified",
    ):
        value = upstream.headers.get(name)
        if value:
            headers[name.title()] = value
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{source.filename}"'
    return headers


def _close_upstream(upstream: Any, session: Any) -> None:
    try:
        upstream.close()
    finally:
        session.close()


def _stream_upstream(upstream: Any, session: Any) -> Iterator[bytes]:
    try:
        for chunk in upstream.iter_content(chunk_size=64 * 1024):
            if chunk:
                yield chunk
    finally:
        _close_upstream(upstream, session)


@app.api_route("/api/media/{token}", methods=["GET", "HEAD"])
def proxy_media(token: str, request: Request, download: bool = False) -> Response:
    try:
        source = registry.get(token)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="媒体地址已过期，请重新解析") from exc

    proxies = (
        {"http": DEFAULT_OUTBOUND_PROXY, "https": DEFAULT_OUTBOUND_PROXY}
        if DEFAULT_OUTBOUND_PROXY
        else None
    )
    session = curl_requests.Session(
        proxies=proxies,
        cookies=source.context.cookies,
    )
    method = "HEAD" if request.method == "HEAD" else "GET"
    try:
        upstream = session.request(
            method,
            source.url,
            headers=_proxy_headers(source, request),
            impersonate="chrome124",
            timeout=30,
            allow_redirects=True,
            stream=method == "GET",
        )
    except Exception as exc:
        session.close()
        raise HTTPException(status_code=502, detail=f"媒体读取失败: {exc}") from exc

    final_url = str(upstream.url)
    if not is_allowed_media_url(final_url):
        _close_upstream(upstream, session)
        raise HTTPException(status_code=502, detail="媒体重定向地址无效")
    if upstream.status_code not in {200, 206}:
        status = upstream.status_code
        _close_upstream(upstream, session)
        raise HTTPException(status_code=502, detail=f"媒体源返回 HTTP {status}")

    content_type = upstream.headers.get("content-type") or (
        "video/mp4" if source.kind == "video" else "image/jpeg"
    )
    response_headers = _forward_headers(upstream, source, download)
    if method == "HEAD":
        _close_upstream(upstream, session)
        return Response(
            status_code=upstream.status_code,
            media_type=content_type,
            headers=response_headers,
        )
    return StreamingResponse(
        _stream_upstream(upstream, session),
        status_code=upstream.status_code,
        media_type=content_type,
        headers=response_headers,
    )


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "proxy": DEFAULT_OUTBOUND_PROXY or "direct"}


@app.get("/", response_class=FileResponse)
def serve_index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=21359)
