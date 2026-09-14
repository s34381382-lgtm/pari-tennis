# PORTING: со смартфона на ПК (для ИИ-ассистента, который продолжит проект)

## Где это разрабатывалось

- **Устройство**: Tecno Spark 20 Pro (KJ6), HiOS 13.5, Android 13, 7.5 ГБ RAM
- **Среда**: приложение Acode → proot-контейнер Alpine Linux 3.21 (aarch64).
  Внутри proot мы uid=0, но это НЕ настоящий root: SELinux режет доступ
  (`/data/data` чужих приложений, часть `/sys`/`/proc` закрыты).
- **Аппаратные грабли**:
  - Виден только свой `/sdcard` через FUSE-фильтр (файлы других приложений скрыты).
    Полный доступ — только через `adb shell` (adbd на случайных портах 30000–49999,
    порт умирает каждые несколько минут, держать `adb_port.txt` и пересканировать).
  - OOM-киллер: opencode ~750 МБ RSS + headless chromium ~500 МБ. Фоновые процессы
    (`nohup`, `setsid`) умирают через ~10 минут. Долгое — только foreground-кусками
    или как Android-сервис (поэтому коллектор живёт в APK, а не в скриптах).
  - Системные вызовы иногда падают с `ENOSYS (Errno 38)` под давлением памяти —
    весь сетевой код обязан иметь ретраи (в `pari_line.py`/`CollectorService` есть).
  - `grep`/`find` — BusyBox (нет `--include`), `nslookup` кривой. Python есть (3.12).
  - VPN (SuperProxy) включён почти всегда; DNS резолвится через tun (10.x).
    Без VPN тоже работает, но смотреть лог первые 5 минут.

## Что где работает

| Компонент | Телефон | ПК |
|---|---|---|
| `pari_line.py` (сбор API) | да (бэкап) | **основной режим** |
| APK `pari-apk` | да (прод) | не нужен |
| `pari_view/patterns/backtest/paper/profiles` | да | да, без изменений |
| `pari_obzor.py` (браузер) | chromium+chromedriver из `apk` | Chrome/Chromedriver с официального сайта, путь к бинарнику поправить в коде |
| adb-пути (`localhost:xxxx`, `/sdcard/...`) | да | нет — данные локально |

## Перенос на ПК (чек-лист)

1. `git clone`, `PARI_DATA=/data/pari export`.
2. `python3 pari_line.py --loop 10 --out $PARI_DATA/line.jsonl` — прод-сбор.
3. `pari_backup.sh` не нужен (данные уже локальные); правило ротации почасовых
   файлов и gzip — оставить как есть.
4. Cron/systemd: `pari_view.py` (утренний дайджест), `pari_patterns.py`,
   `pari_backtest.py`, `pari_paper.py report` — раз в день.
5. `pari_profiles.py`: tennisexplorer может резать дата-центровые IP чаще,
   чем мобильные — при банах добавить паузы/прокси.
6. Сборка APK на ПК: нужен `aapt2` + `d8` (из Android SDK `build-tools` /
   `cmdline-tools`) + `apksigner` + `android.jar` (`platforms/android-34`).
   Либо открыть `pari-apk/` в Android Studio (структура пакета стандартная,
   `build.sh` показывает шаги).
7. Протокол API задокументирован в `PARI_SYSTEM.md` + `CollectorService.java`
   (комменты). Хосты пула иногда меняются — смотреть свежие в JS-бандле
   `.../webStaticPB/website/<ver>/matchCenter.entity.tennis.js` (имя чанка
   резолвится из `bootstrap.js`, карта `miniCssF`).

## Что НЕ переносить

- `adb_port.txt`, куки, токены (`.vault/`), `*_seen.json` можно, но лучше с нуля.
- `*.jsonl` данных (сотни МБ) — только свежие при желании; `.gitignore` их режет.
- `debug.keystore` из `pari-apk/` — приватный ключ подписи, НЕ коммитить.
