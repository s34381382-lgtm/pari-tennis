#!/usr/bin/env python3
"""Бумажный трейдинг: журнал сигналов + автосверка исходов (без денег).

  python3 pari_paper.py add --eid 68050403 --match "Монне - Скотт" --trigger T7 \\
      --market "тотал сета меньше 9.5" --side under --odds 1.85 --stake 1
  python3 pari_paper.py settle --files /public/line.jsonl
  python3 pari_paper.py report
  python3 pari_paper.py auto --files /public/line.jsonl   # сигналы из триггеров + сверка
  python3 pari_paper.py void --files '/public/line*.jsonl'  # пропавшие из ленты -> void

ЧТО ИЗМЕНЕНО: 1) match_winner_from_final вообще не существовала как функция —
её тело осталось недостижимым мёртвым кодом ПОСЛЕ return в valid_set_score
(её "def" потерялся). settle вызывал её на каждый финал и падал с NameError —
то есть команда `settle` была полностью неработоспособна. Функция
восстановлена отдельно, плюс добавлена проверка легальности счёта каждого
сета (_valid_set_str) вместо слепого сравнения чисел. 2) Повторяющиеся
"import sys; sys.path.insert(...)" внутри normalize/cmd_add/cmd_auto
(в normalize — на КАЖДЫЙ сигнал при каждой загрузке журнала) вынесены в
один импорт наверху файла.
"""
import json
import os
import sys

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
# Единый импорт pari_lib/pari_patterns здесь, наверху. Раньше каждая функция
# (normalize/cmd_add/cmd_auto) сама делала "import sys; sys.path.insert(0, ...)"
# перед своим локальным "from pari_lib import ...". normalize() вызывается на
# КАЖДЫЙ сигнал при каждой загрузке журнала — то есть при большом paper.jsonl
# это раздувало sys.path одной и той же записью сотни/тысячи раз за один
# запуск. Поведение не менялось, но это чистый мусор — вынесено один раз сюда.
from pari_lib import ALLOWED_HORIZONS, CIRCUIT_STAKE, tour_circuit
from pari_lib import game_fair as _gf
from pari_lib import match_fair as _mf
from pari_lib import serve_point_prob
from pari_patterns import build_timelines

DATA = os.environ.get("PARI_DATA", "/public")
PAPER = os.path.join(DATA, "paper.jsonl")


TRIGGER_HORIZON = {"T7": "set", "ASYM-TB": "set", "A": "match",
                   "B": "set", "C": "game", "D": "game"}


def normalize(s):
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
    sig = {"ts": time.time(), "eid": str(g("--eid")),
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


def _valid_set_str(s):
    """'7-6' -> True (легальный завершённый счёт сета), 'a-b' в виде строки
    (формат потока finals — не путать с set_scores из live, там пары)."""
    try:
        a, b = str(s).split("-")
        a, b = int(a), int(b)
    except (ValueError, AttributeError):
        return False
    if (a == 6 and b <= 4) or (b == 6 and a <= 4):
        return True
    return (a, b) in ((7, 5), (5, 7), (7, 6), (6, 7))


def valid_set_score(a, b):
    """Валиден ли счёт сета a-b (числа или числовые строки из set_scores)."""
    try:
        a, b = int(a), int(b)
    except (ValueError, TypeError):
        return False
    if (a == 6 and b <= 4) or (b == 6 and a <= 4):
        return True
    return (a, b) in ((7, 5), (5, 7), (7, 6), (6, 7))


def match_winner_from_final(fin):
    """final: ['7-6','6-2'] -> 'p1'/'p2'/None.
    ИСПРАВЛЕНО: этой функции вообще не было в модуле — определение
    "def match_winner_from_final(fin):" отсутствовало, а её тело (докстринг
    и код ниже) висело МЁРТВЫМ КОДОМ после return в valid_set_score (то есть
    было недостижимо ни при каком вызове valid_set_score). settle_from_files
    вызывает match_winner_from_final(...) при обработке КАЖДОГО финала — то
    есть команда `settle` падала с NameError при первом же реальном
    результате, и вся сверка сигналов была полностью неработоспособна.
    Заодно добавлена проверка _valid_set_str на каждый сет: раньше счёт
    сета засчитывался просто по "какое число больше", без проверки, что это
    вообще легальный счёт (защиты от кривых/повреждённых записей в finals
    не было)."""
    w1 = w2 = 0
    for s in fin:
        if not _valid_set_str(s):
            continue
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
    import glob as _g
    sigs = load_signals()
    opened = [s for s in sigs if s["status"] == "open"]
    if not opened:
        print("открытых нет")
        return
    last_seen = {}
    for pattern in files:
        for path in sorted(_g.glob(pattern)) or [pattern]:
            if not os.path.exists(path):
                continue
            for line in open(path, encoding="utf-8"):
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = d.get("ts", 0)
                for m in d.get("matches", []):
                    eid = str(m.get("eid"))
                    if eid not in last_seen or ts > last_seen[eid]:
                        last_seen[eid] = ts
    now = time.time()
    n = 0
    for s in opened:
        eid = str(s["eid"])
        ts_added = s.get("ts", 0)
        ls = last_seen.get(eid)
        if ls is None:
            if now - ts_added > 86400:
                s["status"] = "void"
                s["note"] = "not seen in feed"
                n += 1
            continue
        if now - ls > 7200:
            s["status"] = "void"
            s["note"] = "disappeared from feed"
            n += 1
    save_signals(sigs)
    print(f"void: {n}")


def cmd_report():
    sigs = load_signals()
    if not sigs:
        print("пусто")
        return
    by_trig = {}
    for s in sigs:
        by_trig.setdefault(s.get("trigger", "?"), []).append(s)
    total_profit = 0.0
    total_n = 0
    for trig, ss in sorted(by_trig.items()):
        closed = [x for x in ss if x["status"] in ("win", "lose")]
        wins = [x for x in closed if x["status"] == "win"]
        profit = sum(x.get("profit", 0) for x in closed)
        total_profit += profit
        total_n += len(closed)
        openc = sum(1 for x in ss if x["status"] == "open")
        voidc = sum(1 for x in ss if x["status"] == "void")
        wr = f"{100*len(wins)/len(closed):.0f}%" if closed else "—"
        print(f"{trig:10s} закрыто={len(closed):3d} ({wr}) прибыль={profit:+.2f}u "
              f"открыто={openc} void={voidc}")
    print(f"{'ИТОГО':10s} закрыто={total_n:3d} прибыль={total_profit:+.2f}u")


def cmd_auto(args):
    import glob as _g
    # 18.09: main() передаёт уже список файлов (files), а не argv-хвост —
    # старая версия ждала "--files" внутри и всегда отвечала "нужны --files".
    # Принимаем оба формата: ["--files", pat...] или [file...].
    pats = []
    if args and args[0] == "--files":
        pats = args[1:]
    elif args and any(a.startswith("--") for a in args):
        g = lambda k, d=None: args[args.index(k) + 1] if k in args else d
        pattern = g("--files")
        if pattern:
            idx = args.index("--files") + 1
            while idx < len(args) and not args[idx].startswith("--"):
                pats.append(args[idx])
                idx += 1
    else:
        pats = list(args or [])
    line_files = []
    for p in pats:
        line_files.extend(sorted(_g.glob(p)) or [p])
    if not line_files:
        print("нужны --files")
        return
    sigs = load_signals()
    TL = build_timelines(line_files)
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
                # 18.09: порог по контуру (LAB 0.16 — маржа выше).
                _em = 0.16 if tour_circuit(m.get("tour")) == "LAB" else 0.12
                if fair1 - 1 / o1 > _em:
                    cand = ("p1", o1, fair1)
                elif (1 - fair1) - 1 / o2 > _em:
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
