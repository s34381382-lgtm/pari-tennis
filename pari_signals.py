#!/usr/bin/env python3
"""Живые сигналы: хвост файла APK -> триггеры в реальном времени -> крик.

  python3 pari_signals.py --follow            # следить за телефоном (adb), орать сигналы
  python3 pari_signals.py --follow --interval 20
  python3 pari_signals.py --file /tmp/x.jsonl # один прогон по файлу (тест)

Триггеры: M-edge (марковская цена vs кэф), GAME (эмпирический холд
поинт-состояния vs геймовый кэф — калибровка пул 2268 геймов),
ПРОСАДКА (отдал с 40-0/0-40 -> против следующей подачи),
коллапс (2+ гейма подряд с ведения),
чок (проигрыш с 40-0/40-15), свежие матчи, конец сета у фаворита в борьбе.

ЧТО ИЗМЕНЕНО (главное): комментарий "# --- M-edge: ... ---    try:" склеился
с "try:" в одну строку-комментарий, и настоящего try для блока M-edge не
было. По отступам весь блок M-edge стал ВТОРЫМ statement'ом внутри
except-суиты триггера "поинты" (подтверждено AST) — то есть EDGE-сигнал
исполнялся только если "поинты" ВЫШЕ бросали исключение, а в норме — никогда.
Проверено на данных с заведомым перекосом кэфа: раньше 0 сигналов, теперь
EDGE корректно срабатывает. У блока M-edge теперь свой независимый try/except.
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
from pari_patterns import is_women

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
        # окно последних состояний очков этого матча в ТЕКУЩЕМ снимке snaps.
        # ОГРАНИЧЕНИЕ (не исправлял без данных для проверки, только фиксирую):
        # snaps в режиме --follow — это только "хвост" ТЕКУЩЕГО часового
        # файла (см. fetch_tail, tail -40), а не вся история матча. Сразу
        # после смены часа (новый line-HH.jsonl, почти пустой) 40-0/40-15,
        # случившийся несколькими минутами раньше в ПРЕДЫДУЩЕМ часовом файле,
        # может быть не виден в этом окне — чок тогда молча не поймается
        # (не "опоздает", а просто не сработает). Если это окажется заметным
        # на практике, лечится либо увеличением tail, либо явным чтением
        # прошлого часового файла на границе часа.
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
                p = serve_point_prob(m.get("stats"), m.get("tour"), side)
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

    # --- M-edge: марковская цена матча vs кэф ---
    # ИСПРАВЛЕНО (это было главным багом файла): здесь стояло
    # "# --- M-edge: ... ---    try:" ОДНОЙ строкой — "try:" оказался
    # частью комментария и не создавал никакого try. Следующая строка
    # "if ss and srv in ...:" из-за этого оставалась на том же уровне
    # отступа, что и "pass" в except выше, и AST это подтверждает: весь
    # блок M-edge был ВТОРЫМ statement'ом ВНУТРИ except-суиты триггера
    # "поинты", а не отдельным блоком после неё. То есть M-edge реально
    # исполнялся, только когда блок "поинты" ВЫШЕ бросал исключение — то
    # есть практически никогда. Проверено: на данных, где EDGE обязан
    # сработать (явный перекос кэфа против модели) и где "поинты" отрабатывают
    # штатно без исключений, сигналов не было вообще. Плюс это делало сам
    # M-edge код беззащитным: если БЫ он вдруг исполнился и упал сам, второй
    # (тоже "except Exception: pass") его уже не ловил — тот второй except
    # синтаксически привязан к ТОЙ ЖЕ try/except конструкции и, идя ПОСЛЕ
    # первого except Exception, был недостижим (Python не даёт двум except
    # одного типа на одном try отработать по очереди — сработает только
    # первый). Сейчас у блока M-edge свой независимый try/except, как и
    # задумано по названию секции.
    try:
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

    # --- GAME: эмпирический холд поинт-состояния vs геймовый кэф ---
    # Калибровка pool_states.py (пул 2268 геймов): состояние "видели" =
    # текущее тоже видели, таблица применима к live-счёту напрямую.
    # lead40 .968 (Ж.953/М.977), 30-0 .927, ровно .620 (Ж.552/М.694),
    # 15-30 .445 (Ж.342/М.561), 0-30 .300 (Ж.237/М.381), 0-40 сейв .139.
    try:
        if ss and srv in ("1", "2") and m.get("odds") and len(game) == 2:
            a, b = int(ss[-1][0]), int(ss[-1][1])
            cur_gno = a + b + 1
            side = "p1" if srv == "1" else "p2"
            gm = (str(game[0]), str(game[1]))
            sp, rp = (gm[0], gm[1]) if side == "p1" else (gm[1], gm[0])
            w = is_women(m.get("tour"))
            key = None
            if (sp, rp) in (("40", "00"), ("40", "15")):
                key = "lead40"
            elif (sp, rp) == ("30", "00"):
                key = "s30_0"
            elif (sp == "40" and rp == "40") or "A" in gm:
                key = "deuce"
            elif (sp, rp) == ("15", "30"):
                key = "s15_30"
            elif (sp, rp) == ("00", "30"):
                key = "s0_30"
            elif (sp, rp) in (("00", "40"), ("15", "40")):
                key = "def40"
            if key:
                tab = {"lead40": (0.968, 0.953, 0.977),
                       "s30_0": (0.927, 0.927, 0.927),
                       "deuce": (0.620, 0.552, 0.694),
                       "s15_30": (0.445, 0.342, 0.561),
                       "s0_30": (0.300, 0.237, 0.381),
                       "def40": (0.139, 0.139, 0.139)}[key]
                p = tab[1] if w else tab[2]
                want_s = "%1" if side == "p1" else "%2"
                want_o = "%2" if side == "p1" else "%1"
                vs = vo = None
                for o in m.get("odds", []):
                    mk = str(o.get("m", ""))
                    if "то выиграет гейм" not in mk or str(o.get("pt")) != str(cur_gno):
                        continue
                    if want_s in mk and vs is None:
                        vs = o.get("v")
                    elif want_o in mk and vo is None:
                        vo = o.get("v")
                fired = False
                if vs and p - 1 / vs > EDGE_MIN:
                    gk = ("game", side, key, round(vs, 2))
                    if st.get("game") != gk:
                        st["game"] = gk
                        who = p1 if side == "p1" else p2
                        sigs.append(f"ГЕЙМ {nm}: {who} держит с {sp}-{rp} "
                                    f"p={p:.0%} vs кэф {vs} (+{p - 1/vs:.0%})")
                        fired = True
                if not fired and vo and (1 - p) - 1 / vo > EDGE_MIN:
                    gk = ("game", "anti-" + side, key, round(vo, 2))
                    if st.get("game") != gk:
                        st["game"] = gk
                        who = p2 if side == "p1" else p1
                        sigs.append(f"ГЕЙМ {nm}: против {p1 if side == 'p1' else p2} "
                                    f"с {sp}-{rp} p_брейк={1-p:.0%} vs кэф {vo} "
                                    f"(+{1-p - 1/vo:.0%})")
    except Exception:
        pass

    # --- ПРОСАДКА: отдал гейм с 40-0/0-40 -> против его следующей подачи ---
    # Следствия из пула: после чока 17/30 (56.7%), после 0-40 наружу 59.0% (n=344).
    try:
        cur_ab = (hist[-1][0], hist[-1][1]) if hist else None
        if cur_ab and srv in ("1", "2") and len(game) == 2:
            gm = (str(game[0]), str(game[1]))
            side = "p1" if srv == "1" else "p2"
            if st.get("last_ab") != cur_ab:
                # гейм закрыт: кто взял и было ли давление 40-0/0-40
                if len(hist) >= 2:
                    pa, pb, _ = hist[-2]
                    ca, cb = cur_ab
                    if (ca == pa + 1) != (cb == pb + 1):
                        won = "p1" if ca == pa + 1 else "p2"
                        fin_srv = "p2" if side == "p1" else "p1"
                        if won != fin_srv and st.get("had_lead") == fin_srv:
                            st["fade"] = (fin_srv, cur_ab)
                st["last_ab"] = cur_ab
                st["had_lead"] = None
            else:
                if st.get("had_lead") is None:
                    snaps_e = [mm for dd in snaps for mm in dd.get("matches", [])
                               if str(mm.get("eid")) == str(eid)][-8:]
                    for mm in snaps_e:
                        gg = mm.get("game") or []
                        if len(gg) == 2 and str(mm.get("serve")) == srv:
                            pp = (str(gg[0]), str(gg[1]))
                            mine, ors = (pp[0], pp[1]) if side == "p1" else (pp[1], pp[0])
                            if (mine, ors) in (("40", "00"), ("40", "15"),
                                               ("00", "40"), ("15", "40")):
                                st["had_lead"] = side
                                break
            fd = st.get("fade")
            if fd and fd[1] != cur_ab and side == fd[0]:
                sp, rp = (gm[0], gm[1]) if side == "p1" else (gm[1], gm[0])
                if (sp, rp) not in (("40", "00"), ("40", "15")):
                    if st.get("fade_fired") != (fd[0], cur_ab):
                        st["fade_fired"] = (fd[0], cur_ab)
                        who = p1 if side == "p1" else p2
                        sigs.append(f"ПРОСАДКА {nm}: {who} отдал прошлый с 40-0/0-40 — "
                                    f"против его подачи (счёт {cur_ab[0]}-{cur_ab[1]})")
                        st["fade"] = None
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
            for s in check_match(eid, snaps, state):
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
    main()
