"""毎晩のまとめ通知のテスト"""
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from bot import daily
from bot.engine import new_state

JST = timezone(timedelta(hours=9))
ms = lambda *a: int(datetime(*a, tzinfo=JST).timestamp() * 1000)


class DailyTest(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.p1 = mock.patch.object(daily.notify, "send", side_effect=lambda t, b, **k: self.sent.append((t, b)))
        self.p2 = mock.patch.object(daily, "_mood", return_value={"value": 13, "label": "極度の恐怖"})
        self.p1.start(); self.p2.start()

    def tearDown(self):
        self.p1.stop(); self.p2.stop()

    def test_once_per_night_after_1am(self):
        st = new_state(100_000, "paper")
        sm = {"equity": 100_000, "month_ret": 0.0, "halted": False}
        daily.run(st, sm, ms(2026, 11, 26, 15, 0))      # 入れた日：送らずに印だけ
        self.assertEqual(self.sent, [])
        daily.run(st, sm, ms(2026, 11, 26, 23, 0))      # 夜11時：まだ
        self.assertEqual(self.sent, [])
        daily.run(st, sm, ms(2026, 11, 27, 1, 40))      # 深夜1時40分：送る
        daily.run(st, sm, ms(2026, 11, 27, 6, 0))       # 同じ夜にもう一度は送らない
        self.assertEqual(len(self.sent), 1)
        t, b = self.sent[0]
        self.assertIn("11/26", t)
        self.assertIn("極度の恐怖", b)
        self.assertIn("売買なし", b)
        daily.run(st, sm, ms(2026, 11, 28, 3, 0))       # 次の夜
        self.assertEqual(len(self.sent), 2)


if __name__ == "__main__":
    unittest.main()
