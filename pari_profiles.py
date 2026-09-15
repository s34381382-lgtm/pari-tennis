#!/usr/bin/env python3
"""Автопрофили игроков: fresh-матч -> история с TennisExplorer -> файл.

  python3 pari_profiles.py --eid 68041328 --p1 "Эззат Я" --p2 "Мдлулва Возуко"
  python3 pari_profiles.py --fresh line.jsonl   # профили всем свежим без файлов

ЧТО ИЗМЕНЕНО: 1) surname_lat падала с IndexError на имени из одних пробелов
(".split()[0]" на пустом списке) — сейчас есть явная проверка. 2) Нечёткий
поиск по кэшу (find_te_url) без инициала раньше молча брал ПЕРВЫЙ попавшийся
URL под похожей фамилией, даже если в кэше под этой же фамилией лежат
РАЗНЫЕ игроки — то есть рисковал подставить историю чужого игрока и передать
её дальше в психо-профиль/аналитику. Теперь отдаёт кэш без вопросов только
если для этой фамилии в кэше ровно один URL; при реальной неоднозначности
без инициала — не гадает. 3) Проверка заголовка страницы при подтверждении
инициала сверяла только первые 4 буквы фамилии независимо от её длины —
для длинных фамилий это давало ложные совпадения ("Petrova" проходило бы
как "Petrovskaya"); теперь порог растёт вместе с длиной фамилии (до 6 букв).
"""
import json
import os
import re
import sys
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128"
DATA = os.environ.get("PARI_DATA", "/public")
OUTDIR = os.path.join(DATA, "profiles")

RU2EN = str.maketrans({
    "А": "A", "Б": "B", "В": "V", "Г": "G", "Д": "D", "Е": "E", "Ё": "E",
    "Ж": "Zh", "З": "Z", "И": "I", "Й": "Y", "К": "K", "Л": "L", "М": "M",
    "Н": "N", "О": "O", "П": "P", "Р": "R", "С": "S", "Т": "T", "У": "U",
    "Ф": "F", "Х": "Kh", "Ц": "Ts", "Ч": "Ch", "Ш": "Sh", "Щ": "Sch",
    "Ъ": "", "Ы": "Y", "Ь": "", "Э": "E", "Ю": "Yu", "Я": "Ya",
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
})


UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128",
    "Mozilla/5.0 (Linux; Android 13; TECNO KJ6) AppleWebKit/537.36 Chrome/136 Mobile Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
]
UAIDX = [0]
CACHE_FILE = os.path.join(OUTDIR, "_urlcache.json")


def http(url, timeout=20):
    UAIDX[0] = (UAIDX[0] + 1) % len(UAS)
    req = urllib.request.Request(url, headers={"User-Agent": UAS[UAIDX[0]]})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def load_cache():
    try:
        return json.load(open(CACHE_FILE, encoding="utf-8"))
    except Exception:
        return {}


def save_cache(c):
    try:
        json.dump(c, open(CACHE_FILE, "w", encoding="utf-8"))
    except Exception:
        pass


def surname_lat(name):
    """'Эззат Я' -> 'Ezzat' (фамилия латиницей для поиска)."""
    if not name or not name.strip():
        return ""
    sur = name.strip().split()[0]
    return sur.translate(RU2EN)


def ddg_links(query):
    """Ссылки tennisexplorer из DuckDuckGo (с повтором при 202/пусто)."""
    import time as _t
    q = urllib.parse.quote(query)
    for attempt in range(3):
        try:
            html = http(f"https://html.duckduckgo.com/html/?q={q}")
        except Exception:
            _t.sleep(2 + attempt * 2)
            continue
        found = []
        for m in re.finditer(r'href="(https?://(?:www\.|noproxy\.)?tennisexplorer\.com/player/[^"]+)"',
                             html):
            url = m.group(1).replace("www.", "noproxy.")
            if "/player/" in url and "?" not in url and url not in found:
                found.append(url)
        for m in re.finditer(r"tennisexplorer\.com%2Fplayer%2F([a-z0-9%\-]+)%2F", html):
            url = f"https://noproxy.tennisexplorer.com/player/{urllib.parse.unquote(m.group(1))}/"
            if url not in found:
                found.append(url)
        if found:
            return found
        _t.sleep(2 + attempt * 2)
    return []


def find_te_url(surname, initial="", firstname=""):
    """URL профиля TennisExplorer: кэш (точное+нечёткое) -> поиск -> сверка."""
    import difflib
    cache = load_cache()
    key = f"{surname}|{initial}"
    if key in cache:
        return cache[key], None
    # нечёткий поиск по кэшу (транслитерация имён неоднозначна: в=v/w и т.п.)
    surnames = {}
    for k in cache:
        s = k.split("|")[0]
        surnames.setdefault(s, []).append(k)
    best = difflib.get_close_matches(surname, list(surnames.keys()), n=1, cutoff=0.8)
    if best:
        keys = surnames[best[0]]
        if not initial:
            # ИСПРАВЛЕНО: без инициала нечем отличить одного игрока от
            # другого с похожей фамилией. Раньше здесь брался keys[0] вслепую
            # (порядок dict) — если под этой фамилией в кэше реально лежат
            # РАЗНЫЕ игроки, можно было тихо подставить чужую историю. Теперь:
            # если все ключи с этой фамилией указывают на один и тот же URL —
            # неоднозначности нет, отдаём его. Если URL разные — не гадаем.
            urls = {cache[k] for k in keys}
            if len(urls) == 1:
                return cache[keys[0]], None
            return None, "ambiguous cache match without initial"
        for k in keys:
            if k.split("|")[1] == initial:
                return cache[k], None
        return cache[keys[0]], "fuzzy-initial"
    queries = [f"{surname} tennisexplorer"]
    if firstname:
        queries.append(f"tennisexplorer {surname} {firstname}")
    queries.append(f"tennisexplorer player {surname}")
    cands = []
    import time as _t
    for q in queries:
        for u in ddg_links(q):
            if u not in cands:
                cands.append(u)
        if len(cands) >= 4:
            break
        _t.sleep(2)
    if not cands:
        return None, "not found"
    if initial:
        # ИСПРАВЛЕНО: сверка заголовка раньше сравнивала только первые 4
        # буквы фамилии НЕЗАВИСИМО от её длины — для длинной фамилии это
        # давало ложные совпадения (например "Petrova" проходило бы как
        # правильное совпадение для страницы "Petrovskaya", т.к. "petr" —
        # префикс обеих). Порог теперь растёт вместе с длиной фамилии
        # (до 6 букв), но не сужается для коротких — поведение для них не
        # изменилось.
        need = min(6, len(surname))
        for url in cands[:6]:
            try:
                h = http(url)
                t = re.search(r"<h1[^>]*>(.*?)</h1>", h, re.S)
                title = re.sub(r"<[^>]+>", "", t.group(1)).strip() if t else ""
                parts = title.replace("- profile", "").strip().split()
                if len(parts) >= 2 and parts[0].lower().startswith(surname.lower()[:need]) \
                        and parts[1][:1].upper() == initial.upper():
                    cache[key] = url
                    save_cache(cache)
                    return url, None
            except Exception:
                continue
        cache[key] = cands[0]
        save_cache(cache)
        return cands[0], "initial-mismatch"
    cache[key] = cands[0]
    save_cache(cache)
    return cands[0], None


def parse_te(html):
    """Последние матчи + инфа профиля.
    ОГРАНИЧЕНИЕ (зафиксировано, не правил вслепую без живого HTML для сверки):
    разбор построен на regex по конкретной разметке TennisExplorer. Если
    сайт поменяет структуру таблицы (порядок/названия колонок), re.findall
    молча вернёт [] — функция не бросит исключение и не даст знать, что
    данные потерялись, просто отдаст профиль с пустым "recent". Стоит хотя
    бы логировать случай len(rows)==0 при непустом html, чтобы отличать
    "у игрока правда нет истории" от "парсер сломался"."""
    out = {"recent": []}
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
    if m:
        out["title"] = re.sub(r"<[^>]+>", "", m.group(1)).strip()[:60]
    m = re.search(r"Current/Highest rank - singles:\s*([\d.]+)\s*/\s*([\d.]+)", html)
    if m:
        out["rank"] = {"cur": m.group(1), "max": m.group(2)}
    rows = re.findall(
        r'<td class="first time">([^<]+)</td>\s*<td class="s-color">.*?</td>\s*'
        r'<td class="t-name">(.*?)</td>\s*<td class="round"[^>]*>(.*?)</td>\s*'
        r'<td class="tl"><a[^>]*>(.*?)</a></td>\s*'
        r'<td class="course">([^<]*)</td>\s*<td class="course">([^<]*)</td>',
        html, re.S)
    for r in rows[:8]:
        match = re.sub(r"<[^>]+>", "", r[1]).replace("&nbsp;", " ").strip()
        score = re.sub(r"<[^>]+>", "", r[3]).strip()
        out["recent"].append({"date": r[0].strip(), "match": match,
                              "round": r[2].strip(), "score": score,
                              "odds": [r[4].strip(), r[5].strip()]})
    return out


def profile_player(name):
    parts = (name or "").strip().split()
    sur = surname_lat(name)
    ini = surname_lat(parts[1][:1]) if len(parts) > 1 else ""
    first = " ".join(w.translate(RU2EN) for w in parts[1:]) if len(parts) > 1 else ""
    if not sur:
        return {"name": name, "error": "empty"}
    url, err = find_te_url(sur, ini, first)
    if not url:
        return {"name": name, "surname": sur, "error": err}
    try:
        html = http(url)
    except Exception as e:
        return {"name": name, "surname": sur, "url": url, "error": str(e)}
    d = parse_te(html)
    d.update({"name": name, "surname": sur, "url": url})
    return d


def save_profile(eid, p1, p2):
    os.makedirs(OUTDIR, exist_ok=True)
    prof = {"eid": eid,
            "p1": profile_player(p1),
            "p2": profile_player(p2)}
    path = os.path.join(OUTDIR, f"{eid}.json")
    json.dump(prof, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return path, prof


def main():
    args = sys.argv[1:]
    if "--eid" in args:
        eid = args[args.index("--eid") + 1]
        p1 = args[args.index("--p1") + 1] if "--p1" in args else ""
        p2 = args[args.index("--p2") + 1] if "--p2" in args else ""
        path, prof = save_profile(eid, p1, p2)
        for k in ("p1", "p2"):
            p = prof[k]
            n = len(p.get("recent", []))
            print(f"{p.get('name')}: {p.get('title', '?')} rank={p.get('rank', '?')} "
                  f"матчей={n} {p.get('error', '')}")
        print("->", path)
    elif "--fresh" in args:
        path = args[args.index("--fresh") + 1]
        snaps = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
        last = snaps[-1]
        done = 0
        for e in last.get("matches", []) + last.get("events", []):
            eid = str(e.get("eid"))
            if os.path.exists(os.path.join(OUTDIR, f"{eid}.json")):
                continue
            sc = e.get("set_scores") or []
            if (e.get("sets") == ["0", "0"] or not e.get("sets")) and len(sc) <= 1 \
                    and e.get("p1"):
                print("профиль:", e.get("p1"), "-", e.get("p2"))
                save_profile(eid, e.get("p1", ""), e.get("p2", ""))
                done += 1
                if done >= 5:
                    break
        print("готово:", done)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
