import json
import unittest

import instagram_extractor as instagram


class InstagramExtractorTests(unittest.TestCase):
    def test_extract_shortcode_normalizes_reels_url(self) -> None:
        url = "https://www.instagram.com/reels/DYmOO59iKch/extra-text?utm_source=x"
        self.assertEqual(instagram.extract_shortcode(url), "DYmOO59iKch")

    def test_shortcode_to_media_id(self) -> None:
        self.assertEqual(
            instagram.shortcode_to_media_id("DYmOO59iKch"), "3901868724122593057"
        )

    def test_proxy_without_scheme_defaults_to_http(self) -> None:
        self.assertEqual(
            instagram.normalize_proxy("127.0.0.1:2080"), "http://127.0.0.1:2080"
        )

    def test_page_parser_selects_exact_shortcode(self) -> None:
        payload = {
            "feed": [
                {
                    "code": "Recommendation",
                    "video_versions": [{"url": "https://cdn.example/wrong.mp4"}],
                },
                {
                    "code": "DYmOO59iKch",
                    "pk": "3901868724122593057",
                    "user": {"username": "coco.career"},
                    "video_versions": [
                        {"type": 101, "url": "https://cdn.example/video.mp4?a=1&b=2"},
                        {"type": 102, "url": "https://cdn.example/video.mp4?a=1&b=2"},
                    ],
                },
            ]
        }
        html = (
            '<html><script type="application/json" data-sjs>'
            + json.dumps(payload)
            + "</script></html>"
        )
        payloads, _ = instagram._parse_html_payloads(html)
        media = instagram._find_media(payloads, "DYmOO59iKch")
        self.assertIsNotNone(media)
        result = instagram._media_to_result(media, "DYmOO59iKch", "page")
        self.assertEqual(result.username, "coco.career")
        self.assertEqual(
            result.video_urls, ["https://cdn.example/video.mp4?a=1&b=2"]
        )


if __name__ == "__main__":
    unittest.main()
