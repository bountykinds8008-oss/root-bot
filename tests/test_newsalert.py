"""大事なニュースの通知のテスト:  python -m unittest -v"""
import json
import unittest
from unittest import mock

from bot import newsalert


def fake(items):
    m = mock.MagicMock()
    m.read.return_value = json.dumps({"items": items}).encode()
    return m


class NewsAlertTest(unittest.TestCase):
    def test_first_run_marks_read_without_notifying(self):
        st, sent = {}, []
        with mock.patch("urllib.request.urlopen", return_value=fake([{"title": "ビットコイン急騰", "link": "a", "ts": 10, "coins": ["btc_jpy"]}])), \
             mock.patch.object(newsalert.notify, "send", side_effect=lambda *a, **k: sent.append(a)):
            newsalert.run(st, 0)
        self.assertEqual(sent, [])
        self.assertEqual(st["news_seen_ts"], 10)

    def test_only_important_new_and_once(self):
        st, sent = {"news_seen_ts": 10}, []
        items = [{"title": "ビットコイン急騰、最高値", "link": "a", "ts": 20, "coins": ["btc_jpy"]},
                 {"title": "イーサリアムの勉強会", "link": "b", "ts": 21, "coins": ["eth_jpy"]},      # 大事ではない
                 {"title": "取引所でハッキング被害", "link": "c", "ts": 22, "coins": []},           # コインなしでも大事
                 {"title": "XRP上場", "link": "d", "ts": 5, "coins": ["xrp_jpy"]}]                 # 既読より古い
        with mock.patch("urllib.request.urlopen", return_value=fake(items)), \
             mock.patch.object(newsalert.notify, "send", side_effect=lambda *a, **k: sent.append(k.get("click"))):
            newsalert.run(st, 0)
            newsalert.run(st, 0)                                                             # 2回目は送らない
        self.assertEqual(sent, ["a", "c"])
        self.assertEqual(st["news_seen_ts"], 22)


if __name__ == "__main__":
    unittest.main()
