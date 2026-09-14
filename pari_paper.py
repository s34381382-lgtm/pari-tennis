#!/usr/bin/env python3
"""Бумажный трейдинг: журнал сигналов + автосверка исходов (без денег).

  python3 pari_paper.py add --eid 68050403 --match "Монне - Скотт" --trigger T7 \\
      --market "тотал сета меньше 9.5" --side under --odds 1.85 --stake 1
  python3 pari_paper.py settle --files /public/line.jsonl
  python3 pari_paper.py report
  python3 pari_paper.py auto --files /public/line.jsonl   # сигналы из триггеров + сверка
"""
import json
import os
import sys

REPO = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("PARI_DATA", "/public")
PAPER = os.path.join(DATA, "paper.jsonl")


TRIGGER_HORIZON = {"T7": "set", "ASYM-TB": "set", "A": "match",
                   "B": "set", "C": "game", "D": "game"}


def normalize(s):
    import sys as _s
    _s.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from pari_lib import tour_circuit
    if not s.get("circuit"):
        s["circuit"] = "ASYM" if str(s.get("trigger", "")).startswith("ASYM") \
            else tour_circuit(s.get("tour", ""))
    if not s.get("horizon"):
        s["horizon"] = TRIGGER_HORIZON.get(s.get("trigger"), "match")
    if not s.get("plan"):
        s["plan"] = "-"
    if not s.get("tour"):
        s["tour"] = ""
    return s


def load_signals():
    out = []
    if os.path.exists(PAPER):
        for line in open(PAPER, encoding="utf-8"):
            line = line.strip()
            if line:
                try:
                    out.append(normalize(json.loads(line)))
                except json.JSONDecodeError:
                    pass
    return out


def save_signals(sigs):
    with open(PAPER, "w", encoding="utf-8") as f:
        for s in sigs:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")


def cmd_add(args):
    import sys as _s
    _s.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from pari_lib import ALLOWED_HORIZONS, CIRCUIT_STAKE, tour_circuit
    g = lambda k, d="": args[args.index(k) + 1] if k in args else d
    horizon = g("--horizon", "match")
    if horizon not in ALLOWED_HORIZONS:
        print(f"запрет секунд: горизонт '{horizon}' вне {ALLOWED_HORIZONS} — ставка отклонена")
        return
    tour = g("--tour")
    circuit = tour_circuit(tour)
    stake = g("--stake")
    stake = float(stake) if stake else CIRCUIT_STAKE.get(
        "ASYM" if g("--trigger").startswith("ASYM") else circuit, 1.0)
    plan = g("--plan")
    if not plan:
        print("внимание: нет точки выхода (--plan). записано, но без плана это мусор")
        plan = "-"
    sig = {"ts": __import__("time").time(), "eid": str(g("--eid")),
           "match": g("--match"), "trigger": g("--trigger"),
           "market": g("--market"), "side": g("--side"),
           "odds": float(g("--odds", "0") or 0),
           "stake": stake, "status": "open",
           "horizon": horizon, "circuit": circuit, "plan": plan,
           "tour": tour}
    sigs = load_signals()
    if any(s["eid"] == sig["eid"] and s["trigger"] == sig["trigger"]
           and s["status"] == "open" for s in sigs):
        print("уже есть открытый такой сигнал")
        return
    sigs.append(sig)
    save_signals(sigs)
    print(f"записан: {sig['trigger']} {sig['match']} {sig['market']} @{sig['odds']}")


RET_RE = None


def is_retirement(final, comment=""):
    import re as _re
    global RET_RE
    if RET_RE is None:
        RET_RE = _re.compile(r"ret\b|w\.?\s*o\.?|walkover|отказ|сня|default|def\.|травм",
                             _re.I)
    if comment and RET_RE.search(comment):
        return True
    # недоигранный финал: никто не взял 2 сета (BO3) — почти наверняка снятие
    try:
        w1 = sum(1 for s in final if int(str(s).split("-")[0]) > int(str(s).split("-")[1]))
        w2 = sum(1 for s in final if int(str(s).split("-")[1]) > int(str(s).split("-")[0]))
    except (ValueError, IndexError, AttributeError):
        return True
    if w1 < 2 and w2 < 2:
        return True
    return False


def valid_set_score(a, b):
    try:
        a, b = int(a), int(b)
    except (ValueError, TypeError):
        return False
    if (a == 6 and b <= 4) or (b == 6 and a <= 4):
        return True
    return (a, b) in ((7, 5), (5, 7), (7, 6), (6, 7))
    """final: ['7-6','6-2'] -> 'p1'/'p2'/None."""
    w1 = w2 = 0
    for s in fin:
        try:
            a, b = s.split("-")
            if int(a) > int(b):
                w1 += 1
            elif int(b) > int(a):
                w2 += 1
        except (ValueError, AttributeError):
            pass
    if w1 == w2:
        return None
    return "p1" if w1 > w2 else "p2"


def settle_from_files(files):
    """Сверка открытых сигналов по finals + set_scores из файлов."""
    sigs = load_signals()
    opened = [s for s in sigs if s["status"] == "open"]
    if not opened:
        print("открытых нет")
        return
    # собрать исходы: eid -> {winner, set_totals:{si:total}}
    outcomes = {}
    for path in files:
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            for f in d.get("finals", []):
                eid = str(f.get("eid"))
                o = outcomes.setdefault(eid, {})
                o["winner"] = match_winner_from_final(f.get("final", []))
                o["final"] = f.get("final", [])
                o["ret"] = is_retirement(f.get("final", []), f.get("comment", ""))
    n = 0
    for s in opened:
        o = outcomes.get(str(s["eid"]))
        if not o or not o.get("winner"):
            continue
        if o.get("ret"):
            s["status"] = "void"
            s["note"] = "retirement/unfinished"
            n += 1
            continue
        mk = s.get("market", "")
        win = None
        if mk.startswith("П") or "победа" in mk.lower() or "1x2" in mk.lower():
            side = s.get("side", "")
            w = o["winner"]
            win = (side == w) or (side == "p1" and w == "p1") or (side == "p2" and w == "p2")
        if win is None:
            continue
        s["status"] = "win" if win else "lose"
        s["profit"] = round(s["stake"] * (s["odds"] - 1), 2) if win else round(-s["stake"], 2)
        s["settled"] = o.get("final", [])
        n += 1
    save_signals(sigs)
    print(f"сверено: {n}")


def cmd_void(files):
    """Открытые сигналы по матчам, пропавшим из ленты 2+ часа (или старше суток) -> void."""
    import time as _t
    import glob as _g
    paths = []
    for f in files:
        paths.extend(_g.glob(f) or [f])
    last_seen = {}
    now = _t.time()
    for path in paths:
        try:
            fh = open(path, encoding="utf-8")
        except OSError:
            continue
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = d.get("ts", 0)
            for m in d.get("matches", []):
                last_seen[str(m.get("eid"))] = ts
        fh.close()
    sigs = load_signals()
    n = 0
    for s in sigs:
        if s["status"] != "open":
            continue
        ls = last_seen.get(str(s["eid"]), 0)
        if (ls and now - ls > 7200) or (now - s.get("ts", now) > 86400):
            s["status"] = "void"
            n += 1
    save_signals(sigs)
    print(f"void: {n}")


def cmd_report():
    sigs = load_signals()
    closed = [s for s in sigs if s["status"] in ("win", "lose")]
    opened = [s for s in sigs if s["status"] == "open"]
    print(f"сигналов: {len(sigs)} (открыто {len(opened)}, закрыто {len(closed)})")
    if not closed:
        return
    w = sum(1 for s in closed if s["status"] == "win")
    pl = round(sum(s.get("profit", 0) for s in closed), 2)
    print(f"проход: {w}/{len(closed)} ({100 * w / len(closed):.0f}%), P/L: {pl:+}u")
    from collections import Counter
    for title, key in (("по триггерам", "trigger"), ("по контурам", "circuit"),
                       ("по горизонтам", "horizon")):
        by_k = Counter()
        for s in closed:
            by_k[(s.get(key) or "?", s["status"])] += 1
        print(f" {title}:")
        for t in sorted(set(t for t, _ in by_k)):
            ww, ll = by_k.get((t, "win"), 0), by_k.get((t, "lose"), 0)
            ppl = round(sum(s.get("profit", 0) for s in closed
                            if (s.get(key) or "?") == t), 2)
            print(f"   {t}: {ww}W-{ll}L  {ppl:+}u")


def cmd_auto(files):
    """Автосигналы из триггеров pari_patterns + сверка."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from pari_patterns import build_timelines
    line_files = [f for f in files if "line" in f]
    TL = build_timelines(line_files)
    sigs = load_signals()
    added = 0
    # T7: сухой первый гейм -> сет меньше 9.5
    for eid, t in TL.items():
        by_set = {}
        for g in t["games"]:
            by_set.setdefault(g["set"], []).append(g)
        for si, gs in by_set.items():
            if not gs:
                continue
            if gs[0]["pts_end"] in (("40", "00"), ("40", "15"),
                                    ("00", "40"), ("15", "40")):
                if any(s["eid"] == eid and s["trigger"] == "T7"
                       and s.get("set") == si for s in sigs):
                    continue
                sigs.append({"ts": gs[0]["ts"], "eid": eid,
                             "match": f"{t['p1']} - {t['p2']}",
                             "trigger": "T7", "set": si,
                             "market": f"тотал сета {si} меньше 9.5",
                             "side": "under9.5", "odds": 1.85,
                             "stake": 1, "status": "open"})
                added += 1
    save_signals(sigs)
    print(f"автосигналов T7: {added}")
    # сверка under9.5 по итогам сетов
    n = 0
    for s in sigs:
        if s["status"] != "open" or s.get("side") != "under9.5":
            continue
        t = TL.get(str(s["eid"]), {})
        fin = t.get("sets_final", {}).get(s.get("set"))
        if not fin or not valid_set_score(fin[0], fin[1]):
            continue
        total = fin[0] + fin[1]
        win = total <= 9
        s["status"] = "win" if win else "lose"
        s["profit"] = round(s["stake"] * (s["odds"] - 1), 2) if win \
            else round(-s["stake"], 2)
        s["settled"] = list(fin)
        n += 1
    save_signals(sigs)
    print(f"сверено T7: {n}")
    # ASYM-TB: тай-брейк идёт (6-6 + очки) -> андердог берёт сет
    tb_added = tb_settled = 0
    for path in line_files:
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            for m in d.get("matches", []):
                ss = m.get("set_scores") or []
                game = m.get("game") or []
                if not ss or not game:
                    continue
                try:
                    last = (int(ss[-1][0]), int(ss[-1][1]))
                    pts = (str(game[0]), str(game[1]))
                except (ValueError, TypeError, IndexError):
                    continue
                if last != (6, 6):
                    continue
                if not all(p.isdigit() and int(p) <= 12 for p in pts):
                    continue
                eid, si = str(m.get("eid")), len(ss)
                if any(s["eid"] == eid and s["trigger"] == "ASYM-TB"
                       and s.get("set") == si for s in sigs):
                    continue
                o1 = o2 = None
                for o in m.get("odds", []):
                    if o.get("f") == 921:
                        o1 = o.get("v")
                    elif o.get("f") == 923:
                        o2 = o.get("v")
                if not o1 or not o2:
                    continue
                dog = "p1" if o1 > o2 else "p2"
                sigs.append({"ts": d["ts"], "eid": eid,
                             "match": f"{m.get('p1')} - {m.get('p2')}",
                             "trigger": "ASYM-TB", "set": si,
                             "market": f"сет {si} выигрывает андердог",
                             "side": dog, "odds": max(o1, o2),
                             "stake": 1.5, "status": "open",
                             "horizon": "set", "circuit": "ASYM",
                             "tour": m.get("tour"),
                             "plan": "держать до конца тай-брейка"})
                tb_added += 1
    save_signals(sigs)
    print(f"автосигналов ASYM-TB: {tb_added}")
    for s in sigs:
        if s["status"] != "open" or s.get("trigger") != "ASYM-TB":
            continue
        t = TL.get(str(s["eid"]), {})
        fin = t.get("sets_final", {}).get(s.get("set"))
        if not fin or not valid_set_score(fin[0], fin[1]):
            continue
        w = "p1" if fin[0] > fin[1] else "p2"
        win = (w == s["side"])
        s["status"] = "win" if win else "lose"
        s["profit"] = round(s["stake"] * (s["odds"] - 1), 2) if win \
            else round(-s["stake"], 2)
        s["settled"] = list(fin)
        tb_settled += 1
    save_signals(sigs)
    print(f"сверено ASYM-TB: {tb_settled}")
    # M: марковская цена гейма vs кэф (edge>12%)
    import sys as _s2
    _s2.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from pari_lib import game_fair, serve_point_prob, tour_circuit, CIRCUIT_STAKE
    from pari_lib import match_fair as _mf
    from pari_lib import game_fair as _gf
    m_added = m_settled = 0
    running = {}
    first_win = {}
    for path in line_files:
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            for m in d.get("matches", []):
                eid = str(m.get("eid"))
                cur = running.setdefault(eid, {})
                for o in m.get("odds", []):
                    cur[(o.get("f"), o.get("pt"))] = o
                if eid not in first_win:
                    o1 = o2 = None
                    for o in m.get("odds", []):
                        if o.get("f") == 921:
                            o1 = o.get("v")
                        elif o.get("f") == 923:
                            o2 = o.get("v")
                    if o1 and o2:
                        first_win[eid] = (o1, o2)
                # M: марковская цена МАТЧА vs кэф (реальные 921/923 из running)
                ss = m.get("set_scores") or []
                srv = str(m.get("serve") or "")
                if not ss or srv not in ("1", "2"):
                    continue
                if any(s["eid"] == eid and s["trigger"] == "M" for s in sigs):
                    continue
                try:
                    sw = [0, 0]
                    for g in ss[:-1]:
                        a, b = int(g[0]), int(g[1])
                        if (a == 6 and b <= 4) or (a, b) in ((7, 5), (7, 6)):
                            sw[0] += 1
                        elif (b == 6 and a <= 4) or (a, b) in ((5, 7), (6, 7)):
                            sw[1] += 1
                    a, b = int(ss[-1][0]), int(ss[-1][1])
                except (ValueError, TypeError, IndexError):
                    continue
                stats = m.get("stats")
                fw = first_win.get(eid, (None, None))
                p1 = serve_point_prob(stats, m.get("tour"), "p1",
                                      fw[0] if isinstance(fw, tuple) else None)
                p2 = serve_point_prob(stats, m.get("tour"), "p2",
                                      fw[1] if isinstance(fw, tuple) else None)
                h1, h2 = _gf(p1, (0, 0)), _gf(p2, (0, 0))
                if not h1 or not h2:
                    continue
                fair1 = _mf(tuple(sw), (a, b), int(srv), h1, h2)
                o1 = o2 = None
                for (ff, pt), o in cur.items():
                    if ff == 921:
                        o1 = o.get("v")
                    elif ff == 923:
                        o2 = o.get("v")
                if not o1 or not o2:
                    continue
                cand = None
                if fair1 - 1 / o1 > 0.12:
                    cand = ("p1", o1, fair1)
                elif (1 - fair1) - 1 / o2 > 0.12:
                    cand = ("p2", o2, round(1 - fair1, 3))
                if not cand:
                    continue
                side, odd, fair = cand
                circ = tour_circuit(m.get("tour"))
                sigs.append({"ts": d["ts"], "eid": eid,
                             "match": f"{m.get('p1')} - {m.get('p2')}",
                             "trigger": "M",
                             "market": f"победа {side}",
                             "side": side, "odds": odd,
                             "fair": fair, "stake": CIRCUIT_STAKE.get(circ, 0.5),
                             "status": "open", "horizon": "match",
                             "circuit": circ, "tour": m.get("tour"),
                             "plan": "держать до конца матча"})
                m_added += 1
    save_signals(sigs)
    print(f"автосигналов M: {m_added}")
    for s in sigs:
        if s["status"] != "open" or s.get("trigger") != "M":
            continue
        t = TL.get(str(s["eid"]), {})
        # исход матча из финалов TL
        fin = None
        for si in sorted(t.get("sets_final", {}).keys()):
            fin = t["sets_final"][si]
        w = None
        if fin:
            try:
                w1 = sum(1 for k, v in t["sets_final"].items()
                         if int(v[0]) > int(v[1]))
                w2 = sum(1 for k, v in t["sets_final"].items()
                         if int(v[1]) > int(v[0]))
                if w1 >= 2 or w2 >= 2:
                    w = "p1" if w1 > w2 else "p2"
            except (ValueError, TypeError):
                pass
        if w is None:
            continue
        hit = (w == s["side"])
        s["status"] = "win" if hit else "lose"
        s["profit"] = round(s["stake"] * (s["odds"] - 1), 2) if hit \
            else round(-s["stake"], 2)
        m_settled += 1
    save_signals(sigs)
    print(f"сверено M: {m_settled}")


def main():
    args = sys.argv[1:]
    if not args or "-h" in args:
        print(__doc__)
        return
    if args[0] == "add":
        cmd_add(args[1:])
    elif args[0] == "settle":
        files = args[args.index("--files") + 1:] if "--files" in args else []
        settle_from_files(files)
    elif args[0] == "auto":
        files = args[args.index("--files") + 1:] if "--files" in args else []
        cmd_auto(files)
    elif args[0] == "report":
        cmd_report()
    elif args[0] == "void":
        files = args[args.index("--files") + 1:] if "--files" in args else []
        cmd_void(files)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
