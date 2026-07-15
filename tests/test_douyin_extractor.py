import json
import unittest

import douyin_extractor as douyin
from media_registry import is_allowed_media_url
from media_resolver import InvalidMediaURL, detect_platform


SHARE_TEXT = (
    "3.35 O@K.ws AtE:/ 05/31 :3pm 小伙设计抓获害人的眼镜蛇 "
    "# 真实户外 # 神奇动物 # 青年创作者扶持计划  "
    "https://v.douyin.com/XEPBeqmlse4/ "
    "复制此链接，打开Dou音搜索，直接观看视频！"
)
FINAL_URL = (
    "https://www.douyin.com/video/7630749453923047281"
    "?previous_page=web_code_link"
)


class FakeResponse:
    def __init__(self, url: str, text: str = "", status_code: int = 200) -> None:
        self.url = url
        self.text = text
        self.status_code = status_code


class FakeCookies:
    def get_dict(self) -> dict[str, str]:
        return {}


class FakeSession:
    def __init__(self, *responses: FakeResponse) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict]] = []
        self.cookies = FakeCookies()

    def _response(self) -> FakeResponse:
        if not self.responses:
            raise AssertionError("No fake response remains")
        return self.responses.pop(0)

    def get(self, url: str, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self._response()

    def request(self, method: str, url: str, **kwargs):
        self.calls.append((method, url, kwargs))
        return self._response()


def router_html(item: dict) -> str:
    payload = {
        "loaderData": {
            "video_layout": {},
            "video_(id)/page": {
                "videoInfoRes": {
                    "status_code": 0,
                    "filter_list": [],
                    "item_list": [item],
                }
            },
        }
    }
    return (
        "<script>window._ROUTER_DATA = "
        + json.dumps(payload)
        + ";</script>"
    )


class DouyinExtractorTests(unittest.TestCase):
    def test_extracts_url_from_complete_share_text(self) -> None:
        self.assertEqual(
            douyin.extract_douyin_url(SHARE_TEXT),
            "https://v.douyin.com/XEPBeqmlse4/",
        )
        self.assertEqual(detect_platform(SHARE_TEXT), "douyin")

    def test_rejects_lookalike_domain(self) -> None:
        with self.assertRaises(douyin.DouyinURLInvalid):
            douyin.extract_douyin_url(
                "https://v.douyin.com.evil.example/video/7630749453923047281"
            )
        with self.assertRaises(InvalidMediaURL):
            detect_platform(
                "https://www.douyin.com.evil.example/video/7630749453923047281"
            )

    def test_redirect_result_uses_response_url(self) -> None:
        final_response = FakeResponse(FINAL_URL, status_code=444)
        session = FakeSession(final_response)
        share_url, response, real_url = douyin.resolve_douyin_url(
            SHARE_TEXT, session=session
        )
        self.assertEqual(share_url, "https://v.douyin.com/XEPBeqmlse4/")
        self.assertIs(response, final_response)
        self.assertEqual(real_url, response.url)
        self.assertTrue(session.calls[0][2]["allow_redirects"])

    def test_extract_aweme_id_supports_share_and_canonical_paths(self) -> None:
        self.assertEqual(
            douyin.extract_aweme_id(
                "https://www.iesdouyin.com/share/video/7630749453923047281/"
            ),
            "7630749453923047281",
        )
        self.assertEqual(
            douyin.extract_aweme_id(
                "https://www.douyin.com/video/7630749453923047281"
            ),
            "7630749453923047281",
        )

    def test_extractor_returns_structured_video_result(self) -> None:
        item = {
            "aweme_id": "7630749453923047281",
            "desc": "caption",
            "author": {
                "unique_id": "author-id",
                "nickname": "Author",
                "avatar_thumb": {
                    "url_list": ["https://p3.douyinpic.com/avatar.jpeg"]
                },
            },
            "statistics": {"digg_count": 12},
            "video": {
                "width": 1080,
                "height": 1920,
                "duration": 12500,
                "play_addr": {
                    "url_list": [
                        "https://aweme.snssdk.com/aweme/v1/playwm/?video_id=abc"
                    ]
                },
                "cover": {"url_list": ["https://p3.douyinpic.com/cover.webp"]},
            },
        }
        extractor = douyin.DouyinExtractor(SHARE_TEXT, proxy=None)
        extractor.s = FakeSession(
            FakeResponse(FINAL_URL),
            FakeResponse(
                "https://www.douyin.com/share/video/7630749453923047281/",
                router_html(item),
            ),
        )
        result = extractor.extract()

        self.assertEqual(result.media_id, "7630749453923047281")
        self.assertEqual(result.caption, "caption")
        self.assertEqual(result.author_name, "Author")
        self.assertEqual(result.media[0].duration, 12)
        self.assertIn("/aweme/v1/play/", result.primary_url)
        self.assertNotIn("/playwm/", result.primary_url)
        self.assertEqual(result.video_urls, [result.primary_url])
        self.assertEqual(result.image_urls, [])
        self.assertEqual(result.to_dict()["source"], "router-data")
        self.assertEqual(extractor.realurl, extractor.response.url)

    def test_image_post_preserves_order(self) -> None:
        item = {
            "images": [
                {
                    "url_list": ["https://p3.douyinpic.com/1.webp"],
                    "width": 720,
                    "height": 1280,
                },
                {
                    "url_list": ["https://p3.douyinpic.com/2.webp"],
                    "width": 720,
                    "height": 1280,
                },
            ]
        }
        result = douyin._item_to_result(
            item,
            aweme_id="123",
            share_url="https://v.douyin.com/abc/",
            real_url="https://www.douyin.com/video/123",
        )
        self.assertEqual(
            result.image_urls,
            [
                "https://p3.douyinpic.com/1.webp",
                "https://p3.douyinpic.com/2.webp",
            ],
        )
        self.assertEqual([item.kind for item in result.media], ["image", "image"])

    def test_douyin_media_hosts_are_allowed(self) -> None:
        for url in (
            "https://www.douyin.com/aweme/v1/play/?video_id=1",
            "https://p3.douyinpic.com/cover.webp",
            "https://v26-chf.douyinvod.com/video.mp4",
            "https://v3-dy-o.zjcdn.com/video.mp4",
            "https://aweme.snssdk.com/aweme/v1/play/?video_id=1",
        ):
            self.assertTrue(is_allowed_media_url(url), url)
        self.assertFalse(is_allowed_media_url("https://douyin.com.evil.example/a"))


if __name__ == "__main__":
    unittest.main()
