#!/usr/bin/env python3
"""Общая база: контуры денег, страны турниров, горизонты сигналов."""
import re

# LAB — мелкие рынки (ITF/UTR/квалификации): только лаборатория мелочью.
# MAIN — основа WTA/ATP: туда идёт размер.
LAB_RE = re.compile(r"ITF|UTR|квалифик|челленджер|challenger", re.I)
MAIN_RE = re.compile(r"WTA\.|ATP\.", re.I)


def tour_circuit(tour):
    t = tour or ""
    if LAB_RE.search(t):
        return "LAB"
    if MAIN_RE.search(t):
        return "MAIN"
    return "LAB"


CIRCUIT_STAKE = {"LAB": 0.5, "MAIN": 1.0, "ASYM": 1.5}

# турнир -> страна (для «местных голодных» и акклиматизации)
TOUR_COUNTRY = {
    "Хургада": "EGY", "Hurghada": "EGY", "Шарм": "EGY", "Sharm": "EGY",
    "Монастир": "TUN", "Monastir": "TUN",
    "Таллахасси": "USA", "Tallahassee": "USA",
    "Тибурон": "USA", "Tiburon": "USA", "Ньюпорт": "USA", "Newport": "USA",
    "Гвадалахара": "MEX", "Guadalajara": "MEX",
    "Сан-Паулу": "BRA", "Sao Paulo": "BRA",
    "Ренн": "FRA", "Rennes": "FRA",
    "Фантхьет": "VIE", "Анталья": "TUR", "Antalya": "TUR",
    "Шымкент": "KAZ", "Кайсери": "TUR",
}

ALLOWED_HORIZONS = ("game", "set", "match", "prematch")


def tour_country(tour):
    t = tour or ""
    for k, v in TOUR_COUNTRY.items():
        if k.lower() in t.lower():
            return v
    return ""


# ---------------- Марковская цена гейма ----------------
from functools import lru_cache as _lru


@_lru(maxsize=None)
def _pg(a, b, p):
    if a > b and a >= 4:
        return 1.0
    if b > a and b >= 4:
        return 0.0
    if a == 3 and b == 3:
        return p * p / (p * p + (1 - p) * (1 - p))
    if a == 3:
        d = p * p / (p * p + (1 - p) * (1 - p))
        return p + (1 - p) * d
    if b == 3:
        d = p * p / (p * p + (1 - p) * (1 - p))
        return p * d
    return p * _pg(a + 1, b, p) + (1 - p) * _pg(a, b + 1, p)


PT_MAP = {"00": 0, "0": 0, "15": 1, "30": 2, "40": 3, "A": 4, "А": 4}


def game_fair(p,pts):
    """Честная P(подающий берёт гейм) из счёта очков. pts=(c1,c2) '40','15'..."""
    try:
        a = PT_MAP.get(str(pts[0]).strip(), 0)
        b = PT_MAP.get(str(pts[1]).strip(), 0)
    except (IndexError, TypeError):
        return None
    if a >= 4 or b >= 4:  # гейм почти кончен — не котируем
        return None
    return round(_pg(a, b, p), 3)


# Базовый p (шанс очка на подаче) по нашим замерам брейков:
# мужчины hold~79% -> p~0.63, женщины hold~68% -> p~0.585
def base_point_prob(tour):
    t = (tour or "").upper()
    if "WTA" in t or "WOMEN" in t or "ЖЕНЩИН" in t:
        return 0.585
    return 0.63


def serve_point_prob(stats, tour, server):
    """p с учётом live-статы (эйсы/двойные), иначе база турнира."""
    p = base_point_prob(tour)
    if not stats:
        return p
    try:
        if server == "p1":
            ac, df = int(stats.get("эйсы", {}).get("c1", 0) or 0), \
                     int(stats.get("двойные ошибки", {}).get("c1", 0) or 0)
        else:
            ac, df = int(stats.get("эйсы", {}).get("c2", 0) or 0), \
                     int(stats.get("двойные ошибки", {}).get("c2", 0) or 0)
        # каждый чистый эйс сверх двойных сдвигает p (эмпирика v1: ±0.008, кап ±0.04)
        p += max(-0.04, min(0.04, 0.008 * (ac - df)))
    except (ValueError, TypeError):
        pass
    return round(p, 3)


# ---------------- Марковская цена МАТЧА ----------------
from functools import lru_cache as _lru2


@_lru2(maxsize=None)
def _set_prob(a, b, srv, h1, h2):
    """P(1-й выиграет сет) при счёте a-b, подача srv (1/2).
    h1/h2 — P(холда) каждого. Тай-брейк 6-6: 50/50 со сдвигом силы подачи."""
    if (a == 6 and b <= 4) or (a == 7):
        return 1.0
    if (b == 6 and a <= 4) or (b == 7):
        return 0.0
    if a == 6 and b == 6:
        tb = 0.5 + 0.1 * (h1 - h2)
        return max(0.05, min(0.95, tb))
    h = h1 if srv == 1 else h2
    ns = 2 if srv == 1 else 1
    if srv == 1:
        return h * _set_prob(a + 1, b, ns, h1, h2) + (1 - h) * _set_prob(a, b + 1, ns, h1, h2)
    return (1 - h) * _set_prob(a + 1, b, ns, h1, h2) + h * _set_prob(a, b + 1, ns, h1, h2)


def match_fair(sets_won, cur_games, next_srv, h1, h2, best_of=3):
    """Честная P(1-й выиграет матч). sets_won=(s1,s2), cur_games=(a,b) текущего
    сета (или None), next_srv=1/2. Возвращает 0..1."""
    need = 2 if best_of == 3 else 3
    s1, s2 = sets_won
    if s1 >= need:
        return 1.0
    if s2 >= need:
        return 0.0
    if cur_games is None:
        ps = 0.5  # между сетами без счёта — грубо пополам с весом холдов
        ps = 0.5 + 0.2 * (h1 - h2)
        ps = max(0.05, min(0.95, ps))
    else:
        a, b = cur_games
        ps = _set_prob(a, b, next_srv, h1, h2)
    # разложение по исходам оставшихся сетов (BO3)
    if s1 == 1 and s2 == 0:
        return ps + (1 - ps) * ps
    if s1 == 0 and s2 == 1:
        return ps * ps
    if s1 == 0 and s2 == 0:
        return ps * ps * (3 - 2 * ps)
    if s1 == 1 and s2 == 1:
        return ps
    return ps
