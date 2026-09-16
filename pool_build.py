#!/usr/bin/env python3
"""Единый дедуплицированный пул снимков линии.

Собирает все источники (night/backup/line.jsonl/day14), убирает дубли
по sha1 строки, сортирует по ts и пишет pool.jsonl.

  python3 pool_build.py                 # пул по умолчанию: $PARI_DATA/pool/pool.jsonl
  python3 pool_build.py --out /tmp/p.jsonl

Источники ищутся в $PARI_DATA (по умолчанию /public) + каталог репозитория.
night 01-09 полностью дублируют backup 01-09 — дедуп их схлопывает.
"""
import glob
import gzip
import hashlib
import json
import os
import sys

DATA = os.environ.get("PARI_DATA", "/public")
REPO = os.path.dirname(os.path.abspath(__file__))


def sources():
    pats = [
        f"{DATA}/pari_backup/line-*.jsonl",
        f"{DATA}/pari_backup/line-*.jsonl.gz",
        f"{DATA}/night/*.jsonl",
        f"{DATA}/line.jsonl",
        os.path.join(REPO, "day14.jsonl"),
    ]
    out = []
    for p in pats:
        out.extend(sorted(glob.glob(p)))
    # убрать дубли путей с сохранением порядка
    return list(dict.fromkeys(out))


def main():
    args = sys.argv[1:]
    out_path = (args[args.index("--out") + 1] if "--out" in args
                else os.path.join(DATA, "pool", "pool.jsonl"))
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    files = [f for f in sources() if os.path.exists(f)]
    if not files:
        print("нет источников")
        sys.exit(1)
    seen = set()
    rows = []
    tot = 0
    for f in files:
        n = u = 0
        opener = gzip.open if f.endswith(".gz") else open
        with opener(f, "rt", encoding="utf-8") as fh:
            for line in fh:
                s = line.strip()
                if not s:
                    continue
                n += 1
                tot += 1
                h = hashlib.sha1(s.encode()).hexdigest()
                if h not in seen:
                    seen.add(h)
                    rows.append(s)
                    u += 1
        print(f"{os.path.basename(f)}: строк {n}, новых {u}")
    rows_data = []
    for s in rows:
        try:
            rows_data.append(json.loads(s))
        except json.JSONDecodeError:
            pass
    rows_data.sort(key=lambda d: d.get("ts", 0))
    with open(out_path, "w", encoding="utf-8") as fh:
        for d in rows_data:
            fh.write(json.dumps(d, ensure_ascii=False) + "\n")
    print(f"ВСЕГО строк {tot}, уникальных {len(rows_data)} -> {out_path}")


if __name__ == "__main__":
    main()
