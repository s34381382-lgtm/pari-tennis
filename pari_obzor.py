#!/usr/bin/env python3
"""PARI live-tennis: ВСЕ данные матча.
Табы рынков (Популярное/Матч/сеты/Роспись) + статистика по периодам
(Весь матч/1 сет/2 сет/...) + ход матча по очкам + счёт.

  python3 pari_obzor.py "<url>"                    # полный снимок -> stdout JSON
  python3 pari_obzor.py "<url>" --loop 20 --out m.jsonl          # быстрый опрос каждые 20с
  python3 pari_obzor.py "<url>" --loop 20 --full 6 --out m.jsonl # + полный обход каждые 6 кругов
"""
import json, re, subprocess, sys, time, urllib.request

CD_URL = "http://127.0.0.1:9515"
UA = ("Mozilla/5.0 (Linux; Android 13; TECNO KJ6) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/136.0.0.0 Mobile Safari/537.36")


def wd(method, path, data=None, timeout=60, retries=4):
    import urllib.error
    last = None
    for a in range(retries):
        try:
            req = urllib.request.Request(
                CD_URL + path,
                data=json.dumps(data).encode() if data is not None else None,
                headers={"Content-Type": "application/json"}, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())["value"]
        except Exception as e:
            last = e
            time.sleep(3 + a * 3)
    raise last


def ensure_driver():
    try:
        urllib.request.urlopen(CD_URL + "/status", timeout=4).read()
        return
    except Exception:
        pass
    subprocess.run(["pkill", "-f", "[c]hromedriver.*9515"],
                   capture_output=True, timeout=10)
    time.sleep(2)
    subprocess.Popen(["setsid", "chromedriver", "--port=9515"],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    for _ in range(15):
        try:
            urllib.request.urlopen(CD_URL + "/status", timeout=4).read()
            return
        except Exception:
            time.sleep(1)
    raise RuntimeError("chromedriver не поднялся")


def new_session():
    return wd("POST", "/session", {"capabilities": {"alwaysMatch": {
        "browserName": "chrome", "goog:chromeOptions": {
            "binary": "/usr/bin/chromium",
            "args": ["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
                     "--disable-software-rasterizer", "--headless=new",
                     "--user-agent=" + UA]}}}})["sessionId"]


def js_click_all(sess, xpath):
    """Кликнуть ВСЕ элементы по xpath через JS. Возвращает сколько кликнуто."""
    return wd("POST", f"/session/{sess}/execute/sync", {"script": (
        "var els=document.evaluate(\"XPATH\",document,null,7,null);"
        "var n=0;for(var i=0;i<els.snapshotLength;i++){"
        "try{els.snapshotItem(i).scrollIntoView({block:'nearest'});"
        "els.snapshotItem(i).click();n++;}catch(e){}}return n;"
        .replace("XPATH", xpath)), "args": []}, timeout=30)


def js_click_first(sess, xpath):
    return wd("POST", f"/session/{sess}/execute/sync", {"script": (
        "var els=document.evaluate(\"XPATH\",document,null,7,null);"
        "var el=els.snapshotItem(0);"
        "if(el){el.scrollIntoView({block:'center'});el.click();return 1;}return 0;"
        .replace("XPATH", xpath)), "args": []}, timeout=30)


def scroll_all(sess, steps=12):
    for _ in range(steps):
        wd("POST", f"/session/{sess}/execute/sync",
           {"script": "window.scrollBy(0,2500);return 1;", "args": []}, timeout=30)
        time.sleep(1)


def title_of(html):
    m = re.search(r"<title>(.*?)</title>", html, re.S)
    return (m.group(1).strip() if m else "")


def texts_of(html):
    from scrapling.parser import Selector
    s = Selector(html)
    return [t.strip() for t in s.css("body ::text").getall() if t.strip()]


def parse_header(texts, title=""):
    """Счёт матча: sets, set_scores (завершённые сеты), current/tiebreak/game_points, server."""
    h = {}
    m = re.match(r"Ставки Live на (.+?) в PARI", title or "")
    if m and " - " in m.group(1):
        h["p1"], h["p2"] = [x.strip() for x in m.group(1).split(" - ", 1)]
    try:
        ilive = next(i for i, t in enumerate(texts)
                     if t == "Live" or (isinstance(t, str) and t.startswith("Live >")))
    except StopIteration:
        ilive = None
    zone = texts[ilive:ilive + 16] if ilive is not None else []
    rest = []
    for i, t in enumerate(zone):
        if re.fullmatch(r"(\d+):(\d+)", t or "") and i + 3 < len(zone) \
                and zone[i + 2] == "-":
            h["sets"] = [t.split(":")[0], t.split(":")[1]]
            h["p1"], h["p2"] = zone[i + 1], zone[i + 3]
            rest = zone[i + 4:]
            break
        if t in (h.get("p1"), h.get("p2")) and i + 3 < len(zone) \
                and re.fullmatch(r"\d+", zone[i + 2] or "") \
                and re.fullmatch(r"\d+", zone[i + 3] or "") \
                and zone[i + 1] in (h.get("p1"), h.get("p2")):
            h["sets"] = [zone[i + 2], zone[i + 3]]
            rest = zone[i + 4:]
            break
    done, cur = [], None
    n_done = (int(h["sets"][0]) + int(h["sets"][1])) if h.get("sets") else 99
    for t in rest:
        c = re.sub(r"[^\d-]", "", t or "")
        mm = re.fullmatch(r"(\d+)-(\d+)", c)
        if not mm:
            if t == "ПОДАЧА":
                break
            continue
        a, b = mm.group(1), mm.group(2)
        if len(done) < n_done and a != b:
            done.append([a, b])
        else:
            cur = [a, b]
            break
    if not done:  # строковый формат: "1 сет",7,6 / "2 сет",6,6 ...
        j = 0
        rows = []
        z2 = zone + (["Гейм"] if "Гейм" not in zone else [])
        while j + 2 < len(zone) and re.fullmatch(r"\d+ сет", zone[j] or ""):
            a, b = zone[j + 1], zone[j + 2]
            if re.fullmatch(r"\d{1,2}", a or "") and re.fullmatch(r"\d{1,2}", b or ""):
                rows.append([a, b])
                j += 3
            else:
                break
        # строковый формат идёт СРАЗУ за сетами: ищем "1 сет" рядом с sets
        for k, t in enumerate(zone):
            if t == "1 сет" and k + 2 < len(zone):
                a, b = zone[k + 1], zone[k + 2]
                if re.fullmatch(r"\d{1,2}", a or "") and re.fullmatch(r"\d{1,2}", b or ""):
                    rows = []
                    kk = k
                    while kk + 2 < len(zone) and re.fullmatch(r"\d+ сет", zone[kk] or ""):
                        a2, b2 = zone[kk + 1], zone[kk + 2]
                        if re.fullmatch(r"\d{1,2}", a2 or "") and re.fullmatch(r"\d{1,2}", b2 or ""):
                            rows.append([a2, b2])
                            kk += 3
                        else:
                            break
                    break
        if rows:
            n = n_done if n_done != 99 else len(rows)
            done = [r for r in rows[:n] if r[0] != r[1]]
            if len(rows) > len(done):
                left = rows[len(done)]
                if left[0] == left[1]:
                    h["current_games"] = left  # 6-6: текущий сет, очки ниже из строки Гейм
                else:
                    cur = left
    if done:
        h["set_scores"] = done
    if cur:
        ia, ib = int(cur[0]), int(cur[1])
        if ia > 7 or ib > 7 or "тай-брейк" in zone:
            h["tiebreak"] = cur
        elif "Гейм" in zone:
            h["game_points"] = cur
        else:
            h["current_games"] = cur
    for i, t in enumerate(zone):
        if t == "ПОДАЧА" and i + 1 < len(zone):
            h["server"] = zone[i + 1]
            break
        if t == "Гейм" and i + 2 < len(zone) and not h.get("game_points") \
                and not h.get("tiebreak"):
            a, b = zone[i + 1], zone[i + 2]
            if re.fullmatch(r"\d+|A|А|40|30|15|00", a or "") \
                    and re.fullmatch(r"\d+|A|А|40|30|15|00", b or ""):
                if "тай-брейк" in zone:
                    h["tiebreak"] = [a, b]
                else:
                    h["game_points"] = [a, b]
    return h


def parse_stats(html):
    stats = {}
    for m in re.finditer(r'<div[^>]*class="wrapper[^"]*"[^>]*>', html):
        attrs = dict(re.findall(r'([\w-]+)="([^"]*)"', m.group(0)))
        if attrs.get("text"):
            stats[attrs["text"]] = {"p1": attrs.get("first", ""),
                                    "p2": attrs.get("second", "")}
    serve = {}
    tx = texts_of(html)
    for i, t in enumerate(tx):
        if t == "to 40" and i + 11 < len(tx):
            serve[tx[i + 1]] = {"to0": tx[i + 2], "to15": tx[i + 3],
                                "to30": tx[i + 4], "to40": tx[i + 5]}
            serve[tx[i + 6]] = {"to0": tx[i + 7], "to15": tx[i + 8],
                                "to30": tx[i + 9], "to40": tx[i + 10]}
            break
    return stats, serve


def parse_timeline(html):
    tl = []
    for m in re.finditer(
            r'component="([A-Za-z]+)"([^>]*)>(.*?)</div>\s*</div>', html, re.S):
        comp, attrs, inner = m.groups()
        a = dict(re.findall(r'([\w-]+)="([^"]*)"', attrs))
        label = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", inner)).strip()
        tl.append({"type": comp, "score": a.get("timetext", "").strip(),
                   "period": a.get("period", ""), "event": label})
    return tl


def parse_odds(texts, ban=()):
    """Рынки текущей вкладки: Название -> {исход: кэф}."""
    odds = {}
    for j in range(len(texts) - 4):
        t0, t1, t2, t3, t4 = texts[j:j + 5]
        if (re.fullmatch(r"\d+\.\d+", t2 or "")
                and re.fullmatch(r"\d+\.\d+", t4 or "")
                and not re.fullmatch(r"\d+\.\d+", t1 or "")
                and len(t1 or "") > 2 and len(t3 or "") > 2
                and not re.fullmatch(r"[\d\s.,()%+–\-:]+", t0 or "")
                and len(t0 or "") < 60 and (t0 or "") not in ban):
            odds.setdefault(t0, {})[t1] = t2
            odds[t0][t3] = t4
    # одиночные исходы вида Название/параметр/Б/кэф/М/кэф (тоталы)
    for j in range(len(texts) - 5):
        t0, t1, t2, t3, t4, t5 = texts[j:j + 6]
        if ((t2 == "Больше" or t2 == "Б") and (t4 == "Меньше" or t4 == "М")
                and re.fullmatch(r"\d+\.\d+", t3 or "")
                and re.fullmatch(r"\d+\.\d+", t5 or "")):
            odds.setdefault(t0, {})[f"{t1} Б"] = t3
            odds[t0][f"{t1} М"] = t5
    # точные счета вида Название-рынка, "2:0", 2.20, "0:2", – (битые пропускаем)
    cur_title = None
    for j, t in enumerate(texts):
        if t in ("Точный счет", "Точный счёт", "Точный счет сета", "Точный счёт сета",
                 "Счёт сета", "Счет сета"):
            cur_title = t
            continue
        if cur_title and re.fullmatch(r"\d+:\d+", t or "") \
                and j + 1 < len(texts) \
                and re.fullmatch(r"\d+\.\d+", texts[j + 1] or ""):
            odds.setdefault(cur_title, {})[t] = texts[j + 1]
        if cur_title and t in ("Роспись", "Популярное", "Матч", "Победа в\u00a0матче"):
            cur_title = None
    return odds


def market_tabs(html):
    """Все вкладки групп рынков: [(testid, название)]."""
    tabs = []
    for m in re.finditer(
            r'data-testid="(tabItem_\d+)"[^>]*>.*?caption--[^>]*>([^<]+)<', html):
        tabs.append((m.group(1), m.group(2).strip()))
    return tabs


def stat_periods(html):
    """Все переключатели периодов статистики (Весь матч/1 сет/2 сет...)."""
    tx = texts_of(html)
    periods = []
    for t in tx:
        if t == "Весь матч" or re.fullmatch(r"\d+ сет", t):
            if t not in periods:
                periods.append(t)
    return periods


def scroll_top(sess):
    """Наверх страницы: иначе виртуализированная лента очков размонтирована из DOM."""
    try:
        wd("POST", f"/session/{sess}/execute/sync",
           {"script": "window.scrollTo(0,0);return 1;", "args": []}, timeout=30)
        time.sleep(2)
    except Exception:
        pass


def full_sweep(sess):
    """Полный обход: все табы рынков + все периоды статистики + таймлайн."""
    snap = {"ts": int(time.time())}
    scroll_top(sess)
    html = wd("GET", f"/session/{sess}/source", timeout=60)
    tx = texts_of(html)
    snap["header"] = parse_header(tx, title_of(html))
    if not snap["header"].get("set_scores"):
        open("/tmp/dbg_header.html", "w", encoding="utf-8").write(html)
    players = [snap["header"].get("p1", ""), snap["header"].get("p2", "")]
    snap["timeline"] = parse_timeline(html)

    # 1) статистика по всем периодам
    snap["stats_by_period"] = {}
    for per in stat_periods(html):
        js_click_all(sess, f"//*[text()='{per}']")
        time.sleep(3)
        h2 = wd("GET", f"/session/{sess}/source", timeout=60)
        st, serve = parse_stats(h2)
        snap["stats_by_period"][per] = {"stats": st, "serve_table": serve}

    # 2) кэфы по всем табам рынков
    snap["odds_by_tab"] = {}
    for testid, name in market_tabs(html):
        js_click_first(
            sess, f"//*[@data-testid='{testid}']")
        time.sleep(4)
        scroll_all(sess, steps=8)
        h3 = wd("GET", f"/session/{sess}/source", timeout=60)
        snap["odds_by_tab"][name] = parse_odds(texts_of(h3), ban=players)
    # 3) вернуться на ленту очков, чтобы быстрые опросы видели таймлайн
    try:
        js_click_first(sess, "//*[text()='Ход матча']")
        time.sleep(3)
    except Exception:
        pass
    return snap


def quick_poll(sess):
    """Быстрый опрос без кликов: счёт + таймлайн + кэфы активной вкладки."""
    scroll_top(sess)
    html = wd("GET", f"/session/{sess}/source", timeout=60)
    tx = texts_of(html)
    hdr = parse_header(tx, title_of(html))
    players = [hdr.get("p1", ""), hdr.get("p2", "")]
    return {"ts": int(time.time()), "header": hdr,
            "timeline": parse_timeline(html),
            "odds": parse_odds(tx, ban=players)}


def open_match(sess, url):
    wd("POST", f"/session/{sess}/url", {"url": url}, timeout=90)
    for _ in range(30):
        if "Обзор" in wd("GET", f"/session/{sess}/source", timeout=60):
            break
        time.sleep(2)
    for attempt in range(4):  # клик + проверка что Обзор реально открылся
        js_click_first(sess, "//div[text()='Обзор']")
        time.sleep(5)
        html = wd("GET", f"/session/{sess}/source", timeout=60)
        if "Весь матч" in html or "Ход матча" in html:
            return
    raise RuntimeError("вкладка Обзор не открылась")


def main():
    args = sys.argv[1:]
    if not args or "-h" in args or "--help" in args:
        print(__doc__)
        return
    url = args[0]
    loop = int(args[args.index("--loop") + 1]) if "--loop" in args else 0
    full_every = int(args[args.index("--full") + 1]) if "--full" in args else 5
    out = args[args.index("--out") + 1] if "--out" in args else None

    ensure_driver()
    sess = new_session()
    try:
        open_match(sess, url)

        def emit(snap):
            for _ in range(3):
                try:
                    line = json.dumps(snap, ensure_ascii=False)
                    if out:
                        with open(out, "a", encoding="utf-8") as f:
                            f.write(line + "\n")
                    else:
                        print(line, flush=True)
                    return
                except Exception as e:
                    print(f"emit failed: {e}", file=sys.stderr, flush=True)
                    time.sleep(5)

        if loop > 0:
            n, open_fails = 0, 0
            while True:
                try:
                    emit(full_sweep(sess) if n % full_every == 0 else quick_poll(sess))
                    open_fails = 0
                except Exception as e:
                    print(f"round {n} failed: {e}", file=sys.stderr, flush=True)
                    try:
                        wd("DELETE", f"/session/{sess}")
                    except Exception:
                        pass
                    time.sleep(20)
                    try:
                        ensure_driver()
                        sess = new_session()
                        open_match(sess, url)
                        open_fails = 0
                    except Exception as e2:
                        open_fails += 1
                        print(f"reopen failed x{open_fails}: {e2}",
                              file=sys.stderr, flush=True)
                        if open_fails >= 10:
                            print("матч кончился или страница недоступна — выхожу",
                                  file=sys.stderr, flush=True)
                            return
                        time.sleep(30)
                        continue
                n += 1
                time.sleep(loop)
        else:
            emit(full_sweep(sess))
    finally:
        if not loop:
            try:
                wd("DELETE", f"/session/{sess}")
            except Exception:
                pass


if __name__ == "__main__":
    main()
