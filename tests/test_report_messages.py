import datetime as dt
import unittest
from unittest.mock import patch

import sync_articles


class ReportMessageTests(unittest.TestCase):
    def make_groups(self, count):
        return [
            (
                f"Company {i}",
                [{
                    "title": f"Unique Article Title {i}",
                    "link": f"https://example.com/article-{i}",
                }],
            )
            for i in range(1, count + 1)
        ]

    def test_short_report_is_single_message(self):
        with patch.object(sync_articles, "_beijing_now", return_value=dt.datetime(2026, 10, 18)):
            messages = sync_articles.build_report_messages(self.make_groups(2), "evening")

        self.assertEqual(len(messages), 1)
        self.assertIn("Unique Article Title 1", messages[0])
        self.assertIn("Unique Article Title 2", messages[0])
        self.assertLessEqual(sync_articles._html_text_length(messages[0]), sync_articles.CAPTION_LIMIT)

    def test_long_report_splits_without_dropping_articles(self):
        groups = self.make_groups(35)
        with patch.object(sync_articles, "_beijing_now", return_value=dt.datetime(2026, 10, 18)):
            messages = sync_articles.build_report_messages(groups, "evening")

        self.assertGreater(len(messages), 1)
        combined = "\n".join(messages)
        for i in range(1, 36):
            self.assertIn(f"Unique Article Title {i}", combined)
        self.assertIn("21.", combined)
        self.assertIn("35.", combined)
        self.assertLessEqual(sync_articles._html_text_length(messages[0]), sync_articles.CAPTION_LIMIT)
        for message in messages[1:]:
            self.assertLessEqual(sync_articles._html_text_length(message), sync_articles.MAX_MESSAGE_LENGTH)

    def test_long_title_is_shortened_but_link_is_preserved(self):
        groups = [("Company", [{
            "title": "Very long title " * 200,
            "link": "https://example.com/long-title",
        }])]
        with patch.object(sync_articles, "_beijing_now", return_value=dt.datetime(2026, 10, 18)):
            messages = sync_articles.build_report_messages(groups, "morning")

        self.assertTrue(any("https://example.com/long-title" in message for message in messages))
        self.assertTrue(all(sync_articles._html_text_length(message) <= sync_articles.MAX_MESSAGE_LENGTH for message in messages))


if __name__ == "__main__":
    unittest.main()
