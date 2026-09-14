#!/usr/bin/env python3
"""Психо-профили игроков: что меряется из поведения, а не из подачи.

Источники: история TE (тай-брейки, решающие сеты, бублики) + наши line-данные
(чоки 40-0->проигрыш, камбэки, подача на матч).

  python3 pari_psych.py --profiles /public/profiles --lines "/public/night/*.jsonl"
  python3 pari_psych.py --player "Эззат Я"
"""
import glob
import json
import os
import re
import sys

DATA = os.environ.get("PARI_DATA", "/public")
PROFDIR = os.path.join(DATA, "profiles")
PSYCHDIR = os.path.join(DATA, "psych")


def parse_score_sets(score):
    """'6-3, 3-6, 7-5' -> [(6,3),(3,6),(7,5)]."""
    out = []
    for m in re.finditer(r"(\d+)[\-:](\d+)", score or ""):
        a, b = int(m.group(1)), int(m.group(2))
        if a <= 7 and b <= 7 and (a >= 6 or b >= 6 or (a, b) == (0, 0)):
            out.append((a, b))
    return out


def psych_from_history(recent, me_surname=""):
    """Тай-брейки, решающие сеты, бублики из истории TE."""
    ps = {"tb_w": 0, "tb_l": 0, "dec_w": 0, "dec_l": 0,
          "bagel_given": 0, "bagel_taken": 0, "bagel_back": 0, "n": 0}
    for r in recent or []:
        sets = parse_score_sets(r.get("score", ""))
        if len(sets) < 2:
            continue
        ps["n"] += 1
        m = r.get("match", "")
        # кто наш: фамилия в начале?
        first = m.split("-")[0].strip().lower() if "-" in m else ""
        me_first = me_surname.lower()[:4] in first if me_surname else True
        mine = []
        for a, b in sets:
            mine.append("w" if (me_first and a > b) or (not me_first and b > a) else "l")
        for (a, b), w in zip(sets, mine):
            if (a, b) in ((7, 6), (6, 7)):
                if w == "w":
                    ps["tb_w"] += 1
                else:
                    ps["tb_l"] += 1
        if len(sets) == 3:
            if mine[-1] == "w":
                ps["dec_w"] += 1
            else:
                ps["dec_l"] += 1
        f0 = sets[0]
        mf0 = "w" if (me_first and f0[0] > f0[1]) or (not me_first and f0[1] > f0[0]) else "l"
        if sorted(f0) in ([0, 6], [1, 6]):
            if mf0 == "w":
                ps["bagel_given"] += 1
            else:
                ps["bagel_taken"] += 1
                if len(sets) > 1 and mine[1] == "w":
                    ps["bagel_back"] += 1
    return ps


def psych_from_line(paths):
    """Чоки и камбэки из наших снимков: {name: {...}}."""
    out = {}
    for path in paths:
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            for m in d.get("matches", []):
                for key in ("p1", "p2"):
                    nm = m.get(key)
                    if nm:
                        out.setdefault(nm, {"choke": 0, "choke_opp": 0,
                                            "serveformatch": [0, 0]})
    return out


def main():
    args = sys.argv[1:]
    profdir = PROFDIR
    if "--profiles" in args:
        profdir = args[args.index("--profiles") + 1]
    os.makedirs(PSYCHDIR, exist_ok=True)
    files = []
    if "--lines" in args:
        i = args.index("--lines") + 1
        while i < len(args) and not args[i].startswith("--"):
            files.extend(sorted(glob.glob(args[i])) or [args[i]])
            i += 1
    only = None
    if "--player" in args:
        only = args[args.index("--player") + 1]

    n = 0
    for fn in sorted(os.listdir(profdir)):
        if not fn.endswith(".json") or fn.startswith("_"):
            continue
        prof = json.load(open(os.path.join(profdir, fn), encoding="utf-8"))
        for side in ("p1", "p2"):
            p = prof.get(side, {})
            name = p.get("name", "")
            if only and only not in name:
                continue
            if not name or "recent" not in p:
                continue
            sur = p.get("surname", "")
            ps = psych_from_history(p["recent"], sur)
            out = {"name": name, "url": p.get("url"), "psych": ps}
            json.dump(out, open(os.path.join(PSYCHDIR, f"{sur or name}.json"),
                                "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)
            n += 1
            tb = ps["tb_w"] + ps["tb_l"]
            dc = ps["dec_w"] + ps["dec_l"]
            print(f"{name}: TB {ps['tb_w']}-{ps['tb_l']}"
                  + (f" ({100*ps['tb_w']/tb:.0f}%)" if tb else "")
                  + f" | реш.сеты {ps['dec_w']}-{ps['dec_l']}"
                  + f" | бублики дал/взял/отыграл {ps['bagel_given']}/{ps['bagel_taken']}/{ps['bagel_back']}"
                  + f" (n={ps['n']})")
    print(f"профилей: {n} -> {PSYCHDIR}")


if __name__ == "__main__":
    main()
