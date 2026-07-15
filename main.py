from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict

import instagram_extractor as instagram
from media_resolver import ResolverError, resolve_media


DEFAULT_PROXY = os.getenv(
    "MEDIA_PROXY", os.getenv("INSTAGRAM_PROXY", instagram.DEFAULT_PROXY)
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract public Instagram and TikTok media URLs."
    )
    parser.add_argument("url", nargs="?", help="Instagram or TikTok media URL")
    parser.add_argument(
        "--proxy",
        default=DEFAULT_PROXY,
        help=f"HTTP/SOCKS proxy (default: {DEFAULT_PROXY})",
    )
    parser.add_argument("--no-proxy", action="store_true", help="Connect directly")
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--json", action="store_true", help="Print metadata as JSON")
    parser.add_argument("--all", action="store_true", help="Print every media URL")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    url = args.url or input("Media URL: ").strip()
    proxy = None if args.no_proxy else args.proxy
    try:
        result = resolve_media(url, proxy=proxy, timeout=args.timeout)
    except ResolverError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        payload = result.public_metadata()
        payload.update(
            {
                "thumbnail_url": result.thumbnail_url,
                "author_avatar": result.author_avatar,
                "media": [asdict(item) for item in result.media],
            }
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    media = result.media if args.all else result.media[:1]
    for item in media:
        print(item.url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
