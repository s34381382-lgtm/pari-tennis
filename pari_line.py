#!/usr/bin/env python3
"""PARI live-линия по чистому API (без браузера!).
Протокол вскрыт из JS-бандлов (2026-09-13):
  base:  GET https://line-XXX/events/listBase?lang=ru&scopeMarket=2300  (gzip JSON, ~8MB)
  delta: GET https://line-XXX/events/list?lang=ru&version=V&scopeMarket=2300 (gzip, ~100KB)
  odds:  customFactors[{e, countAll, factors:[{f,v,p,pt}]}]
  score: liveEventInfos[{eventId, scores, subscores}]
  names: каталог /line/factorsCatalog/tables (f -> рынок)

  python3 pari_line.py                                   # все live-матчи -> stdout JSONL
  python3 pari_line.py --tennis --out line.jsonl         # только теннис в файл
  python3 pari_line.py --loop 8 --out line.jsonl         # опрос каждые 8с, вечно
  python3 pari_line.py --once --tennis                   # один снимок тенниса
"""
import gzip
import json
import os
import re
import sys
import time
import urllib.request

HOSTS = ["https://line-lb51-w.pb06e2-resources.com",
         "https://line-lb01-w.pb06e2-resources.com",
         "https://line-vk01-w.pb06e2-resources.ru"]
SCOPE = "2300"
LANG = "ru"
SYSID = "22"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128"
TENNIS_RE = re.compile(r"WTA|ATP|ITF|UTR|еннис|Челленджер|челленджер", re.I)
SEEN_FILE = os.path.join(os.environ.get("PARI_DATA", "/public"), "pari_line_seen.json")


def http_get(url, timeout=20):
    last = None
    for h in HOSTS:
        u = url.replace(HOSTS[0], h)
        try:
            req = urllib.request.Request(u, headers={
                "User-Agent": UA, "Origin": "https://pari.ru",
                "Referer": "https://pari.ru/", "Accept-Encoding": "gzip"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
            try:
                return gzip.decompress(data)
            except OSError:
                return data
        except Exception as e:
            last = e
    raise last


def api(path):
    return json.loads(http_get(HOSTS[0] + path).decode("utf-8"))


class State:
    def __init__(self):
        self.tours = {}
        self.events = {}
        self.live = {}
        self.factors = {}
        self.version = 0
        self.fmap = {}
        try:
            self.seen = set(json.load(open(SEEN_FILE, encoding="utf-8")))
        except Exception:
            self.seen = set()

    def save_seen(self):
        try:
            json.dump(sorted(self.seen)[-5000:], open(SEEN_FILE, "w", encoding="utf-8"))
        except Exception:
            pass

    def load_catalog(self):
        try:
            j = api(f"/line/factorsCatalog/tables?version=0&lang={LANG}&sysId={SYSID}")
            for g in j.get("groups", []):
                for t in g.get("tables", []):
                    rows = t.get("rows", [])
                    head = [c.get("name", "") for c in rows[0]] if rows else []
                    for r in rows[1:]:
                        rn = r[0].get("name", "") if r else ""
                        for ci, c in enumerate(r):
                            if isinstance(c, dict) and "factorId" in c:
                                col = head[ci] if ci < len(head) else ""
                                self.fmap[c["factorId"]] = (g.get("name", ""), col, rn)
        except Exception as e:
            print(f"catalog fail: {e}", file=sys.stderr, flush=True)

    def apply(self, j):
        for s in j.get("sports", []):
            self.tours[s["id"]] = s.get("name", "")
        for e in j.get("events", []):
            self.events[e["id"]] = e
        for li in j.get("liveEventInfos", []):
            self.live[li["eventId"]] = li
        for cf in j.get("customFactors", []):
            old = self.factors.get(cf["e"], {})
            merged = {f["f"]: f for f in old.get("factors", [])}
            for f in cf.get("factors", []):
                merged[f["f"]] = f
            self.factors[cf["e"]] = {"countAll": cf.get("countAll", 0),
                                     "factors": list(merged.values())}
        self.version = j.get("packetVersion", self.version)

    def boot(self):
        self.load_catalog()
        self.apply(api(f"/events/listBase?lang={LANG}&scopeMarket={SCOPE}"))

    def delta(self):
        self.apply(api(f"/events/list?lang={LANG}&version={self.version}&scopeMarket={SCOPE}"))


def parse_score(li):
    """liveEventInfo -> dict."""
    out = {}
    try:
        sc = li.get("scores", [])
        if sc and len(sc[0]) > 0:
            out["sets"] = [sc[0][0].get("c1"), sc[0][0].get("c2")]
        if len(sc) > 1:
            out["set_scores"] = [[x.get("c1"), x.get("c2")] for x in sc[1]]
        if len(sc) > 2 and sc[2]:
            g = sc[2][0]
            out["game"] = [g.get("c1"), g.get("c2")]
            if g.get("serve"):
                out["serve"] = int(g["serve"])
        out["comment"] = li.get("scoreComment", "")
    except Exception:
        pass
    stats = {}
    for s in li.get("subscores", []):
        n = s.get("kindName", "")
        if n and "сет" not in n and "тайм" not in n.lower():
            stats[n] = {"c1": s.get("c1"), "c2": s.get("c2"),
                        "by_set": s.get("comment", "")}
    if stats:
        out["stats"] = stats
    return out


def resolve_odd(st, f):
    meta = st.fmap.get(f["f"])
    name = " / ".join(x for x in meta if x) if meta else f"f{f['f']}"
    o = {"m": name, "v": f.get("v")}
    if f.get("pt") not in (None, ""):
        o["pt"] = f["pt"]
    return o


def snapshot(st, tennis_only=True):
    out = {"ts": int(time.time()), "matches": [], "fresh": [], "gone": []}
    for eid, e in st.events.items():
        if e.get("place") != "live":
            continue
        tour = st.tours.get(e.get("sportId"), "")
        if tennis_only and not TENNIS_RE.search(tour or ""):
            continue
        m = {"eid": eid, "tour": tour,
             "p1": e.get("team1"), "p2": e.get("team2"),
             "start": e.get("startTime")}
        li = st.live.get(eid)
        if li:
            m.update(parse_score(li))
        cf = st.factors.get(eid)
        if cf:
            m["odds"] = [resolve_odd(st, f) for f in cf["factors"]]
            m["n_odds"] = cf.get("countAll", len(cf["factors"]))
        out["matches"].append(m)
        # свежий матч: первый сет, геймов <= 2, имена известны
        try:
            if not m.get("p1") or not m.get("p2"):
                continue
            sc = (m.get("set_scores") or [])
            if (m.get("sets") == ["0", "0"] or not m.get("sets")) and len(sc) <= 1:
                g = [int(x) for x in (sc[0] if sc else ["0", "0"])]
                if sum(g) <= 2 and str(eid) not in st.seen:
                    st.seen.add(str(eid))
                    out["fresh"].append(eid)
        except Exception:
            pass
    return out


def main():
    args = sys.argv[1:]
    tennis = "--tennis" in args or "--all" not in args
    out = args[args.index("--out") + 1] if "--out" in args else None
    loop = int(args[args.index("--loop") + 1]) if "--loop" in args else 0
    once = "--once" in args

    st = State()
    print("boot: listBase...", file=sys.stderr, flush=True)
    st.boot()
    print(f"boot ok: events={len(st.events)} live_tennis="
          + str(sum(1 for e in st.events.values()
                    if e.get("place") == "live" and TENNIS_RE.search(
                        st.tours.get(e.get("sportId"), "") or ""))),
          file=sys.stderr, flush=True)

    def emit():
        snap = snapshot(st, tennis)
        line = json.dumps(snap, ensure_ascii=False)
        if out:
            with open(out, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        else:
            print(line, flush=True)
        return snap

    if once or (not loop and out is None):
        st.delta()
        emit()
        st.save_seen()
        return
    fails = 0
    while True:
        try:
            st.delta()
            s = emit()
            st.save_seen()
            fails = 0
            if loop <= 0:
                return
        except Exception as e:
            fails += 1
            print(f"poll fail x{fails}: {e}", file=sys.stderr, flush=True)
            time.sleep(min(30 + fails * 10, 120))
            continue
        time.sleep(loop)


if __name__ == "__main__":
    main()
