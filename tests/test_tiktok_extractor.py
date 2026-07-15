import json
import unittest

import tiktok_extractor as tiktok
from media_registry import MediaRegistry, is_allowed_media_url
from media_resolver import InvalidMediaURL, RequestContext, detect_platform


class TikTokExtractorTests(unittest.TestCase):
    def test_universal_payload_prefers_play_addr(self) -> None:
        item = {
            "id": "7607438661319281941",
            "desc": "caption",
            "author": {"uniqueId": "futurebuilt2", "nickname": "Avenism"},
            "video": {
                "width": 576,
                "height": 768,
                "duration": 13,
                "ratio": "540p",
                "playAddr": "https://v16-webapp-prime.us.tiktok.com/video/clean.mp4",
                "downloadAddr": "https://v16-webapp-prime.us.tiktok.com/video/watermark.mp4",
                "cover": "https://p16-common-sign.tiktokcdn-us.com/cover.jpg",
            },
        }
        payload = {
            "__DEFAULT_SCOPE__": {
                "webapp.video-detail": {"itemInfo": {"itemStruct": item}}
            }
        }
        html = (
            '<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">'
            + json.dumps(payload)
            + "</script>"
        )
        scripts = tiktok._parse_json_scripts(html)
        parsed, source = tiktok._find_item(scripts, "7607438661319281941")
        result = tiktok._item_to_result(parsed, source)
        self.assertEqual(result.media[0].url, item["video"]["playAddr"])
        self.assertNotIn("watermark", result.media[0].url)
        self.assertEqual(result.media[0].width, 576)

    def test_image_post_preserves_order(self) -> None:
        item = {
            "id": "123",
            "imagePost": {
                "images": [
                    {"imageURL": {"urlList": ["https://p16-common-sign.tiktokcdn-us.com/1.jpg"]}},
                    {"imageURL": {"urlList": ["https://p16-common-sign.tiktokcdn-us.com/2.jpg"]}},
                ]
            },
        }
        result = tiktok._item_to_result(item, "fixture")
        self.assertEqual([media.kind for media in result.media], ["image", "image"])
        self.assertTrue(result.media[0].url.endswith("/1.jpg"))

    def test_h264_bitrate_fallback(self) -> None:
        item = {
            "id": "456",
            "video": {
                "bitrateInfo": [
                    {
                        "CodecType": "h265_hvc1",
                        "Bitrate": 900000,
                        "PlayAddr": {"UrlList": ["https://v16-webapp-prime.us.tiktok.com/h265.mp4"]},
                    },
                    {
                        "CodecType": "h264",
                        "Bitrate": 700000,
                        "PlayAddr": {"UrlList": ["https://v16-webapp-prime.us.tiktok.com/h264.mp4"]},
                    },
                ]
            },
        }
        result = tiktok._item_to_result(item, "fixture")
        self.assertTrue(result.media[0].url.endswith("/h264.mp4"))

    def test_platform_host_validation(self) -> None:
        self.assertEqual(detect_platform("https://www.tiktok.com/@a/video/1"), "tiktok")
        self.assertEqual(detect_platform("https://www.instagram.com/reel/abc/"), "instagram")
        with self.assertRaises(InvalidMediaURL):
            detect_platform("https://www.tiktok.com.evil.example/video/1")

    def test_media_registry_rejects_arbitrary_hosts(self) -> None:
        self.assertTrue(
            is_allowed_media_url("https://v16-webapp-prime.us.tiktok.com/video/a.mp4")
        )
        self.assertFalse(is_allowed_media_url("https://127.0.0.1/private"))
        registry = MediaRegistry()
        with self.assertRaises(ValueError):
            registry.register(
                url="https://example.com/file.mp4",
                kind="video",
                platform="tiktok",
                filename="file.mp4",
                context=RequestContext("https://www.tiktok.com/", "ua"),
            )


if __name__ == "__main__":
    unittest.main()
