# DocProc — автообработка сканов (Ubuntu, systemd)

Самостоятельный сервис: входящий PDF/скан → **распознавание штрихкода силами Python**
→ поиск клиента в SQLite → перемещение документа в папку клиента.
Работает как системный демон `docproc`, без внешних OCR/штрихкод-сервисов.

## Как это работает

```
incoming/<файл>
   │  (ожидание стабилизации файла)
   ▼
recognizer  ── pypdfium2 (PDF → изображение) + zxing-cpp (штрихкод)
   │
   ├─ штрихкод не найден ─────────────► errors/no_barcode/
   ├─ клиент не найден в БД ──────────► errors/client_not_found/
   ├─ ошибка распознавания ───────────► errors/processing_error/
   └─ клиент найден ► clients/<id> <ФИО>/<файл>
```

Работает круглосуточно. Исходные файлы **только перемещаются**, никогда не удаляются.

## Требования

- Ubuntu 22.04 LTS или 24.04 LTS.
- Python 3.11+ и пакет `python3-venv`.
- Пакеты из `requirements.txt`:
  - `pypdfium2` — рендер PDF в изображение;
  - `zxing-cpp` — распознавание штрихкодов (manylinux-колёса);
  - `pillow`, `numpy`.
- Дополнительных системных библиотек не требуется (`fcntl` — стандартная библиотека).

## Установка

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip

sudo mkdir -p /opt/docproc
# скопируйте проект в /opt/docproc (git clone / rsync / scp — на выбор)
sudo cp -a . /opt/docproc/

cd /opt/docproc
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

## Настройка

Всё задаётся в `config.ini`, в коде ничего не зашито. Пути — POSIX; `base_dir`
обязан быть абсолютным (иначе будет понятная ошибка при старте).

| Секция | Ключ | Назначение |
|--------|------|-----------|
| `[app]` | `base_dir` | корень проекта (по умолчанию `/opt/docproc`) |
| | `incoming_dir`, `clients_dir`, `errors_dir`, `logs_dir` | рабочие папки (относительно `base_dir`) |
| | `db_path`, `lock_path` | база и lock-файл |
| `[recognition]` | `formats` | `linear` (все линейные), `all` (вкл. QR/DataMatrix), либо список имён `Code128,Code39,EAN13` |
| | `dpi_candidates` | разрешения страницы, перебираются по порядку до первого успеха |
| | `max_pages` | `0` = все страницы |
| | `try_rotate` / `try_downscale` / `try_invert` | устойчивость к повороту/масштабу/инверсии |
| `[processing]` | `extensions` | какие файлы брать |
| | `stability_seconds`, `stability_checks` | ожидание, пока файл «допишется» |
| | `poll_interval_seconds` | период опроса демона |
| | `final_name_template` | имя папки клиента, напр. `{client_id} {full_name}` |
| | `duplicate_policy` | `counter` \| `timestamp` \| `overwrite` \| `skip` |
| `[logging]` | `level`, `max_bytes`, `backup_count` | журнал с ротацией |

При нескольких **разных** штрихкодах на документе берётся **первый**, факт пишется в лог.

## Использование

```bash
cd /opt/docproc

.venv/bin/python -m app.main run                  # демон (то же, что делает systemd)
.venv/bin/python -m app.main once                 # один проход по incoming
.venv/bin/python -m app.main status               # сводка по документам и клиентам
.venv/bin/python -m app.main recognize <файл>     # диагностика: какие штрихкоды видит
.venv/bin/python -m app.main import-clients <csv> # импорт клиентов (client_id,full_name,barcode)
.venv/bin/python -m app.main add-client <id> "<ФИО>" <barcode>
```

Генерация тестового PDF:

```bash
.venv/bin/python examples/make_sample.py 0020000101 incoming/sample.pdf
.venv/bin/python examples/make_sample.py --no-barcode incoming/blank.pdf
```

Самотест (в изолированной песочнице, реальные папки не трогает):

```bash
.venv/bin/python tests/selftest.py
```

## Автозапуск (systemd)

```bash
sudo bash /opt/docproc/scripts/install_service.sh
```

Скрипт:
- проверяет root и наличие `python3`/`config.ini`;
- создаёт системного пользователя `docproc` (если нет);
- создаёт `.venv` и ставит зависимости;
- создаёт рабочие каталоги и выставляет владельца `docproc:docproc` на проект
  (каталог `incoming/` не обходится рекурсивно, если это отдельное монтирование);
- генерирует юнит из `scripts/docproc.service` с реальным путём проекта,
  кладёт его в `/etc/systemd/system/docproc.service`, делает `daemon-reload`,
  `enable --now`.

Управление:

```bash
systemctl status docproc --no-pager
sudo systemctl restart docproc
sudo systemctl stop docproc
journalctl -u docproc -f                 # журнал systemd
tail -f /opt/docproc/logs/docproc.log    # файловый журнал приложения
```

Удаление сервиса (данные сохраняются):

```bash
sudo bash /opt/docproc/scripts/uninstall_service.sh
# вместе с системным пользователем:
sudo bash /opt/docproc/scripts/uninstall_service.sh --remove-user
```

Юнит: `Type=simple`, `Restart=on-failure`, `RestartSec=5`, `NoNewPrivileges=yes`.
При занятой блокировке второй экземпляр завершается с кодом 0, поэтому
`Restart=on-failure` не устраивает цикл перезапусков.

## База данных (SQLite)

Файл: `data/clients.db` (локальный диск; WAL включён).

- `clients(client_id INTEGER PK, full_name TEXT, barcode TEXT UNIQUE)` — штрихкод → клиент.
- `documents(id, filename, barcode, client_id, status, source_path, final_path, error, sha256, created_at, processed_at, updated_at)` — история обработки.

Статусы: `NEW`, `COMPLETED`, `NO_BARCODE`, `CLIENT_NOT_FOUND`, `ERROR`.
Ведущие нули штрихкода при поиске игнорируются (`0030012345` ↔ `30012345`).

## Миграция с прежней установки

1. Остановите старый процесс, чтобы БД была корректно закрыта.
2. Скопируйте **только** `data/clients.db` (файлы `-wal`/`-shm` отдельно не копируйте).
3. Не копируйте `.venv` — на Linux создайте новый (см. «Установка»).
4. Проверьте:

```bash
cd /opt/docproc
.venv/bin/python -m app.main status
```

Если базы нет — импортируйте клиентов из CSV:

```bash
.venv/bin/python -m app.main import-clients examples/clients_demo.csv
```

## Права и сетевые папки

- Каталог `incoming/` должен быть доступен пользователю `docproc` на чтение **и запись**
  (файлы из него перемещаются).
- Если `incoming/` — это SMB/NFS-монтирование, монтируйте с `uid=docproc,gid=docproc`
  (и правами на запись). База `data/clients.db` при этом должна оставаться на локальном диске.
- После ручного копирования файлов в `incoming/` при необходимости:
  `sudo chown -R docproc:docproc /opt/docproc/incoming`.

## Надёжность

- **Без потери документов** — только перемещение (`os.replace`), при сбое файл уходит в `errors/`.
- **Идемпотентность** — повторный `once` не создаёт дублей; повторно появившийся файл
  с тем же именем считается новым поступлением.
- **Один экземпляр** — `fcntl.flock` на `data/app.lock`; PID пишется в lock-файл.
- **Штатная остановка** — `SIGTERM`/`SIGINT` переводят демон в остановку, блокировка снимается в `finally`.
- **Восстановление после перезапуска** — при старте состояние сверяется (`recover`).
- **Ожидание стабилизации** — файл берётся в работу, только когда перестал расти.

## Диагностика

| Ситуация | Причина | Решение |
|---|---|---|
| Файл в `errors/client_not_found` | нет клиента с таким штрихкодом | добавить клиента и вернуть файл в `incoming/` |
| Файл в `errors/no_barcode` | штрихкод не распознан | пересканировать; поднять `dpi_candidates` (например `300,400,600`) |
| Файл в `errors/processing_error` | битый/неподдерживаемый файл | проверить файл, смотреть `logs/docproc.log` |
| Файл долго лежит в `incoming` | файл ещё пишется или сервис остановлен | проверить `systemctl status docproc` |
| Сервис не стартует | занят lock / неверный путь | `journalctl -u docproc -n 100 --no-pager` |

Полезно:

```bash
.venv/bin/python -m app.main recognize incoming/problem.pdf
.venv/bin/python -m app.main status
grep <имя_файла> logs/docproc.log
```
