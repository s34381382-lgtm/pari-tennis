#!/usr/bin/env python3
"""Читаемо показать, что накопил Pari Collector (формат API v2 + старый SSR).

  python3 pari_view.py --pull            # забрать свежий jsonl с телефона
  python3 pari_view.py                   # сейчас в эфире + движение счёта и кэфов
  python3 pari_view.py --match 68050403  # история матча: счёт и кэфы П1 по времени
  python3 pari_view.py --list            # все матчи за сессию
  python3 pari_view.py --file /tmp/x.jsonl  # другой файл (напр. line.jsonl)
"""
import json
import subprocess
import sys
import os
from datetime import datetime

PHONE = "localhost:43447"
LOCAL = "/tmp/pari_line.jsonl"
ADB = ["adb", "-s", PHONE]


def pull():
    day = datetime.now().strftime("%Y-%m-%d")
    remote = f"/sdcard/Android/data/com.pari.collector/files/line-{day}.jsonl"
    r = subprocess.run(ADB + ["shell", f"cat {remote}"],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0 or not r.stdout.strip():
        print("нет данных на телефоне:", r.stderr.strip()[:200])
        sys.exit(1)
    open(LOCAL, "w", encoding="utf-8").write(r.stdout)
    print(f"забрано {len(r.stdout) // 1024} KB -> {LOCAL}")


def load(path):
    snaps = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line:
            try:
                snaps.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return snaps


def evs_of(s):
    """Единый вид: [{eid,p1,p2,tour,sets,setscore,game,odds{},stats}] (оба формата)."""
    out = []
    for e in s.get("matches", []) + s.get("events", []):
        odds = {}
        for o in e.get("odds", []):
            m = o.get("m", "?")
            key = m if o.get("pt") in (None, "") else f"{m} ({o['pt']})"
            odds[key] = o.get("v")
        out.append({
            "eid": str(e.get("eid", "?")),
            "p1": e.get("p1", "?") or "?", "p2": e.get("p2", "?") or "?",
            "tour": (e.get("tour") or e.get("cat") or "?")[:34],
            "sets": e.get("sets"), "sc": e.get("set_scores") or e.get("setscore"),
            "game": e.get("game"),
            "odds": odds,
        })
    return out


def score_str(e):
    s = ""
    sets = e.get("sets")
    if isinstance(sets, str):
        s += sets + " "
    elif sets:
        s += ":".join(str(x) for x in sets) + " "
    if e.get("sc"):
        s += " ".join("-".join(str(x) for x in g) for g in e["sc"]) \
            if isinstance(e["sc"], list) else str(e["sc"])
    if e.get("game"):
        s += f" [{'-'.join(str(x) for x in e['game'])}]"
    return s.strip() or "?"


def win_odds(e):
    """Кэфы победы П1/П2 (ищем рынки исхода)."""
    p1 = p2 = None
    for m, v in e["odds"].items():
        ml = m.lower()
        if ("1" in ml.split("/") or ml.rstrip().endswith("/ 1")
                or "победа" in ml and "1" in ml) and p1 is None:
            if "(" not in m and "фора" not in ml and "тотал" not in ml:
                p1 = v
        if ("2" in ml.split("/") or ml.rstrip().endswith("/ 2")) and p2 is None:
            if "(" not in m and "фора" not in ml and "тотал" not in ml:
                p2 = v
    # запасной вариант: рынки "Основные ставки / 1" и "/ 2" без параметров
    if p1 is None:
        p1 = e["odds"].get("Основные ставки / 1")
    if p2 is None:
        p2 = e["odds"].get("Основные ставки / 2")
    return p1, p2


def merged_odds(snaps):
    """Сквозное состояние кэфов (дифы -> полное): eid -> {key: (m,v,pt)}."""
    cur = {}
    for s in snaps:
        for e in s.get("matches", []) + s.get("events", []):
            eid = str(e.get("eid", "?"))
            d = cur.setdefault(eid, {})
            for o in e.get("odds", []):
                key = (o.get("f"), o.get("pt"))
                d[key] = o
    return cur


def report_live(snaps):
    last = snaps[-1]
    evs = evs_of(last)
    full = merged_odds(snaps)
    for e in evs:
        if e["eid"] in full and full[e["eid"]]:
            e["odds"] = {f"{o.get('m')} ({o.get('pt')})"
                         if o.get("pt") not in (None, "") else o.get("m", "?"): o.get("v")
                         for o in full[e["eid"]].values()}
    t = datetime.fromtimestamp(last["ts"]).strftime("%H:%M:%S")
    print(f"=== В ЭФИРЕ ({t}) — {len(evs)} матчей ===")
    for e in sorted(evs, key=lambda x: (x["tour"], str(x["p1"]))):
        p1, p2 = win_odds(e)
        ko = f" {p1}-{p2}" if p1 else ""
        w, g, indoor = wind_for(e["tour"])
        if indoor:
            wx = " зал"
        elif w is None:
            wx = ""
        else:
            wx = f" ветер {w:.0f}/{g:.0f}" + ("!!" if w >= 6 or g >= 9 else "")
        print(f" [{e['tour']:34s}] {e['p1']:22s} - {e['p2']:22s} "
              f"{score_str(e):22s}{ko}{wx}")
    print()
    print("=== ДВИЖЕНИЕ (последние ~3 мин) ===")
    cutoff = last["ts"] - 200
    hist = {}
    for s in snaps:
        if s["ts"] < cutoff:
            continue
        for e in evs_of(s):
            hist.setdefault(e["eid"], []).append((s["ts"], e))
    shown = 0
    for eid, hh in hist.items():
        scores = [score_str(x[1]) for x in hh]
        odds = [win_odds(x[1]) for x in hh]
        sc_moved = len(set(scores)) > 1
        od_moved = len(set(o for o in odds if o != (None, None))) > 1
        if not (sc_moved or od_moved):
            continue
        e = hh[-1][1]
        print(f" {e['p1']} - {e['p2']}:")
        if sc_moved:
            chain = " -> ".join(dict.fromkeys(scores))
            print(f"    счёт: {chain}")
        if od_moved:
            chain = " -> ".join(dict.fromkeys(
                f"{a}-{b}" for a, b in odds if a is not None))
            print(f"    П1-П2: {chain}")
        shown += 1
    if not shown:
        print(" (тишина)")


def report_match(snaps, eid):
    rows = []
    for s in snaps:
        for e in evs_of(s):
            if e["eid"] == eid:
                key = (score_str(e), win_odds(e))
                if not rows or rows[-1][0] != key:
                    rows.append((key, s["ts"]))
    if not rows:
        print(f"матч {eid} не найден")
        return
    e0 = next(e for s in snaps for e in evs_of(s) if e["eid"] == eid)
    print(f"=== {e0['p1']} - {e0['p2']} [{e0['tour']}] ===")
    t0 = rows[0][1]
    for (sc, (a, b)), ts in rows:
        ko = f"  кэф {a}-{b}" if a else ""
        print(f"  +{int((ts - t0) / 60):3d} мин  {sc}{ko}")


def report_list(snaps):
    seen = {}
    for s in snaps:
        for e in evs_of(s):
            seen.setdefault(e["eid"], e)
    print(f"=== МАТЧЕЙ ЗА СЕССИЮ: {len(seen)} ===")
    for e in sorted(seen.values(), key=lambda x: x["tour"]):
        print(f" [{e['tour']:34s}] {e['p1']:22s} - {e['p2']:22s}  id={e['eid']}")


# ---------------- ветер ----------------

GEO = {  # подстрока турнира -> (широта, долгота, крытый?)
    "Ренн": (48.11, -1.68, True),
    "Тибурон": (37.87, -122.45, False),
    "Ньюпорт": (33.62, -117.93, False),
    "Гвадалахара": (20.67, -103.35, False),
    "Сан-Паулу": (-23.55, -46.63, False),
    "Хургада": (27.25, 33.81, False),
    "Монастир": (35.76, 10.81, False),
    "Таллахасси": (30.44, -84.28, False),
    "Шарм": (27.91, 34.33, False),
    "Шымкент": (42.30, 69.59, False),
    "Анталья": (36.88, 30.70, False),
    "Кайсери": (38.72, 35.48, False),
}
WIND_CACHE = {}


def wind_for(tour):
    """(m/s, порывы, крытый?) с часовым кешем."""
    import time as _t
    import urllib.request as _u
    for key, (la, lo, indoor) in GEO.items():
        if key.lower() in (tour or "").lower():
            if indoor:
                return (0, 0, True)
            c = WIND_CACHE.get(key)
            if c and _t.time() - c[0] < 3600:
                return c[1]
            try:
                u = (f"https://api.open-meteo.com/v1/forecast?latitude={la}&longitude={lo}"
                     f"&current=wind_speed_10m,wind_gusts_10m&wind_speed_unit=ms&timezone=auto")
                d = __import__("json").load(_u.urlopen(
                    _u.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=12))
                w = (d["current"]["wind_speed_10m"], d["current"]["wind_gusts_10m"], False)
                WIND_CACHE[key] = (_t.time(), w)
                return w
            except Exception:
                return (None, None, False)
    return (None, None, False)

def main():
    args = sys.argv[1:]
    path = LOCAL
    if "--file" in args:
        path = args[args.index("--file") + 1]
    if "--pull" in args:
        pull()
    if not os.path.exists(path):
        pull()
    snaps = load(path)
    if not snaps:
        print("пусто")
        return
    print(f"(снимков: {len(snaps)})")
    if "--match" in args:
        report_match(snaps, args[args.index("--match") + 1])
    elif "--list" in args:
        report_list(snaps)
    else:
        report_live(snaps)


if __name__ == "__main__":
    main()
