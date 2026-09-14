# pari-tennis — live-аналитика тенниса (Python 3.8+, только stdlib)

Автономный сбор live-линии pari.ru по открытому API + поиск валуйных паттернов.
Без браузера, без платных ключей, работает везде где есть Python и интернет.

## Быстрый старт (ПК)

```sh
git clone <repo> && cd pari-tennis
python3 pari_line.py --once --tennis        # один снимок: все live-матчи, счета, кэфы
python3 pari_line.py --loop 10 --out line.jsonl   # вечный сбор каждые 10 сек
python3 pari_view.py --file line.jsonl      # читаемый отчёт: эфир + движение
python3 pari_patterns.py --files line.jsonl # брейки, триггеры 2/3/7, симметрия сетов
python3 pari_backtest.py --files "line*.jsonl"    # триггеры A/B/C/D
python3 pari_paper.py report                # бумажный P/L
```

Данные (paper.jsonl, profiles/, seen-файлы) по умолчанию лежат в `/public`
(наследие телефона); на ПК задай `PARI_DATA=/путь/к/данным`.

Зависимости: только Python stdlib. Опционально `scrapling` (pip) для
браузерного глубокого разбора одного матча (`pari_obzor.py` + chromedriver).

## APK-коллектор (`pari-apk/`, 20 КБ)

Фоновый сбор на Android: тот же API-протокол на Java (stdlib + org.json),
пишет `line-YYYY-ММ-ДД-HH.jsonl`, детект новых матчей, gzip старых.
Сборка без Gradle/SDK (`build.sh`, нужны `aapt2`, `d8`, `apksigner`, `android.jar` —
см. PORTING.md). На ПК не нужен — заменяется `pari_line.py --loop`.

## Формат данных

`{"ts","catv","n","matches":[{eid,tour,p1,p2,t1id,t2id,start,sets,set_scores,
game,serve,cm,mtimer,mdelay,blocked,stats,odds:[{m,f,v,pt}],n_odds}],
"fresh":[eid...],"finals":[{eid,comment,final}],"prematch":[...]}`
Кэфы — дифами (только изменения + часовой кадр). 921=П1, 923=П2.

## Правила денег

Горизонты только game/set/match/prematch. Контуры: LAB 0.5u, MAIN 1u, ASYM 1.5u.
Порог edge 12%. Точка выхода — до входа. 3 проигрыша в день = стоп.
Сначала бумага (`pari_paper.py`), деньги — по кривой вверх.
