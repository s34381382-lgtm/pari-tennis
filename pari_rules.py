#!/usr/bin/env python3
"""Движок правил ЕСЛИ-ТО: майнинг условий из базы + живая оценка.

Каждое правило: условие на момент ДО гейма -> исход гейма (холд/срыв).
Формат: IF <условие> THEN <прогноз> (n=.., rate=..).

  python3 pari_rules.py --mine --files "night/*.jsonl"   # пересчитать правила
  python3 pari_rules.py --list                            # показать таблицу
  python3 pari_rules.py --eval --file /tmp/x.jsonl        # применить к снимкам
"""
import glob
import json
import os
import sys

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
from pari_patterns import build_timelines

RULES_FILE = os.path.join(os.environ.get("PARI_DATA", "/public"), "pari_rules.json")
MIN_N = 15  # минимум геймов для правила


def game_rows(TL):
    """Строки геймов с контекстом ДО гейма: (eid, tour, server, winner,
    prev_winner, prev_server, prev_pts, set_no, game_no, score_before)."""
    rows = []
    for eid, t in TL.items():
        gs = [g for g in t["games"] if g["winner"] and g["server"]]
        for i, g in enumerate(gs):
            p = gs[i - 1] if i > 0 else None
            rows.append({
                "eid": eid, "tour": t.get("tour", ""),
                "server": g["server"], "winner": g["winner"],
                "prev_winner": p["winner"] if p else None,
                "prev_server": p["server"] if p else None,
                "prev_pts": tuple(p["pts_end"]) if p and p.get("pts_end") else (),
                "set": g["set"],
                "prev_dbl": p["dbl"] if p else (0, 0),
            })
    return rows


def cond_holds_server(r):
    return r["winner"] == r["server"]


# Условия: (id, описание, функция от строки -> True/False/None)
def conditions():
    def prev_hold(r):
        return r["prev_winner"] == r["prev_server"] if r["prev_winner"] else None

    def prev_break(r):
        if not r["prev_winner"]:
            return None
        return r["prev_winner"] != r["prev_server"]

    def server_won_last(r):
        return r["prev_winner"] == r["server"] if r["prev_winner"] else None

    def server_lost_last(r):
        if not r["prev_winner"]:
            return None
        return r["prev_winner"] != r["server"]

    def prev_dry(r):
        return r["prev_pts"] in (("40", "00"), ("40", "15"),
                                 ("00", "40"), ("15", "40")) or None

    def prev_deuce(r):
        p = r["prev_pts"]
        return (len(p) == 2 and ("40" in p and ("A" in p or p in (("40", "40"),)))) or None

    def prev_choke(r):
        # подающий вёл 40-0/40-15 и проиграл гейм
        p = r["prev_pts"]
        if not r["prev_winner"] or not r["prev_server"]:
            return None
        led = (p in (("40", "00"), ("40", "15")) and r["prev_server"] == "p1") or \
              (p in (("00", "40"), ("15", "40")) and r["prev_server"] == "p2")
        return bool(led and r["prev_winner"] != r["prev_server"])

    def dbl_in_prev(r):
        s = 0 if r["server"] == "p1" else 1
        return r["prev_dbl"][s] >= 1

    def set2plus(r):
        return r["set"] >= 2

    return [
        ("prev_hold", "прошлый гейм удержан подающим", prev_hold),
        ("prev_break", "прошлый гейм - брейк", prev_break),
        ("srv_won_last", "подающий выиграл прошлый гейм", server_won_last),
        ("srv_lost_last", "подающий проиграл прошлый гейм", server_lost_last),
        ("prev_dry", "прошлый гейм всухую", prev_dry),
        ("prev_deuce", "прошлый гейм через ровно", prev_deuce),
        ("prev_choke", "прошлый гейм - чок (вёл 40-0 и отдал)", prev_choke),
        ("dbl_prev", "у подающего двойная в прошлом гейме", dbl_in_prev),
        ("set2plus", "сет 2+", set2plus),
    ]


def mine(files):
    TL = build_timelines(files)
    rows = game_rows(TL)
    rules = []
    for rid, desc, fn in conditions():
        n = h = 0
        for r in rows:
            try:
                c = fn(r)
            except Exception:
                c = None
            if c is None:
                continue
            if c:
                n += 1
                if cond_holds_server(r):
                    h += 1
        if n >= MIN_N:
            rules.append({"id": rid, "desc": desc, "n": n, "hits": h,
                          "rate": round(h / n, 3)})
    rules.sort(key=lambda x: -abs(x["rate"] - 0.5))
    json.dump({"rules": rules, "games": len(rows)},
              open(RULES_FILE, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    return rules


def show():
    try:
        d = json.load(open(RULES_FILE, encoding="utf-8"))
    except Exception:
        print("нет правил — сначала --mine")
        return
    print(f"геймов в базе: {d.get('games')}")
    base = None
    rows = [r for r in d["rules"]]
    # база = общий холд (правило prev_hold ближе всего, иначе среднее)
    tot_h = tot_n = 0
    for r in rows:
        tot_h += r["hits"]
        tot_n += r["n"]
    base = tot_h / tot_n if tot_n else 0.5
    print(f"базовый холд: {base:.0%} (ставить на холд по базе — НЕ edge, нужны кэфы!)")
    for r in rows:
        delta = r["rate"] - base
        mark = ""
        if abs(delta) >= 0.07 and r["n"] >= 30:
            mark = f"  <-- СИГНАЛ {'против' if delta < 0 else 'за'} холд ({delta:+.0%})"
        print(f"ЕСЛИ {r['desc']}: холд {r['hits']}/{r['n']} ({r['rate']:.0%}, Δ{delta:+.0%}){mark}")


def main():
    args = sys.argv[1:]
    if "--mine" in args:
        i = args.index("--mine") + 1
        files = []
        while i < len(args) and not args[i].startswith("--"):
            if args[i] == "--files":
                i += 1
                continue
            files.extend(sorted(glob.glob(args[i])) or [args[i]])
            i += 1
        # файлы данных по умолчанию
        if not files:
            files = sorted(glob.glob("/public/night/*.jsonl")) + \
                    (["/public/line.jsonl"] if os.path.exists("/public/line.jsonl") else [])
        rules = mine(files)
        print(f"правил: {len(rules)}")
        show()
    elif "--list" in args:
        show()
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
