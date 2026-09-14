#!/usr/bin/env python3
"""Живые сигналы: хвост файла APK -> триггеры в реальном времени -> крик.

  python3 pari_signals.py --follow            # следить за телефоном (adb), орать сигналы
  python3 pari_signals.py --follow --interval 20
  python3 pari_signals.py --file /tmp/x.jsonl # один прогон по файлу (тест)

Триггеры: M-edge (марковская цена vs кэф), коллапс (2+ гейма подряд с ведения),
чок (проигрыш с 40-0/40-15), свежие матчи, конец сета у фаворита в борьбе.
"""
import json
import os
import re
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
from pari_lib import game_fair, match_fair, serve_point_prob, tour_circuit

PHONE = "localhost:45375"
ADB_PORT_FILE = "/tmp/adb_port.txt"
EDGE_MIN = 0.12


def adb_port():
    try:
        return open(ADB_PORT_FILE, encoding="utf-8").read().strip().split("=")[1]
    except Exception:
        return "localhost:45375".split(":")[1]


def fetch_tail():
    """Последние ~40 строк текущего часового файла с телефона."""
    import datetime as _dt
    port = adb_port()
    hour = _dt.datetime.now().strftime("%Y-%m-%d-%H")
    remote = f"/sdcard/Android/data/com.pari.collector/files/line-{hour}.jsonl"
    r = subprocess.run(["adb", "-s", f"localhost:{port}", "shell",
                        f"tail -40 {remote}"],
                       capture_output=True, text=True, timeout=30)
    out = []
    for line in r.stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def games_seq(snaps, eid):
    """История счёта геймов матча: [(a,b,ts)] + последний serve/game/odds/stats."""
    hist, last = [], None
    for d in snaps:
        for m in d.get("matches", []):
            if str(m.get("eid")) != str(eid):
                continue
            ss = m.get("set_scores") or []
            if not ss:
                continue
            try:
                a, b = int(ss[-1][0]), int(ss[-1][1])
            except (ValueError, TypeError):
                continue
            if not hist or hist[-1][:2] != (a, b):
                hist.append((a, b, d["ts"]))
            last = (m, d["ts"])
    return hist, last


def check_match(eid, snaps, state):
    """Список строк-сигналов. state: память между вызовами {eid: {...}}."""
    sigs = []
    hist, last = games_seq(snaps, eid)
    if not last or len(hist) < 2:
        return sigs
    m, ts = last
    p1, p2 = m.get("p1", "?"), m.get("p2", "?")
    nm = f"{p1}-{p2}"
    st = state.setdefault(str(eid), {})
    ss = m.get("set_scores") or []
    game = m.get("game") or []
    srv = str(m.get("serve") or "")

    # --- коллапс: серия 3+ прервана проигрышем 1-2 геймов подряд ---
    # сначала восстанавливаем порядок геймов сета из истории счёта
    if len(hist) >= 2:
        # грубая серия по счёту: кто брал последние геймы
        wins = []
        for i in range(1, len(hist)):
            a0, b0, _ = hist[i - 1]
            a1, b1, _ = hist[i]
            if a1 == a0 + 1 and b1 == b0:
                wins.append("p1")
            elif b1 == b0 + 1 and a1 == a0:
                wins.append("p2")
            else:
                wins.append("?")
        # текущая серия проигрышей держателя длинной серии
        for L in (5, 4, 3):
            if len(wins) >= L + 1 and len(set(wins[-(L + 1):-1])) == 1 \
                    and wins[-1] != wins[-2]:
                holder = wins[-2]
                if st.get("collapse") != (L, hist[-1][0], hist[-1][1]):
                    st["collapse"] = (L, hist[-1][0], hist[-1][1])
                    who = p1 if holder == "p1" else p2
                    opp = p2 if holder == "p1" else p1
                    sigs.append(f"КОЛЛАПС {nm}: {who} взял {L} подряд и отдал "
                                f"гейм (счёт {hist[-1][0]}-{hist[-1][1]}). "
                                f"Разворот? Смотреть {opp}!")
                break

    # --- чок: проигрыш гейма с 40-0/40-15 (ловим по очкам в окне) ---
    if len(game) == 2 and srv in ("1", "2"):
        key = ("choke", m.get("sets"), ss[-1][0] if ss else None)
        # точку входа фиксируем на брейк-пойнте против подающего после 40-0/40-15:
        # упрощённо: 30-40/40-A на подаче + предыдущие очки были 40-0/40-15
        pts = [tuple(x.get("game") or ()) for x in
               [mm for dd in snaps for mm in dd.get("matches", [])
                if str(mm.get("eid")) == str(eid)][-6:]]
        flat = [p for p in pts if len(p) == 2]
        server_side = "p1" if srv == "1" else "p2"
        if tuple(game) in (("30", "40"), ("40", "A"), ("15", "40"), ("00", "40")):
            had40 = any(p[0] == "40" and p[1] in ("00", "15") for p in flat) \
                if server_side == "p1" else \
                any(p[1] == "40" and p[0] in ("00", "15") for p in flat)
            if had40 and st.get("choke") != (ss[-1][0], ss[-1][1], tuple(game)):
                st["choke"] = (ss[-1][0], ss[-1][1], tuple(game))
                who = p1 if server_side == "p1" else p2
                sigs.append(f"ЧОК {nm}: {who} вёл 40-0/40-15, сейчас {game[0]}-{game[1]} "
                            f"на своей подаче. Подача плывёт!")

    # --- поинты: кто возьмёт очко N гейма G (честно, но ЛАТЕНТНОСТЬ!) ---
    try:
        from pari_lib import serve_point_prob as _spp
        ss = m.get("set_scores") or []
        srv = str(m.get("serve") or "")
        if ss and srv in ("1", "2") and m.get("odds"):
            a, b = int(ss[-1][0]), int(ss[-1][1])
            cur_gno = a + b + 1
            for o in m.get("odds", []):
                f = o.get("f") or 0
                if not (2995 <= f <= 3020):
                    continue
                mm = re.match(r"(\d+)\s*-\s*очко\s*(\d+)", str(o.get("pt") or ""))
                if not mm:
                    continue
                gno = int(mm.group(1))
                if gno < cur_gno:
                    continue  # гейм уже прошёл
                # кто подаёт в том гейме: чётность от текущего
                flip = (gno - cur_gno) % 2
                srv_n = int(srv) if flip == 0 else 3 - int(srv)
                side = "p1" if srv_n == 1 else "p2"
                # чья победа в очке котируется: %1/%2 в имени
                mk = str(o.get("m", ""))
                for_want = "%1" if side == "p1" else "%2"
                against = "%2" if side == "p1" else "%1"
                if for_want not in mk:
                    continue
                p = _spp(m.get("stats"), m.get("tour"), side)
                v = o.get("v")
                if v and p - 1 / v > EDGE_MIN:
                    key = ("point", gno, mm.group(2), round(v, 2))
                    if st.get("point") != key:
                        st["point"] = key
                        who = p1 if side == "p1" else p2
                        sigs.append(f"ПОИНТ {nm}: очко {mm.group(2)} гейма {gno} "
                                    f"возьмёт {who} p={p:.0%} vs кэф {v} "
                                    f"(+{p - 1/v:.0%}) [ЛАГ: руками не успеть, для изучения]")
    except Exception:
        pass

    # --- M-edge: марковская цена матча vs кэф ---    try:
        if ss and srv in ("1", "2"):
            sw = [0, 0]
            for g in ss[:-1]:
                a_, b_ = int(g[0]), int(g[1])
                if (a_ == 6 and b_ <= 4) or (a_, b_) in ((7, 5), (7, 6)):
                    sw[0] += 1
                elif (b_ == 6 and a_ <= 4) or (a_, b_) in ((5, 7), (6, 7)):
                    sw[1] += 1
            a, b = int(ss[-1][0]), int(ss[-1][1])
            stats = m.get("stats")
            p1p = serve_point_prob(stats, m.get("tour"), "p1")
            p2p = serve_point_prob(stats, m.get("tour"), "p2")
            h1, h2 = game_fair(p1p, (0, 0)), game_fair(p2p, (0, 0))
            o1 = o2 = None
            for o in m.get("odds", []):
                if o.get("f") == 921:
                    o1 = o.get("v")
                elif o.get("f") == 923:
                    o2 = o.get("v")
            if h1 and h2 and o1 and o2:
                fair1 = match_fair(tuple(sw), (a, b), int(srv), h1, h2)
                for side, fo, bo in (("p1", fair1, o1), ("p2", 1 - fair1, o2)):
                    if fo - 1 / bo > EDGE_MIN:
                        key = ("edge", side, round(bo, 2))
                        if st.get("edge") != key:
                            st["edge"] = key
                            who = p1 if side == "p1" else p2
                            sigs.append(f"EDGE {nm}: модель {fo:.0%} vs кэф {bo} "
                                        f"на {who} (+{fo - 1/bo:.0%})")
    except Exception:
        pass
    return sigs


def main():
    args = sys.argv[1:]
    interval = int(args[args.index("--interval") + 1]) if "--interval" in args else 20
    if "--file" in args:
        snaps = [json.loads(l) for l in
                 open(args[args.index("--file") + 1], encoding="utf-8") if l.strip()]
        state = {}
        eids = {str(m.get("eid")) for d in snaps for m in d.get("matches", [])}
        for eid in sorted(eids):
            for s in check_match(eid, snaps[-40:], state):
                print(s, flush=True)
        return
    state = {}
    print("слежу за эфиром (Ctrl-C - стоп)...", flush=True)
    while True:
        try:
            snaps = fetch_tail()
        except Exception as e:
            print(f"fetch fail: {e}", flush=True)
            time.sleep(interval)
            continue
        if not snaps:
            time.sleep(interval)
            continue
        eids = {str(m.get("eid")) for d in snaps for m in d.get("matches", [])}
        for eid in sorted(eids):
            for s in check_match(eid, snaps, state):
                print(time.strftime("%H:%M:%S") + " " + s, flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    import time
    main()
