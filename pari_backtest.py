#!/usr/bin/env python3
"""Бэктест 4 триггеров из гайдов на наших данных (line.jsonl, APK-формат).

  A: 0-40 на подаче фаворита -> фаворит берёт матч
  B: фаворит проиграл 1-й сет в борьбе -> фаворит берёт 2-й сет
  C: 15-30 на сильном сервере -> подающий берёт гейм
  D: подающий на сет с геймовым кэфом <1.30 -> гейм берёт принимающий

  python3 pari_backtest.py --files night/*.jsonl
"""
import glob
import json
import sys

WIN_FIDS = {921, 923}  # 921=П1, 923=П2


def seqs_from_files(paths):
    """eid -> [снимки по порядку]."""
    S = {}
    for path in paths:
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
                S.setdefault(eid, {"tour": m.get("tour"), "p1": m.get("p1"),
                                   "p2": m.get("p2"), "snaps": []})
                s = S[eid]
                if m.get("p1"):
                    s["p1"], s["p2"], s["tour"] = m["p1"], m.get("p2"), m.get("tour")
                s["snaps"].append({"ts": ts, "sets": m.get("sets"),
                                   "ss": m.get("set_scores"), "game": m.get("game"),
                                   "serve": m.get("serve"), "odds": m.get("odds", []),
                                   "stats": m.get("stats", {})})
            for pm in d.get("prematch", []):
                eid = str(pm.get("eid"))
                S.setdefault(eid, {"tour": pm.get("tour"), "p1": pm.get("p1"),
                                   "p2": pm.get("p2"), "snaps": []})
                s = S[eid]
                if pm.get("p1"):
                    s["p1"], s["p2"] = pm["p1"], pm.get("p2")
                s.setdefault("pre_odds", []).append(
                    {"ts": ts, "odds": pm.get("odds", [])})
    return S


def _olist(sn):
    return sn.get("odds") or []


def find_win_odds(s, idx, maxback=500):
    """921/923 поиском назад (кэфы теперь дифами)."""
    o = {}
    for j in range(idx, max(-1, idx - maxback), -1):
        for x in _olist(s["snaps"][j]):
            if x.get("f") == 921 and "p1" not in o:
                o["p1"] = x.get("v")
            elif x.get("f") == 923 and "p2" not in o:
                o["p2"] = x.get("v")
        if "p1" in o and "p2" in o:
            break
    return o


def find_game_odd(s, idx, side, game_no, maxback=500):
    want = "%1" if side == "p1" else "%2"
    for j in range(idx, max(-1, idx - maxback), -1):
        for x in _olist(s["snaps"][j]):
            m = str(x.get("m", ""))
            if "то выиграет гейм" in m and want in m \
                    and str(x.get("pt")) == str(game_no):
                return x.get("v")
    return None


def win_odds(odds):
    """{p1: odd, p2: odd} победы в матче (полный список)."""
    o = {}
    for x in odds or []:
        if x.get("f") == 921:
            o["p1"] = x.get("v")
        elif x.get("f") == 923:
            o["p2"] = x.get("v")
    return o


def fav_of(s):
    """Прематч-фаворит: минимальные 921/923 из ранних кэфов."""
    cands = []
    for po in s.get("pre_odds", [])[:5]:
        cands.append(win_odds(po["odds"]))
    for sn in s["snaps"][:6]:
        cands.append(win_odds(sn["odds"]))
    for c in cands:
        if c.get("p1") and c.get("p2"):
            return "p1" if c["p1"] < c["p2"] else "p2", c
    return None, {}


def game_odds(odds, side, game_no):
    """Кэф 'кто выиграет гейм N' для стороны p1/p2."""
    want = "%1" if side == "p1" else "%2"
    for x in odds or []:
        m = str(x.get("m", ""))
        if "то выиграет гейм" in m and want in m and str(x.get("pt")) == str(game_no):
            return x.get("v")
    return None


def match_winner(s):
    """Победитель матча из финальных сетов (последний снимок)."""
    ss = None
    for sn in reversed(s["snaps"]):
        if sn.get("ss"):
            ss = sn["ss"]
            break
    if not ss:
        return None
    w1 = sum(1 for g in ss if int(g[0]) > int(g[1]))
    w2 = sum(1 for g in ss if int(g[1]) > int(g[0]))
    if w1 == w2 or (w1 < 2 and w2 < 2 and len(ss) < 3):
        # матч может идти; засчитываем только завершённые (2 сета у кого-то / 3 сета)
        done = (w1 >= 2 or w2 >= 2)
        if not done:
            return None
    return "p1" if w1 > w2 else "p2"


def set_winner(s, si):
    for sn in reversed(s["snaps"]):
        ss = sn.get("ss") or []
        if len(ss) >= si:
            try:
                a, b = int(ss[si - 1][0]), int(ss[si - 1][1])
            except (ValueError, TypeError, IndexError):
                continue
            if (a == 6 and b <= 4) or (b == 6 and a <= 4) or (a, b) in (
                    (7, 5), (5, 7), (7, 6), (6, 7)):
                return "p1" if a > b else "p2"
    return None


def game_seq(s):
    """[(winner, server, game_no, set_idx, ts_end)] по геймам."""
    out, prev = [], None
    for sn in s["snaps"]:
        ss = sn.get("ss") or []
        if not ss:
            continue
        try:
            si, a, b = len(ss), int(ss[-1][0]), int(ss[-1][1])
        except (ValueError, TypeError):
            continue
        srv = sn.get("serve")
        if prev and (si, a, b) != (prev[0], prev[1], prev[2]):
            if si == prev[0] and ((a == prev[1] + 1) != (b == prev[2] + 1)):
                winner = "p1" if a == prev[1] + 1 else "p2"
                server = None
                if srv and prev[3] and int(srv) != int(prev[3]):
                    server = "p1" if int(prev[3]) == 1 else "p2"
                out.append({"winner": winner, "server": server,
                            "no": a + b, "set": si, "ts": sn["ts"]})
        prev = (si, a, b, srv)
    return out


def main():
    args = sys.argv[1:]
    files = []
    if "--files" in args:
        i = args.index("--files") + 1
        while i < len(args) and not args[i].startswith("--"):
            import glob as _g
            files.extend(_g.glob(args[i]) or [args[i]])
            i += 1
    S = seqs_from_files(files)
    print(f"матчей: {len(S)}")
    sigA, sigB, sigC, sigD = [], [], [], []
    hitA, hitB, hitC, hitD = [], [], [], []
    sumA = sumC = sumD = 0.0

    for eid, s in S.items():
        if not s.get("p1"):
            continue
        fav, _ = fav_of(s)
        mw = match_winner(s)
        gs = game_seq(s)

        # A: 0-40 на фаворите
        if fav and mw:
            for idx, sn in enumerate(s["snaps"]):
                g = sn.get("game") or []
                if list(g) == ["00", "40"] and str(sn.get("serve")) == (
                        "1" if fav == "p1" else "2"):
                    o = find_win_odds(s, idx).get(fav)
                    if o:
                        sigA.append(o)
                        if mw == fav:
                            hitA.append(o)
                    break
        # B: фаворит проиграл 1-й в борьбе
        if fav:
            w1 = set_winner(s, 1)
            w2 = set_winner(s, 2)
            if w1 and w2 and w1 != fav and tuple(
                    sorted_close(s, 1)) in [("4", "6"), ("5", "7"), ("6", "7")]:
                sigB.append(eid)
                if w2 == fav:
                    hitB.append(eid)
        # C/D: по геймам с очками
        for idx, sn in enumerate(s["snaps"]):
            g = list(sn.get("game") or [])
            srv = str(sn.get("serve") or "")
            if not g or srv not in ("1", "2"):
                continue
            server = "p1" if srv == "1" else "p2"
            try:
                cur_no = current_game_no(s, idx)
            except Exception:
                continue
            if g == ["15", "30"]:
                # C: сильный сервер? эйсы>0 и двойных 0 к этому моменту
                st = sn.get("stats", {})
                ac = st.get("эйсы", {})
                try:
                    ai = int(ac.get("c1" if server == "p1" else "c2", 0) or 0)
                except (ValueError, TypeError):
                    ai = 0
                if ai > 0:
                    o = find_game_odd(s, idx, server, cur_no)
                    w = game_winner_after(s, idx)
                    if o and w:
                        sigC.append(o)
                        sumC += o
                        if w == server:
                            hitC.append(o)
            # D: подающий на сет + его геймовый кэф <1.30
            ss = sn.get("ss") or []
            if g and ss:
                try:
                    a, b = int(ss[-1][0]), int(ss[-1][1])
                except (ValueError, TypeError):
                    continue
                mine = a if server == "p1" else b
                ors = b if server == "p1" else a
                if mine == 5 and ors < 5:
                    o = find_game_odd(s, idx, server, cur_no)
                    if o and o < 1.30:
                        w = game_winner_after(s, idx)
                        if w:
                            sigD.append((o, w != server))
                            sumD += find_game_odd(s, idx,
                                             "p2" if server == "p1" else "p1",
                                             cur_no) or 0
                            if w != server:
                                hitD.append(o)
                            break  # один сигнал на матч для D

    def roi(hits, sigs):
        if not sigs:
            return "—"
        st = sum(h - 1 for h in hits) - (len(sigs) - len(hits))
        return f"{st:+.2f}u ({100 * len(hits) / len(sigs):.0f}%)"

    print(f"[A] 0-40 на фаворите, фаворит берёт матч: {len(hitA)}/{len(sigA)} {roi(hitA, sigA)}")
    print(f"[B] фаворит проиграл 1-й в борьбе, берёт 2-й: {len(hitB)}/{len(sigB)}")
    print(f"[C] 15-30 сильный сервер держит гейм: {len(hitC)}/{len(sigC)} {roi(hitC, sigC)}")
    dh = sum(1 for _, w in sigD if w)
    print(f"[D] подающий на сет кэф<1.30, брейк: {dh}/{len(sigD)}")


def sorted_close(s, si):
    for sn in s["snaps"]:
        ss = sn.get("ss") or []
        if len(ss) >= si:
            try:
                return tuple(sorted((ss[si - 1][0], ss[si - 1][1])))
            except IndexError:
                pass
    return ()


def current_game_no(s, idx):
    sn = s["snaps"][idx]
    ss = sn.get("ss") or []
    a, b = int(ss[-1][0]), int(ss[-1][1])
    return a + b + 1


def game_winner_after(s, idx):
    sn0 = s["snaps"][idx]
    ss0 = sn0.get("ss") or []
    try:
        base = (len(ss0), int(ss0[-1][0]), int(ss0[-1][1]))
    except (ValueError, TypeError, IndexError):
        return None
    for sn in s["snaps"][idx + 1:]:
        ss = sn.get("ss") or []
        if not ss:
            continue
        try:
            cur = (len(ss), int(ss[-1][0]), int(ss[-1][1]))
        except (ValueError, TypeError):
            continue
        if cur != base:
            if cur[0] == base[0] and ((cur[1] == base[1] + 1) != (cur[2] == base[2] + 1)):
                return "p1" if cur[1] == base[1] + 1 else "p2"
            return None
    return None


if __name__ == "__main__":
    main()
