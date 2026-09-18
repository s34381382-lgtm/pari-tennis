#!/usr/bin/env python3
"""Детектор обратных брейков (и база под другие микропаттерны).

Реконструирует геймы с подающими из снапшотов (node: подающий чередуется
строго каждый гейм; сервер текущего гейма есть в шапке каждого снимка).
Брейк = гейм выиграл не подающий. Обратный брейк = следующим геймом
отыгрывается сразу.

  python3 pari_patterns.py --files sumann_messis.jsonl ezzat_mdlulva.jsonl line.jsonl

ЧТО ИЗМЕНЕНО: 1) trigger_bagel теперь фильтрует sets_final через valid_set()
перед тем, как включать тотал сета в статистику — раньше сет, ещё не
завершённый (например данные оборвались посреди него), мог попасть в
base_totals/dry_totals как обычный сыгранный сет и смещать среднюю. Ровно та
же защита (valid_set) в этом файле уже применялась в analyze_set_patterns,
но НЕ в trigger_bagel — комментарий в коде даже обещал "фильтр валидности
ниже", но филь ниже фактически не было. 2) server_of теперь берёт голосование
большинства по всем найденным якорям чётности сервера в сете, а не первый
попавшийся (порядок dict) — один битый якорь раньше мог перевернуть
подающего для ВСЕХ геймов сета. 3) is_women — убрана рассинхронизация с
неиспользуемым модульным WOMEN_RE (внутри функция гоняла свою, чуть другую
регулярку). 4) Отчёты триггеров 2/3/7 теперь печатают n рядом с процентом
и метят маленькие выборки — раньше честность/переобучение было невозможно
оценить по одному выводу без пересчёта n в уме."""
import json
import re
import sys

WOMEN_RE = re.compile(r"WTA|WOMEN|ЖЕН", re.I)


def is_women(tour):
    if not tour:
        return False
    t = tour.upper()
    if WOMEN_RE.search(t):
        return True
    if re.search(r"ATP|MEN|МУЖЧИН", t, re.I) and not WOMEN_RE.search(t):
        return False
    return False


def sex_weight(tour):
    """Вес 'женскости' тура: 1.0 Ж, 0.0 М, 0.5 неизвестно (юниоры/ITF-миксты).
    18.09: раньше неизвестные молча падали в мужскую базу p=0.63 —
    перекос. Теперь вызыватель блендит Ж/М таблицы по весу."""
    if not tour:
        return 0.5
    t = tour.upper()
    if WOMEN_RE.search(t):
        return 1.0
    if re.search(r"ATP|MEN|МУЖЧИН", t, re.I):
        return 0.0
    return 0.5


def games_from_browser(path):
    """Из глубокого jsonl (pari_obzor): [(winner, server)] по порядку геймов.
    Сервер есть не в каждом снимке -> якорим чётность по тем что есть."""
    snaps = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        h = d.get("header", {})
        ss = h.get("set_scores") or []
        cur = h.get("current_games") or h.get("game_points")
        if not ss or not cur:
            continue
        try:
            a, b = int(ss[-1][0]), int(ss[-1][1])
        except (ValueError, IndexError, TypeError):
            continue
        snaps.append({"set": len(ss), "a": a, "b": b,
                      "srv": h.get("server"), "p1": h.get("p1", "")})
    # якоря: (set, total) -> server name
    anchors = {}
    for s in snaps:
        if s["srv"]:
            anchors[(s["set"], s["a"] + s["b"])] = s["srv"]
    games = []
    prev = None
    for s in snaps:
        cur = (s["set"], s["a"], s["b"])
        if prev and prev[0] == s["set"] and (s["a"], s["b"]) != (prev[1], prev[2]):
            pa, pb = prev[1], prev[2]
            if (s["a"] == pa + 1) != (s["b"] == pb + 1):
                winner = "p1" if s["a"] == pa + 1 else "p2"
                srv = resolve_server(anchors, s, pa + pb, s["p1"])
                if srv:
                    games.append((winner, srv))
        prev = cur
    return games


def resolve_server(anchors, s, finished_total, p1):
    """Сервер завершённого гейма по якорям чётности."""
    for (st, tot), name in anchors.items():
        if st != s["set"]:
            continue
        # чётность: геймы чередуются
        if (tot - finished_total) % 2 == 0:
            return "p1" if name == p1 else "p2"
        else:
            return "p2" if name == p1 else "p1"
    return None


def games_from_line(path, women_only=False):
    """Из line.jsonl (API): то же самое."""
    games = []
    prev = {}
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        for m in d.get("matches", []):
            if women_only and not is_women(m.get("tour", "")):
                continue
            eid = str(m.get("eid"))
            ss = m.get("set_scores") or []
            cur = m.get("current_games") or m.get("game")
            srv = m.get("serve")
            if not ss or not cur:
                continue
            try:
                a, b = int(ss[-1][0]), int(ss[-1][1])
            except (ValueError, IndexError, TypeError):
                continue
            key = (eid, len(ss))
            p = prev.get(key)
            if p and (a, b) != (p[0], p[1]) and srv:
                if (a == p[0] + 1) != (b == p[1] + 1):
                    winner = "p1" if a == p[0] + 1 else "p2"
                    server = "p2" if int(srv) == 1 else "p1"
                    games.append((winner, server, m.get("tour", "")))
            prev[key] = (a, b)
    return games


def report(games, label):
    n = len(games)
    breaks = [(w, s) for w, s, *_ in [g if len(g) == 3 else (g[0], g[1], "") for g in games]
              if w != s]
    print(f"--- {label}: геймов {n}, брейков {len(breaks)} "
          f"({100 * len(breaks) / n:.0f}%)" if n else f"--- {label}: нет данных")
    if not games:
        return
    # обратные брейки: брейк + следующий гейм выиграл тот, кого только что брейканули
    seq = [(w, s) for w, s, *_ in
           [g if len(g) == 3 else (g[0], g[1], "") for g in games]]
    bb = sum(1 for i in range(len(seq) - 1)
             if seq[i][0] != seq[i][1] and seq[i + 1][0] == seq[i][1]
             and seq[i + 1][0] != seq[i + 1][1])
    nb = sum(1 for i in range(len(seq) - 1) if seq[i][0] != seq[i][1])
    print(f"    обратных брейков: {bb}/{nb} "
          f"({100 * bb / nb:.0f}%)" if nb else "    брейков нет")


def main():
    args = sys.argv[1:]
    women_only = "--women" in args
    files = []
    if "--files" in args:
        i = args.index("--files") + 1
        while i < len(args) and not args[i].startswith("--"):
            files.append(args[i])
            i += 1
    if not files:
        print("укажи --files ... [--women]")
        return
    all_games = []
    for f in files:
        if "line" in f:
            g = games_from_line(f, women_only)
        else:
            g = [(w, s) for w, s in games_from_browser(f)]
        report(g, f + (" [жен]" if women_only and "line" in f else ""))
        all_games += [(w, s, "") for w, s in g] if g and len(g[0]) == 2 else g
    print()
    report(all_games, "ИТОГО" + (" [жен]" if women_only else ""))


# ================= ТАЙМЛАЙН (проход 1) =================
def build_timelines(paths):
    """eid -> {tour,p1,p2, games:[{set,winner,server,dbl,pts_end,ts}],
    sets_final:{si:(a,b)}, gaps:[...]}. Сервер распространяется по чётности."""
    TL, prev, last_seen = {}, {}, {}

    def get(eid):
        return TL.setdefault(eid, {"games": [], "sets_final": {}, "gaps": [],
                                   "pauses": [], "max_gap": 0,
                                   "p1": "", "p2": "", "tour": "",
                                   "srv_state": {}})

    for path in paths:
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            ts = d.get("ts", 0)
            live_now = set()
            for m in d.get("matches", []):
                eid = str(m.get("eid"))
                live_now.add(eid)
                t = get(eid)
                # 18.09: тур/имена обновляем ВСЕГДА, если есть (было только
                # при p1 — в ленте p1 пуст в 75% снапшотов, 84% TL без тура,
                # вся разбивка по контурам врала).
                if m.get("tour"):
                    t["tour"] = m.get("tour")
                if m.get("p1"):
                    t["p1"], t["p2"] = m["p1"], m.get("p2")
                last_seen[eid] = (ts, m)
                ss = m.get("set_scores") or []
                game = m.get("game")
                srv = m.get("serve")
                st = (m.get("stats") or {}).get("двойные ошибки", {})
                if not ss:
                    continue
                try:
                    si = len(ss)
                    a, b = int(ss[-1][0]), int(ss[-1][1])
                    d1, d2 = int(st.get("c1", 0) or 0), int(st.get("c2", 0) or 0)
                except (ValueError, TypeError):
                    continue
                # якорь сервера: номер гейма в сете -> кто подавал
                if srv:
                    t["srv_state"][(si, a + b)] = int(srv)
                p = prev.get(eid)
                if p:
                    gap = ts - p["ts"]
                    if gap > t["max_gap"]:
                        t["max_gap"] = gap
                    # сетевая дыра (нет снимков) — НЕ пауза, просто пропуск
                    if gap > 240 and (si, a, b) == (p["si"], p["a"], p["b"]):
                        t["gaps"].append({"gap": gap, "ts": ts})
                # настоящая пауза: счёт стоит, а снимки идут (MTO/туалет/перерыв)
                sc_key = (si, a, b, tuple(game) if game else ())
                if p and gap <= 150 and sc_key == p.get("sc"):
                    if ts - p.get("static_since", ts) > 300:
                        if not t["pauses"] or t["pauses"][-1]["ts_end"] < p.get("static_since", 0):
                            t["pauses"].append({"dur": ts - p["static_since"],
                                                "at": sc_key, "ts_end": ts})
                def _pair(x):
                    try:
                        return (int(x[0]), int(x[1]))
                    except (ValueError, TypeError, IndexError):
                        return None

                if p and (si, a, b) != (p["si"], p["a"], p["b"]):
                    if si == p["si"] and ((a == p["a"] + 1) != (b == p["b"] + 1)):
                        winner = "p1" if a == p["a"] + 1 else "p2"
                        server = server_of(t, si, p["a"] + p["b"])
                        t["games"].append({
                            "set": si, "winner": winner, "server": server,
                            "ab": (a, b),
                            "dbl": (d1 - p["d1"], d2 - p["d2"]),
                            "pts_end": tuple(p.get("pts") or ()), "ts": ts})
                    elif si == p["si"] + 1:
                        # финал прошлого сета — из СВЕЖЕГО снимка (провайдер правит задним числом)
                        for k in range(1, si):
                            try:
                                pr = _pair(ss[k - 1])
                            except IndexError:
                                pr = None
                            if pr:
                                t["sets_final"][k] = pr
                prev[eid] = {"si": si, "a": a, "b": b, "ts": ts,
                             "d1": d1, "d2": d2,
                             "pts": tuple(game) if game else (),
                             "sc": sc_key,
                             "static_since": p.get("static_since", ts)
                             if p and sc_key == p.get("sc") else ts}
            # матчи, исчезнувшие из live = закончились: финалы из последнего снимка
            for eid in list(prev.keys()):
                if eid not in live_now and eid in TL:
                    _ts, m = last_seen.get(eid, (0, {}))
                    _ss = m.get("set_scores") or []
                    for k in range(1, len(_ss) + 1):
                        try:
                            pr = (int(_ss[k - 1][0]), int(_ss[k - 1][1]))
                        except (ValueError, TypeError, IndexError):
                            continue
                        # последний сет в списке при конце матча — сыгран полностью?
                        # берём как есть; фильтр валидности ниже
                        TL[eid]["sets_final"][k] = pr
                    del prev[eid]
    return TL


def server_of(t, si, game_total):
    """Сервер гейма N в сете по якорям чётности (чередование строгое).
    ИСПРАВЛЕНО: раньше возвращался результат ПЕРВОГО найденного якоря для
    этого сета (порядок обхода dict — по сути произвольный, не по времени
    и не по надёжности). Один битый/шумный якорь (неверный serve в одном
    снимке) переворачивал подающего для ВСЕХ геймов сета целиком. Теперь —
    голосование большинства по всем якорям сета; при единственном якоре
    (типичный случай) поведение идентично старому."""
    from collections import Counter
    st = t.get("srv_state", {})
    votes = Counter()
    for (s, tot), who in st.items():
        if s != si:
            continue
        w = who if (tot - game_total) % 2 == 0 else 3 - who
        votes[w] += 1
    if not votes:
        return None
    w = votes.most_common(1)[0][0]
    return "p1" if w == 1 else "p2"


# ================= ТРИГГЕРЫ =================
def trigger_doubles(TL):
    n = hit = 0
    for t in TL.values():
        gs = t["games"]
        for i, g in enumerate(gs):
            if not g["server"]:
                continue
            s = 0 if g["server"] == "p1" else 1
            if g["dbl"][s] < 2:
                continue
            for h in gs[i + 1:]:
                if h["server"] == g["server"]:
                    n += 1
                    hit += h["winner"] != h["server"]
                    break
    return n, hit


def break_base(TL):
    n = sum(1 for t in TL.values() for g in t["games"] if g["server"])
    h = sum(1 for t in TL.values() for g in t["games"]
            if g["server"] and g["winner"] != g["server"])
    return n, h


def trigger_pauses(TL, min_gap=240):
    n = hit = 0
    for t in TL.values():
        for ps in t.get("pauses", []):
            for g in t["games"]:
                if g["ts"] >= ps["ts_end"] and g["server"]:
                    n += 1
                    hit += g["winner"] != g["server"]
                    break
    return n, hit


def trigger_bagel(TL):
    """Сухой 1-й гейм сета -> распределение тоталов сета vs база.
    ИСПРАВЛЕНО: sets_final бралось без проверки валидности счёта — незавер-
    шённый (например, данные оборвались посреди сета) "финал" сета мог
    попасть в base_totals/dry_totals наравне с реально сыгранными сетами и
    искажать среднюю. Комментарий в build_timelines честно предупреждал
    "фильтр валидности ниже", но самого фильтра тут не было — теперь есть
    (valid_set())."""
    dry_totals, base_totals = [], []
    for t in TL.values():
        by_set = {}
        for g in t["games"]:
            by_set.setdefault(g["set"], []).append(g)
        for si, gs in by_set.items():
            fin = t["sets_final"].get(si)
            if not fin or not valid_set(fin):
                continue
            total = fin[0] + fin[1]
            base_totals.append(total)
            first = gs[0]
            if first["pts_end"] in (("40", "00"), ("40", "15"),
                                    ("00", "40"), ("15", "40")):
                dry_totals.append(total)
    return dry_totals, base_totals


def pct(h, n):
    return f"{100 * h / n:.0f}%" if n else "—"


def sample_note(n, small=30):
    """Честная пометка объёма выборки — чтобы не путать шум со ступенькой."""
    return " [МАЛО ДАННЫХ, не доверять]" if 0 < n < small else ""


def main2():
    args = sys.argv[1:]
    files = []
    if "--files" in args:
        i = args.index("--files") + 1
        while i < len(args) and not args[i].startswith("--"):
            files.append(args[i])
            i += 1
    line_files = [f for f in files if "line" in f]
    if not line_files:
        print("нужен line.jsonl")
        return
    TL = build_timelines(line_files)
    ng = sum(len(t["games"]) for t in TL.values())
    print(f"матчей: {len(TL)}, геймов с известным подающим: {ng}")
    bn, bh = break_base(TL)
    print(f"БАЗА брейков: {bh}/{bn} ({pct(bh, bn)})")
    n, h = trigger_doubles(TL)
    print(f"[2] 2+ двойные -> брейк в след. гейме подачи: {h}/{n} ({pct(h, n)}){sample_note(n)}")
    n, h = trigger_pauses(TL)
    print(f"[3] пауза 4+ мин -> гейм принимающему: {h}/{n} ({pct(h, n)}){sample_note(n)}")
    dry, base = trigger_bagel(TL)
    import statistics as S
    if base:
        b_over9 = sum(1 for x in base if x <= 9)
        print(f"[7] сеты всего: {len(base)}, средний тотал {S.mean(base):.1f}, "
              f"<=9.5: {b_over9}/{len(base)} ({pct(b_over9, len(base))})")
    if dry:
        d_over9 = sum(1 for x in dry if x <= 9)
        print(f"    после сухого 1-го гейма: {len(dry)}, средний тотал {S.mean(dry):.1f}, "
              f"<=9.5: {d_over9}/{len(dry)} ({pct(d_over9, len(dry))}){sample_note(len(dry))}")
        print(f"    тоталы: {sorted(dry)}")
    else:
        print("[7] сухих стартов пока нет")


# ================= СЕТЫ: СИММЕТРИЯ (чёт/нечет, зеркала, доминация) =================
def valid_set(ab):
    a, b = ab
    if (a == 6 and b <= 4) or (b == 6 and a <= 4):
        return True
    return (a, b) in ((7, 5), (5, 7), (7, 6), (6, 7))


def set_class(ab):
    a, b = ab
    w, l = (a, b) if a > b else (b, a)
    if (w, l) in ((6, 0), (6, 1)):
        return "разгром"
    if (w, l) in ((6, 2), (6, 3)):
        return "уверенно"
    return "борьба"


def analyze_set_patterns(TL):
    from collections import Counter
    pairs = []
    for eid, t in TL.items():
        sf = t["sets_final"]
        if 1 in sf and 2 in sf and valid_set(sf[1]) and valid_set(sf[2]):
            s1, s2 = sf[1], sf[2]
            w1 = "p1" if s1[0] > s1[1] else "p2"
            w2 = "p1" if s2[0] > s2[1] else "p2"
            pairs.append((s1, s2, w1 == w2, t.get("tour", "?")))
    print(f"пар сетов 1->2 (валидных): {len(pairs)}")
    if not pairs:
        return
    same = sum(1 for _, _, s, _ in pairs if s)
    print(f"победитель 1-го берёт 2-й: {same}/{len(pairs)} ({100*same/len(pairs):.0f}%)")
    mat = Counter()
    for s1, s2, _, _ in pairs:
        mat[(set_class(s1), set_class(s2))] += 1
    print("матрица (1-й -> 2-й):")
    for k in sorted(mat):
        print(f"  {k[0]:10s} -> {k[1]:10s}: {mat[k]}")
    eo = Counter()
    for s1, s2, _, _ in pairs:
        eo[((s1[0]+s1[1]) % 2, (s2[0]+s2[1]) % 2)] += 1
    print("чёт/нечет тотала (1-й -> 2-й), 0=чёт:")
    for k in sorted(eo):
        print(f"  {k}: {eo[k]}")
    mirr = [(s1, s2) for s1, s2, s, _ in pairs
            if not s and set_class(s1) in ("разгром", "уверенно")]
    print(f"зеркала (разгромил 1-й, проиграл 2-й): {len(mirr)}/{len(pairs)}")
    for s1, s2 in mirr[:8]:
        print(f"  {s1[0]}-{s1[1]} -> {s2[0]}-{s2[1]}")


if __name__ == "__main__":
    main()
    print()
    print("================ ТРИГГЕРЫ 2/3/7 ================")
    main2()
    print()
    print("================ СЕТЫ: СИММЕТРИЯ ================")
    import glob as _g
    _fl = [f for f in sys.argv[1:] if f.endswith(".jsonl") or "*" in f]
    _paths = []
    for _p in _fl:
        _paths.extend(sorted(_g.glob(_p)) or [_p])
    _TL = build_timelines([p for p in _paths if "line" in p])
    try:
        analyze_set_patterns(_TL)
    except Exception as _e:
        print("set-анализ:", _e)
