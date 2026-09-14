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


# Калибровка силы подачи по прематч-кэфу подающего (замер 14.09, ~200 геймов):
# <1.4: 83%, 1.4-1.8: 81%, 1.8-2.5: 78%, 2.5-4: 77%, 4+: 48%
SERVE_HOLD_CURVE = [(1.4, 0.83), (1.8, 0.81), (2.5, 0.78), (4.0, 0.77), (99.0, 0.48)]


def hold_for_odds(o):
    try:
        o = float(o)
    except (ValueError, TypeError):
        return None
    for bound, h in SERVE_HOLD_CURVE:
        if o < bound:
            return h
    return SERVE_HOLD_CURVE[-1][1]


def hold_to_p(H):
    lo, hi = 0.45, 0.95
    for _ in range(40):
        mid = (lo + hi) / 2
        if _pg(0, 0, mid) < H:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2, 3)


def serve_point_prob(stats, tour, server, prem_odds=None):
    """p: калибровка по прематч-кэфу подающего -> live-статка -> база тура."""
    p = None
    if prem_odds:
        h = hold_for_odds(prem_odds)
        if h:
            p = hold_to_p(h)
    if p is None:
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
    сета (или None), next_srv=1/2. ВАЖНО: будущие сеты — нейтральной ps с 0-0,
    иначе счёт 5-3 раздувает всё до 99% (баг 14.09, матч Пиккарт)."""
    need = 2 if best_of == 3 else 3
    s1, s2 = sets_won
    if s1 >= need:
        return 1.0
    if s2 >= need:
        return 0.0
    ps_neutral = (_set_prob(0, 0, 1, h1, h2) + _set_prob(0, 0, 2, h1, h2)) / 2
    if cur_games is None:
        ps = max(0.05, min(0.95, 0.5 + 0.2 * (h1 - h2)))
    else:
        a, b = cur_games
        ps = _set_prob(a, b, next_srv, h1, h2)
    # разложение по исходам оставшихся сетов (BO3).
    # ps_now — только ТЕКУЩИЙ сет; дальше всегда нейтральная ps_n.
    p10 = ps_neutral + (1 - ps_neutral) * ps_neutral  # взять матч с 1-0
    if s1 == 1 and s2 == 0:
        return ps + (1 - ps) * ps_neutral
    if s1 == 0 and s2 == 1:
        return ps * ps_neutral
    if s1 == 0 and s2 == 0:
        if cur_games:
            return ps * p10
        return ps_neutral * ps_neutral * (3 - 2 * ps_neutral)
    if s1 == 1 and s2 == 1:
        return ps
    return ps
    return ps


# ---------------- Монте-Карло матча ----------------
import random as _rnd


def sim_match(h1, h2, sets_won=(0, 0), cur=None, server=1, game_pts=None,
              p1=None, p2=None, n=2000, seed=None):
    """Симуляция концовки матча. h1/h2 — P(холда). cur=(a,b) текущий сет
    (или None), server — кто подаёт сейчас (1/2), game_pts — очки текущего
    гейма (c1,c2) или None, p1/p2 — P(очка на подаче) для точного гейма.
    Возвращает dict распределений."""
    if seed is not None:
        _rnd.seed(seed)
    tot_games, margins, set3, exact, wins = [], [], 0, {}, 0
    for _ in range(n):
        s1, s2 = sets_won
        gms = []
        a, b = cur if cur else (0, 0)
        srv = server
        # текущий гейм с учётом очков: досиммулировать по очкам
        if game_pts and p1 is not None and p2 is not None:
            pa = PT_MAP.get(str(game_pts[0]).strip(), 0)
            cb = PT_MAP.get(str(game_pts[1]).strip(), 0)
            ca = pa
            pp = p1 if srv == 1 else p2
            while True:
                if _rnd.random() < pp:
                    ca += 1
                else:
                    cb += 1
                if ca >= 4 and ca - cb >= 2:
                    a += 1
                    break
                if cb >= 4 and cb - ca >= 2:
                    b += 1
                    break
                if ca > 4 or cb > 4:
                    ca, cb = 3, 3  # ровно
            srv = 2 if srv == 1 else 1
        while True:
            # играем геймы до конца сета
            while not ((a == 6 and b <= 4) or (b == 6 and a <= 4)
                       or (a, b) in ((7, 5), (5, 7))):
                if a == 6 and b == 6:
                    tb = 0.5 + 0.1 * (h1 - h2)
                    tb = max(0.05, min(0.95, tb))
                    if _rnd.random() < tb:
                        a = 7
                    else:
                        b = 7
                    break
                hh = h1 if srv == 1 else h2
                if _rnd.random() < (hh if srv == 1 else 1 - (1 - hh)):
                    pass
                if srv == 1:
                    if _rnd.random() < hh:
                        a += 1
                    else:
                        b += 1
                else:
                    if _rnd.random() < hh:
                        b += 1
                    else:
                        a += 1
                srv = 2 if srv == 1 else 1
            gms.append((a, b))
            if a > b:
                s1 += 1
            else:
                s2 += 1
            if s1 >= 2 or s2 >= 2:
                break
            a, b = 0, 0
        tot_games.append(sum(x + y for x, y in gms))
        margins.append(sum(x - y for x, y in gms))
        if s1 + s2 >= 3:
            set3 += 1
        if s1 > s2:
            wins += 1
        exact[tuple(gms)] = exact.get(tuple(gms), 0) + 1
    tot_games.sort()
    margins.sort()

    def pct(q, arr):
        return arr[min(len(arr) - 1, int(q * len(arr)))]
    return {"n": n, "p1": wins / n,
            "total": tot_games, "margin": margins,
            "p3sets": set3 / n,
            "exact": {k: v / n for k, v in sorted(exact.items(), key=lambda x: -x[1])[:6]}}


# ---------------- Точный счёт гейма и тотал очков ----------------
def game_exact_dist(p):
    """Распределение исходов гейма при P(очко)=p: {(a,b): prob} + P(ровно)."""
    from math import comb
    q = 1 - p
    d = {}
    d[(4, 0)] = p ** 4
    d[(0, 4)] = q ** 4
    d[(4, 1)] = comb(4, 1) * p ** 4 * q
    d[(1, 4)] = comb(4, 1) * q ** 4 * p
    d[(4, 2)] = comb(5, 2) * p ** 4 * q ** 2
    d[(2, 4)] = comb(5, 2) * q ** 4 * p ** 2
    decided = sum(d.values())
    d["deuce"] = max(0.0, 1 - decided)
    # победа из ровно
    dd = p * p / (p * p + q * q) if (p * p + q * q) > 0 else 0.5
    d["hold"] = d[(4, 0)] + d[(4, 1)] + d[(4, 2)] + d["deuce"] * dd
    return d


def game_total_dist(p):
    """P(тотал очков в гейме > X) для X=4.5/5.5/6.5."""
    d = game_exact_dist(p)
    # очков в гейме: 4-0->4, 4-1->5, 4-2->6, ровно->7+
    p4 = d[(4, 0)] + d[(0, 4)]
    p5 = d[(4, 1)] + d[(1, 4)]
    p6 = d[(4, 2)] + d[(2, 4)]
    # из ровно: распределение длины (геометр. с p_deuce_end = p^2+q^2)
    pe = p * p + (1 - p) * (1 - p)
    # P(ровно N раз) -> очков 6+2k; P(total>6.5) = P(деuce и длина>...)
    pdeuce = d["deuce"]
    # P(>4.5) = 1 - p4; P(>5.5) = 1 - p4 - p5; P(>6.5) = pdeuce * P(длина из ровно >0)... ровно минимум 8 очков? нет:
    # ровно наступает после 3-3 (6 очков), дальше минимум 2 очка -> 8. Значит P(>6.5) = pdeuce * 1? нет:
    # >6.5 значит 7+ очков: p6 (ровно 6? нет, 4-2 это 6 очков) hmm:
    # 4-2 = 6 очков (не >6.5), ровно = 8+ очков (всегда >6.5)
    return {"over4.5": 1 - p4, "over5.5": 1 - p4 - p5,
            "over6.5": pdeuce}
