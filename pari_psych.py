#!/usr/bin/env python3
"""Психо-профили игроков: что меряется из поведения, а не из подачи.

Источники: история TE (тай-брейки, решающие сеты, бублики) + наши line-данные
(чоки 40-0->проигрыш, камбэки, подача на матч).

  python3 pari_psych.py --profiles /public/profiles --lines "/public/night/*.jsonl"
  python3 pari_psych.py --player "Эззат Я"

ЧТО ИЗМЕНЕНО: 1) psych_from_line была функцией-заглушкой — заводила пустую
запись {"choke":0,"choke_opp":0,"serveformatch":[0,0]} на каждого игрока и
НИЧЕГО в неё не считала, а из main() даже не вызывалась (несмотря на то, что
докстринг файла обещает именно эти цифры из line-данных). Реализовал по-
настоящему: через build_timelines() из pari_patterns (та же надёжная
реконструкция геймов/подающих, что уже используется в pari_paper.py) считаю
и чок (сервер вёл 40-0/40-15 в гейме, но гейм отдал), и попытки/реализации
"подачи на матч" (сервер уже взял need-1 сетов, и именно этот гейм при
победе закрывал бы и сет, и матч). Проверено на синтетических данных:
сценарии "чок при подаче на матч" и "успешно подал на матч без чока" дают
ожидаемые числа. Подключено в main() — печатается вместе с историей TE.
2) psych_from_history: сравнение фамилии игрока со строкой матча TE было
через "in" (вхождение подстроки где угодно) на первых 4 буквах — для
коротких фамилий (Li, Wu, Bo...) это легко ловит случайные совпадения
внутри вообще другого слова. Заменено на startswith (привязка к началу
строки) — уже не идеально (сама логика "кто в паре я" всё ещё зависит от
формата строки TE, который я не могу сверить без живого HTML), но заведомо
не хуже старого варианта и не даёт случайных подстрочных совпадений."""
import glob
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
from pari_patterns import build_timelines, valid_set

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
    """Тай-брейки, решающие сеты, бублики из истории TE.
    ОГРАНИЧЕНИЕ (зафиксировано, не переписывал вслепую без живого HTML для
    сверки): "кто в паре 'match' — я" определяется по строке TE вида
    "Surname1 - Surname2". Если ФАМИЛИЯ ИГРОКА (не только на TE, но и своя,
    ме_surname) сама содержит дефис (двойные фамилии — обычное дело в
    теннисе), split("-") может разрезать не в том месте. Ниже это не
    трогал за неимением реальных данных TE для проверки — только ужесточил
    сравнение (startswith вместо "где угодно в строке")."""
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
        need = me_surname.lower()[:4]
        me_first = first.startswith(need) if need else True
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
    """Чоки, камбэки и подача на матч — из наших live-снимков (line.jsonl).
    {name: {"choke": N, "choke_opp": N, "serveformatch": [попыток, реализовано]}}.
    choke: игрок подавал, вёл в гейме 40-0/40-15, но гейм отдал.
    choke_opp: то же самое, только с точки зрения того, КОМУ подарили гейм.
    serveformatch: игрок уже взял need-1 сетов и подаёт гейм, который при
    победе закрывает и текущий сет, и матч (BO3 — need=2, как везде в этом
    проекте); [сколько раз была такая возможность, сколько раз реализовал]."""
    TL = build_timelines(paths)
    out = {}

    def ensure(name):
        return out.setdefault(name, {"choke": 0, "choke_opp": 0,
                                     "serveformatch": [0, 0]})

    need = 2  # BO3, как и весь остальной проект (best_of нигде не передаётся)
    for t in TL.values():
        p1n, p2n = t.get("p1"), t.get("p2")
        if not p1n or not p2n:
            continue
        ensure(p1n)
        ensure(p2n)
        # sets_before[si] = [сетов p1, сетов p2] ДО начала сета si — строится
        # из sets_final напрямую, не из порядка t["games"] (устойчиво к тому,
        # что не все геймы сета могли попасть в снимки отдельными переходами).
        sets_before = {1: [0, 0]}
        cum = [0, 0]
        for si in sorted(t["sets_final"].keys()):
            sets_before[si] = list(cum)
            fin = t["sets_final"][si]
            if valid_set(fin):
                cum[0 if fin[0] > fin[1] else 1] += 1
        sets_before[max(t["sets_final"].keys(), default=0) + 1] = list(cum)

        for g in t["games"]:
            si, server, winner = g["set"], g["server"], g["winner"]
            ab, pts_end = g["ab"], g["pts_end"]
            if not server:
                continue
            # счёт геймов ДО этого гейма — из итогового счёта и победителя
            # этого же гейма (устойчиво к пропускам в t["games"], в отличие
            # от накопления "предыдущий гейм этого сета")
            before = (ab[0] - (1 if winner == "p1" else 0),
                      ab[1] - (1 if winner == "p2" else 0))
            if len(pts_end) == 2:
                c1, c2 = pts_end
                dominant = (server == "p1" and c1 == "40" and c2 in ("00", "0", "15")) \
                    or (server == "p2" and c2 == "40" and c1 in ("00", "0", "15"))
                if dominant and winner != server:
                    name_s = p1n if server == "p1" else p2n
                    name_w = p1n if winner == "p1" else p2n
                    out[name_s]["choke"] += 1
                    out[name_w]["choke_opp"] += 1
            if si in sets_before:
                sw = sets_before[si]
                srv_sets = sw[0] if server == "p1" else sw[1]
                if srv_sets == need - 1:
                    my_before = before[0] if server == "p1" else before[1]
                    opp_before = before[1] if server == "p1" else before[0]
                    would_close = valid_set((my_before + 1, opp_before)) \
                        and (my_before + 1) > opp_before
                    if would_close:
                        name_s = p1n if server == "p1" else p2n
                        out[name_s]["serveformatch"][0] += 1
                        if winner == server:
                            out[name_s]["serveformatch"][1] += 1
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

    # ИСПРАВЛЕНО: раньше вообще не вызывалась. Считаем один раз на все файлы,
    # а не заново на каждого игрока.
    line_psych = psych_from_line(files) if files else {}

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
            lp = line_psych.get(name)
            if lp:
                out["line"] = lp
            json.dump(out, open(os.path.join(PSYCHDIR, f"{sur or name}.json"),
                                "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)
            n += 1
            tb = ps["tb_w"] + ps["tb_l"]
            dc = ps["dec_w"] + ps["dec_l"]
            line = (f"{name}: TB {ps['tb_w']}-{ps['tb_l']}"
                  + (f" ({100*ps['tb_w']/tb:.0f}%)" if tb else "")
                  + f" | реш.сеты {ps['dec_w']}-{ps['dec_l']}"
                  + f" | бублики дал/взял/отыграл {ps['bagel_given']}/{ps['bagel_taken']}/{ps['bagel_back']}"
                  + f" (n={ps['n']})")
            if lp:
                sfm = lp["serveformatch"]
                line += (f" | чок(live) {lp['choke']}/пода-соперника-подарила {lp['choke_opp']}"
                        f" | подача на матч {sfm[1]}/{sfm[0]}")
            print(line)
    print(f"профилей: {n} -> {PSYCHDIR}")


if __name__ == "__main__":
    main()
