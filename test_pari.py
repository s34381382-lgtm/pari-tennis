#!/usr/bin/env python3
"""Дымовые тесты pari-tennis (только stdlib, без данных).

Запуск: python3 test_pari.py
Проверяют то, что уже молча врало:
- _set_prob не уходит в рекурсию на кривых счетах (9-4, 8-2)
- match_fair не падает на мусоре из ленты
- serve_point_prob взвешивает эйсы по объёму
- holds_for_match применяет поправку только нужной стороне
- edge_min_for разделяет LAB/MAIN
- sex_weight не врёт на юниорах
- parse_te кричит при смене разметки, а не молчит
- cmd_auto принимает список файлов (баг 'нужны --files')
"""
import io
import os
import sys
import unittest
from contextlib import redirect_stderr

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

from pari_lib import (
    _set_prob, match_fair, game_fair, serve_point_prob,
    holds_for_match, hold_for_odds,
)
from pari_patterns import is_women, sex_weight
from pari_profiles import parse_te


class TestSetProbGuards(unittest.TestCase):
    def test_invalid_scores_no_recursion(self):
        # Кривые счета из ленты 15.09 — раньше RecursionError.
        self.assertEqual(_set_prob(9, 4, 1, 0.8, 0.8), 1.0)
        self.assertEqual(_set_prob(4, 9, 1, 0.8, 0.8), 0.0)
        self.assertEqual(_set_prob(8, 2, 1, 0.8, 0.8), 1.0)
        self.assertEqual(_set_prob(7, 3, 2, 0.8, 0.8), 1.0)
        self.assertEqual(_set_prob(3, 7, 2, 0.8, 0.8), 0.0)

    def test_valid_scores_unchanged(self):
        self.assertEqual(_set_prob(6, 4, 1, 0.8, 0.8), 1.0)
        self.assertEqual(_set_prob(4, 6, 1, 0.8, 0.8), 0.0)
        tb = _set_prob(6, 6, 1, 0.8, 0.7)
        self.assertGreater(tb, 0.5)  # лучший на подаче — фаворит тай-брейка
        self.assertLess(tb, 0.95)

    def test_match_fair_survives_garbage(self):
        # match_fair с мусором не должен ронять вызывателя.
        r = match_fair((0, 0), (9, 4), 1, 0.8, 0.75)
        self.assertGreaterEqual(r, 0.0)
        self.assertLessEqual(r, 1.0)


class TestServeCalibration(unittest.TestCase):
    def test_curve_three_buckets(self):
        # Середина слита в одну корзину (шум ±6 п.п. убран).
        self.assertEqual(hold_for_odds(1.2), 0.83)
        self.assertEqual(hold_for_odds(2.0), 0.79)
        self.assertEqual(hold_for_odds(3.9), 0.79)
        self.assertEqual(hold_for_odds(10.0), 0.65)

    def test_ace_volume_weight(self):
        stats = {"эйсы": {"c1": 3, "c2": 0}, "двойные ошибки": {"c1": 0, "c2": 0}}
        p_loud = serve_point_prob(stats, "ATP", "p1", games_served=12)
        p_quiet = serve_point_prob(stats, "ATP", "p1", games_served=2)
        p_base = serve_point_prob(None, "ATP", "p1")
        # Большой объём — сдвиг больше, малый — почти не двигает.
        self.assertGreater(p_loud - p_base, p_quiet - p_base)
        # Кап держит.
        stats_big = {"эйсы": {"c1": 20, "c2": 0}, "двойные ошибки": {"c1": 0, "c2": 0}}
        p_cap = serve_point_prob(stats_big, "ATP", "p1", games_served=20)
        self.assertLessEqual(p_cap - p_base, 0.041)
        # Без games_served — старое поведение (обратная совместимость).
        p_old = serve_point_prob(stats, "ATP", "p1")
        self.assertAlmostEqual(p_old - p_base, 0.024, places=3)

    def test_holds_for_match(self):
        h1, h2 = holds_for_match(0.8, 0.75, "p1")
        self.assertLess(h1, 0.8)  # поправка давит
        self.assertEqual(h2, 0.75)  # чужую сторону не трогает
        h1b, h2b = holds_for_match(0.8, 0.75, None)
        self.assertEqual((h1b, h2b), (0.8, 0.75))


class TestCircuits(unittest.TestCase):
    def test_edge_thresholds(self):
        from pari_signals import edge_min_for
        self.assertEqual(edge_min_for("ATP Challenger"), 0.16)  # LAB
        self.assertEqual(edge_min_for("UTR Pro"), 0.16)
        self.assertEqual(edge_min_for("ATP. Masters"), 0.12)  # MAIN
        self.assertEqual(edge_min_for("WTA. Tour"), 0.12)
        self.assertEqual(edge_min_for("что-то странное"), 0.16)  # неизвестно = LAB

    def test_sex_weight(self):
        self.assertEqual(sex_weight("WTA. Tour"), 1.0)
        self.assertEqual(sex_weight("ATP. Masters"), 0.0)
        self.assertEqual(sex_weight("ITF Juniors"), 0.5)  # было False=мужчина
        self.assertEqual(sex_weight(""), 0.5)
        self.assertEqual(sex_weight(None), 0.5)
        # is_women не тронут (обратная совместимость).
        self.assertTrue(is_women("WTA x"))
        self.assertFalse(is_women("ITF Juniors"))


class TestParserAlarm(unittest.TestCase):
    def test_parse_te_warns_on_break(self):
        html = "<html><body>" + "x" * 6000 + "<h1>Smith J. - profile</h1></body>"
        buf = io.StringIO()
        with redirect_stderr(buf):
            out = parse_te(html)
        self.assertEqual(out.get("recent"), [])
        self.assertTrue(out.get("parser_broken"))
        self.assertIn("parse_te", buf.getvalue())

    def test_parse_te_quiet_on_empty(self):
        out = parse_te("")
        self.assertEqual(out.get("recent"), [])
        self.assertIsNone(out.get("parser_broken"))


class TestPaperAuto(unittest.TestCase):
    def test_cmd_auto_accepts_file_list(self):
        # Баг 18.09: main() передаёт список файлов, cmd_auto ждал --files
        # и всегда отвечал "нужны --files". Проверяем на пустом файле —
        # должен отработать (0 сигналов), а не ругнуться.
        import tempfile
        import pari_paper
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl",
                                         delete=False, encoding="utf-8") as f:
            f.write('{"ts": 1, "matches": [], "finals": []}\n')
            path = f.name
        tmpdir = tempfile.mkdtemp()
        old_paper = pari_paper.PAPER
        pari_paper.PAPER = os.path.join(tmpdir, "paper.jsonl")
        try:
            buf = io.StringIO()
            import contextlib
            with contextlib.redirect_stdout(buf):
                pari_paper.cmd_auto([path])
            self.assertNotIn("нужны --files", buf.getvalue())
        finally:
            os.unlink(path)
            pari_paper.PAPER = old_paper


if __name__ == "__main__":
    unittest.main(verbosity=2)
