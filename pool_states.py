#!/usr/bin/env python3
"""Глубокий анализ единого пула: база, туры, обратные, условия,
поинт-состояния (М/Ж), чок, сеты, паузы.

  python3 pool_states.py                        # пул по умолчанию
  python3 pool_states.py --pool /tmp/p.jsonl

Только чтение. Порог пометки СИГНАЛ: |Δ|>=5пп при n>=30.
База холда пересчитывается из пула (дубли не искажают: геймы
считаются по смене счёта, а не по числу строк).
"""
import json
import os
import sys
from collections import defaultdict

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
from pari_patterns import build_timelines, valid_set, is_women, trigger_pauses
from pari_lib import tour_circuit
from pari_rules import game_rows, conditions, cond_holds_server

DATA = os.environ.get("PARI_DATA", "/public")


def show(name, sub, base):
    n = len(sub)
    if not n:
        print(f"  {name}: n=0")
        return
    h = sum(1 for g in sub if g["winner"] == g["server"])
    d = h / n - base
    mark = "  <-- СИГНАЛ" if abs(d) >= 0.05 and n >= 30 else ""
    print(f"  {name}: {h}/{n} ({100*h/n:.1f}%, Δ{d:+.1%}){mark}")


def main():
    args = sys.argv[1:]
    pool = (args[args.index("--pool") + 1] if "--pool" in args
            else os.path.join(DATA, "pool", "pool.jsonl"))
    if not os.path.exists(pool):
        print(f"нет пула: {pool} — сначала pool_build.py")
        sys.exit(1)

    # ---------- проход 1: геймы с сервером + условия ----------
    TL = build_timelines([pool])
    rows = game_rows(TL)
    H = sum(1 for r in rows if cond_holds_server(r))
    BASE = H / len(rows) if rows else 0.5
    print(f"матчей: {len(TL)}, геймов: {len(rows)}, база холда: {H}/{len(rows)} ({100*BASE:.1f}%)")

    print("\n[РАЗРЕЗЫ]:")
    for name, fn in [
        ("LAB", lambda r: tour_circuit(r["tour"]) == "LAB"),
        ("MAIN", lambda r: tour_circuit(r["tour"]) == "MAIN"),
        ("Ж", lambda r: is_women(r["tour"])),
        ("М", lambda r: not is_women(r["tour"])),
        ("сет1", lambda r: r["set"] == 1),
        ("сет2", lambda r: r["set"] == 2),
        ("сет3+", lambda r: r["set"] >= 3),
    ]:
        sub = [r for r in rows if fn(r)]
        h = sum(1 for r in sub if cond_holds_server(r))
        print(f"  {name}: {h}/{len(sub)} ({100*h/len(sub):.1f}%)" if sub else f"  {name}: n=0")

    print("\n[ТУРЫ] холд (n>=15):")
    by_tour = defaultdict(list)
    for r in rows:
        by_tour[r["tour"] or "?"].append(r)
    for t, rr in sorted(by_tour.items(), key=lambda x: -len(x[1])):
        if len(rr) >= 15:
            h = sum(1 for r in rr if cond_holds_server(r))
            print(f"  {t[:40]:40s} {h}/{len(rr)} ({100*h/len(rr):.1f}%) {tour_circuit(t)}")

    print("\n[ОБРАТНЫЕ] (n_брейков>=8):")
    seq = defaultdict(list)
    for eid, t in TL.items():
        for g in (x for x in t["games"] if x["winner"] and x["server"]):
            seq[eid].append((g["winner"], g["server"], t.get("tour", "")))
    allb = sum(1 for v in seq.values() for i in range(len(v) - 1) if v[i][0] != v[i][1])
    allbb = sum(1 for v in seq.values() for i in range(len(v) - 1)
                if v[i][0] != v[i][1] and v[i+1][0] == v[i][1] and v[i+1][0] != v[i+1][1])
    print(f"  ВСЕ: {allbb}/{allb} ({100*allbb/allb:.1f}%)" if allb else "  n=0")
    tb = defaultdict(lambda: [0, 0])
    for v in seq.values():
        for i in range(len(v) - 1):
            if v[i][0] != v[i][1]:
                tb[v[i][2] or "?"][0] += 1
                if v[i+1][0] == v[i][1] and v[i+1][0] != v[i+1][1]:
                    tb[v[i][2] or "?"][1] += 1
    for t, (n, h) in sorted(tb.items(), key=lambda x: -x[1][0]):
        if n >= 8:
            print(f"  {t[:40]:40s} {h}/{n} ({100*h/n:.1f}%)")

    print("\n[УСЛОВИЯ]:")
    tot_h = tot_n = 0
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
        tot_h += h
        tot_n += n
    base_c = tot_h / tot_n if tot_n else 0.5
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
        if not n:
            print(f"  {rid:14s} n=0")
            continue
        d = h / n - base_c
        mark = "  <-- СИГНАЛ" if abs(d) >= 0.05 and n >= 30 else ""
        print(f"  {rid:14s} {h}/{n} ({100*h/n:.1f}%, Δ{d:+.1%}){mark}")

    # ---------- проход 2: геймы с поинт-состояниями ----------
    per_eid = defaultdict(list)
    for line in open(pool, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        for m in d.get("matches", []):
            per_eid[str(m.get("eid"))].append((d.get("ts", 0), m))
    games = []
    for eid, sq in per_eid.items():
        sq.sort(key=lambda x: x[0])
        tour = ""
        cur = None
        for _, m in sq:
            if m.get("tour"):
                tour = m["tour"]
            ss = m.get("set_scores") or []
            gm = m.get("game")
            try:
                srv_i = int(m.get("serve")) if m.get("serve") else None
            except (ValueError, TypeError):
                srv_i = None
            if not ss:
                continue
            try:
                si, a, b = len(ss), int(ss[-1][0]), int(ss[-1][1])
            except (ValueError, TypeError):
                continue
            if cur is None:
                cur = [si, a, b, srv_i, set()]
                if gm and len(gm) == 2 and srv_i:
                    cur[4].add((tuple(gm), srv_i))
                continue
            if (si, a, b) == (cur[0], cur[1], cur[2]):
                if gm and len(gm) == 2 and srv_i:
                    cur[4].add((tuple(gm), srv_i))
                if srv_i:
                    cur[3] = srv_i
                continue
            if si == cur[0] and ((a == cur[1] + 1) != (b == cur[2] + 1)) and cur[3]:
                winner = "p1" if a == cur[1] + 1 else "p2"
                server = "p1" if cur[3] == 1 else "p2"
                pts = cur[4]
                def has(pred):
                    return any(pred(p, s) for (p, s) in pts if s == cur[3])
                lead40 = has(lambda p, s: (p[0] == "40" and p[1] in ("00", "15"))
                             if cur[3] == 1 else (p[1] == "40" and p[0] in ("00", "15")))
                def40 = has(lambda p, s: (p[0] in ("00", "15") and p[1] == "40")
                            if cur[3] == 1 else (p[1] in ("00", "15") and p[0] == "40"))
                st1530 = has(lambda p, s: (p == ("15", "30") if cur[3] == 1 else p == ("30", "15")))
                st030 = has(lambda p, s: (p == ("00", "30") if cur[3] == 1 else p == ("30", "00")))
                st300 = has(lambda p, s: (p == ("30", "00") if cur[3] == 1 else p == ("00", "30")))
                deuce = has(lambda p, s: p == ("40", "40") or "A" in p)
                games.append({"eid": eid, "tour": tour, "server": server,
                              "winner": winner, "set": si, "gno": a + b,
                              "lead40": lead40, "def40": def40,
                              "s15_30": st1530, "s0_30": st030,
                              "s30_0": st300, "deuce": deuce,
                              "women": is_women(tour)})
            cur = [si, a, b, srv_i, set()]
            if gm and len(gm) == 2 and srv_i:
                cur[4].add((tuple(gm), srv_i))

    print(f"\n[СОСТОЯНИЯ] геймов: {len(games)}")
    show("вёл 40-0/40-15 -> холд", [g for g in games if g["lead40"]], BASE)
    chokes = [g for g in games if g["lead40"] and g["winner"] != g["server"]]
    print(f"  ЧОК (вёл и проиграл): {len(chokes)} случаев")
    show("был 0-40 -> холд (сейв)", [g for g in games if g["def40"]], BASE)
    show("было 15-30 -> холд", [g for g in games if g["s15_30"]], BASE)
    show("было 0-30 -> холд", [g for g in games if g["s0_30"]], BASE)
    show("было 30-0 -> холд", [g for g in games if g["s30_0"]], BASE)
    show("было ровно -> холд", [g for g in games if g["deuce"]], BASE)

    print("\n[СОСТОЯНИЯ ПО ПОЛАМ]:")
    for w, lab in ((True, "Ж"), (False, "М")):
        show(f"{lab} 15-30", [g for g in games if g["women"] == w and g["s15_30"]], BASE)
        show(f"{lab} 0-30", [g for g in games if g["women"] == w and g["s0_30"]], BASE)
        show(f"{lab} ровно", [g for g in games if g["women"] == w and g["deuce"]], BASE)
        show(f"{lab} вёл 40-0", [g for g in games if g["women"] == w and g["lead40"]], BASE)

    print("\n[НОМЕРА ГЕЙМОВ] (n>=30):")
    by_gno = defaultdict(list)
    for g in games:
        by_gno[g["gno"]].append(g)
    for k in sorted(by_gno):
        if len(by_gno[k]) >= 30:
            h = sum(1 for g in by_gno[k] if g["winner"] == g["server"])
            d = h / len(by_gno[k]) - BASE
            mark = "  <--" if abs(d) >= 0.05 else ""
            print(f"  гейм {k}: {h}/{len(by_gno[k])} ({100*h/len(by_gno[k]):.1f}%, Δ{d:+.1%}){mark}")
    for w, lab in ((True, "Ж"), (False, "М")):
        show(f"{lab} гейм1", [g for g in games if g["women"] == w and g["gno"] == 1], BASE)
        show(f"{lab} гейм3", [g for g in games if g["women"] == w and g["gno"] == 3], BASE)

    print("\n[СЛЕДСТВИЯ]:")
    by_eid = defaultdict(list)
    for g in games:
        by_eid[g["eid"]].append(g)
    n = h = 0
    for v in by_eid.values():
        for i, g in enumerate(v):
            if g["lead40"] and g["winner"] != g["server"]:
                for nxt in v[i+1:]:
                    if nxt["server"] == g["server"]:
                        n += 1
                        h += nxt["winner"] == nxt["server"]
                        break
    print(f"  после ЧОКА след. холд: {h}/{n} ({100*h/n:.1f}%)" if n else "  после ЧОКА: n=0")
    n1 = h1 = n2 = h2 = 0
    for v in by_eid.values():
        for i, g in enumerate(v):
            if g["def40"]:
                for nxt in v[i+1:]:
                    if nxt["server"] == g["server"]:
                        if g["winner"] == g["server"]:
                            n1 += 1
                            h1 += nxt["winner"] == nxt["server"]
                        else:
                            n2 += 1
                            h2 += nxt["winner"] == nxt["server"]
                        break
    print(f"  спас 0-40 -> след. холд: {h1}/{n1} ({100*h1/n1:.1f}%)" if n1 else "  спас 0-40: n=0")
    print(f"  отдал с 0-40 -> след. холд: {h2}/{n2} ({100*h2/n2:.1f}%)" if n2 else "  отдал с 0-40: n=0")

    print("\n[СЕТЫ 1->2]:")
    finals = {}
    for eid, sq in per_eid.items():
        for _, m in reversed(sq):
            ss = m.get("set_scores") or []
            if ss:
                finals[eid] = ss
                break
    pairs, dom, tbs = [], [], []
    for ss in finals.values():
        try:
            s1 = (int(ss[0][0]), int(ss[0][1]))
            if len(ss) < 2:
                continue
            s2 = (int(ss[1][0]), int(ss[1][1]))
        except (ValueError, TypeError, IndexError):
            continue
        if not (valid_set(s1) and valid_set(s2)):
            continue
        w1 = "p1" if s1[0] > s1[1] else "p2"
        w2 = "p1" if s2[0] > s2[1] else "p2"
        pairs.append(w1 == w2)
        if min(s1) <= 1:
            dom.append(w1 == w2)
        if s1 in ((7, 6), (6, 7)):
            tbs.append(w1 == w2)
    if pairs:
        print(f"  1-й берёт 2-й: {sum(pairs)}/{len(pairs)} ({100*sum(pairs)/len(pairs):.1f}%)")
    print(f"  после 6-0/6-1: {sum(dom)}/{len(dom)} ({100*sum(dom)/len(dom):.1f}%)" if dom else "  доминация: n=0")
    print(f"  победитель ТБ: {sum(tbs)}/{len(tbs)} ({100*sum(tbs)/len(tbs):.1f}%)" if tbs else "  ТБ: n=0")

    n, h = trigger_pauses(TL)
    print(f"\n[ПАУЗА 4+мин] брейк следующим: {h}/{n} ({100*h/n:.1f}%)" if n else "\n[ПАУЗА] n=0")
    print("\nDONE")


if __name__ == "__main__":
    main()
