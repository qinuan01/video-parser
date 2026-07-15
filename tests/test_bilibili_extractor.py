import json
import unittest

import bilibili_extractor as bilibili
from media_registry import is_allowed_media_url
from media_resolver import InvalidMediaURL, detect_platform


BVID = "BV1XWTC6cE6H"
VIDEO_URL = "https://cn-test.bilivideo.com/upgcxcode/video.mp4?token=fixture"


class FakeResponse:
    def __init__(self, payload=None, *, url="https://api.bilibili.com/", status_code=200):
        self.text = json.dumps(payload) if payload is not None else ""
        self.url = url
        self.status_code = status_code


class FakeCookies:
    def get_dict(self):
        return {"buvid3": "fixture"}


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.cookies = FakeCookies()

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self.responses:
            raise AssertionError("No fake response remains")
        return self.responses.pop(0)


def api_response(data):
    return FakeResponse({"code": 0, "message": "0", "data": data})


def view_data():
    return {
        "bvid": BVID,
        "title": "Fixture video",
        "pic": "http://i0.hdslb.com/bfs/archive/cover.jpg",
        "owner": {"mid": 123, "name": "Uploader", "face": "https://i1.hdslb.com/face.jpg"},
        "stat": {"view": 100, "like": 20, "reply": 3, "share": 4},
        "pages": [
            {"cid": 39674841098, "page": 1, "dimension": {"width": 1920, "height": 1080}}
        ],
    }


def play_data():
    return {
        "quality": 64,
        "format": "mp4720",
        "timelength": 188283,
        "durl": [{"order": 1, "url": VIDEO_URL, "size": 29541805}],
    }


class BilibiliExtractorTests(unittest.TestCase):
    def test_platform_and_bvid_detection(self):
        url = f"https://www.bilibili.com/video/{BVID}?p=1"
        self.assertEqual(detect_platform(url), "bilibili")
        self.assertEqual(detect_platform("https://b23.tv/fixture"), "bilibili")
        self.assertEqual(bilibili.extract_bvid(url), BVID)
        with self.assertRaises(InvalidMediaURL):
            detect_platform("https://www.bilibili.com.evil.example/video/BV1XWTC6cE6H")

    def test_returns_single_complete_durl_mp4(self):
        extractor = bilibili.BilibiliExtractor(
            f"https://www.bilibili.com/video/{BVID}", proxy=None
        )
        extractor.s = FakeSession(api_response(view_data()), api_response(play_data()))

        result = extractor.extract()

        self.assertEqual(result.media_id, BVID)
        self.assertEqual(result.media[0].url, VIDEO_URL)
        self.assertEqual(result.media[0].format, "mp4")
        self.assertEqual(result.media[0].quality, "720P")
        self.assertEqual((result.media[0].width, result.media[0].height), (1280, 720))
        self.assertEqual(result.media[0].duration, 188)
        self.assertEqual(result.source, "playurl-durl")
        self.assertEqual(extractor.s.calls[1][1]["params"]["fnval"], 0)
        self.assertEqual(extractor.s.calls[1][1]["params"]["qn"], 64)

    def test_resolves_b23_short_link(self):
        final_url = f"https://www.bilibili.com/video/{BVID}?p=1"
        extractor = bilibili.BilibiliExtractor("https://b23.tv/fixture", proxy=None)
        extractor.s = FakeSession(
            FakeResponse(url=final_url),
            api_response(view_data()),
            api_response(play_data()),
        )

        result = extractor.extract()

        self.assertEqual(result.media_id, BVID)
        self.assertEqual(extractor.s.calls[0][0], "https://b23.tv/fixture")

    def test_bilibili_media_hosts_are_allowed(self):
        self.assertTrue(is_allowed_media_url(VIDEO_URL))
        self.assertTrue(is_allowed_media_url("https://i0.hdslb.com/bfs/archive/cover.jpg"))
        self.assertFalse(is_allowed_media_url("https://bilivideo.com.evil.example/video.mp4"))


if __name__ == "__main__":
    unittest.main()
