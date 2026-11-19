# ЦУТ Improvement Auto

Система учёта для СТО. Сервер работает на FastAPI и PostgreSQL, интерфейс собран на React и TypeScript. Репозиторий содержит исходный код, миграции Alembic и шаблон настроек.

## Установка на сервер Linux

Понадобятся Docker Engine с Compose v2 и домен, указывающий на сервер. Откройте TCP-порты 80 и 443 для Caddy. PostgreSQL и Uvicorn доступны внутри сети Compose. Caddy получает и обновляет сертификат HTTPS для указанного домена.

1. Скопируйте `.env.example` в `.env` и ограничьте доступ командой `chmod 600 .env`. Задайте два разных пароля, укажите домен в `CUT_SITE` и `ALLOWED_HOSTS`, проверьте `WORKSHOP_TIMEZONE`. Оставьте `MAX_DISPATCH_MODE=disabled` и уберите токен MAX на время настройки и восстановления данных.
2. Соберите образ, запустите PostgreSQL и примените миграции. Если переносите существующую базу, сначала выполните восстановление из раздела ниже.

   ```sh
   docker compose build app
   docker compose up -d --wait db
   docker compose run --rm app alembic upgrade head
   ```

3. На пустой базе создайте владельца. Скрипт запросит имя, логин и пароль интерактивно.

   ```sh
   docker compose run --rm app python scripts/bootstrap_owner.py
   ```

4. Запустите сервисы и убедитесь, что `/ready` отвечает `200` по HTTPS. `/health` проверяет наличие процесса. `/ready` проверяет соединение с PostgreSQL и актуальность миграций.

   ```sh
   docker compose up -d --wait
   docker compose ps
   ```

   Войдите владельцем, проверьте карточку клиента, заказ-наряд, PDF с кириллицей, отчёт по мастерам и загрузку фотографии. Собираемый образ использует исходники интерфейса текущей версии.

В Compose работает один процесс Uvicorn. Для MAX сначала проверьте привязку и уведомления с отдельным тестовым ботом, `MAX_DISPATCH_MODE=test` и номером тестового клиента в `MAX_TEST_CLIENT_NUMBER`. Проверьте статус отправки в интерфейсе и получение сообщения в чате. Статус успешной отправки означает, что MAX принял запрос. Неоднозначный результат требует ручной проверки.

После проверки остановите бота на прежней установке. Задайте рабочий токен на сервере и включите `MAX_DISPATCH_MODE=all` для рабочей базы с `ENVIRONMENT=production`. После изменения настроек примените `docker compose up -d --wait app proxy`.

При обновлении сделайте копию БД и файлов, соберите новый образ и примените `alembic upgrade head` до запуска новой версии. Проверьте `/ready` и вход. Для отката подготовьте предыдущие версии API и интерфейса, а также совместимую копию БД и данных. Проверяйте восстановление на отдельном экземпляре.

При переносе выданных заказов дата выдачи используется как дата завершения работ для прежних записей. В открытых заказах отметьте фактически завершённые работы после сверки. Отчёт учитывает календарную дату завершения в часовом поясе СТО.

## Резервное копирование и перенос

Копия включает дамп PostgreSQL и том `/srv/sto/data` с фотографиями. Чтобы они соответствовали друг другу, на время копирования остановите приложение. PostgreSQL продолжит работать.

```sh
(
  set -eu
  umask 077
  backup_dir="$(mktemp -d "$PWD/backup-$(date -u +%Y%m%d)-XXXXXX")"
  docker compose stop proxy app
  trap 'docker compose up -d --wait app proxy' EXIT
  docker compose exec -T db sh -ec 'pg_dump -U "$POSTGRES_USER" --format=custom --no-owner --no-acl "$POSTGRES_DB"' > "$backup_dir/postgresql.dump"
  docker compose run --no-deps --rm -T app tar -C /srv/sto/data -czf - . > "$backup_dir/data.tar.gz"
  (cd "$backup_dir" && sha256sum postgresql.dump data.tar.gz > SHA256SUMS)
  printf '%s\n' "$backup_dir"
)
```

Убедитесь, что обе команды копирования завершились успешно. Храните копию с ограниченным доступом в отдельном защищённом хранилище. Для проверки восстановления используйте свежий Compose-проект с отдельными томами и `.env.restore` с собственными паролями и отключённым MAX. Для переноса на новый рабочий сервер используйте нужный файл настроек и пустую целевую базу.

```sh
(
  set -eu
  backup_dir=/path/to/backup
  (cd "$backup_dir" && sha256sum -c SHA256SUMS)
  restore() { docker compose --env-file .env.restore -p cut-restore "$@"; }
  restore up -d --wait db
  restore exec -T db sh -ec \
    'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --role="$DB_USER" --no-owner --no-acl --exit-on-error --single-transaction' \
    < "$backup_dir/postgresql.dump"
  restore run --no-deps --rm -T app tar -C /srv/sto/data --no-same-owner -xzf - < "$backup_dir/data.tar.gz"
  restore run --rm app alembic upgrade head
  restore up -d --wait app
)
```

На восстановленном экземпляре сверьте количество клиентов, машин и заказов с исходным, откройте сохранённую фотографию и проверьте PDF и отчёт. При переносе со штатной Windows-копии используйте `postgresql.dump` и содержимое `data.zip`, сохранив относительный путь `data/dispute_photos`. Для приведённой команды `tar` архив должен содержать содержимое папки `data` в корне.

## Локальный запуск из исходников

Используйте Python 3.13, Node.js 22.12+ и PostgreSQL 17. В `.env` задайте параметры локальной БД и `ENVIRONMENT=development`, добавьте `localhost` в `ALLOWED_HOSTS`. Создайте пустую БД под прикладной ролью или восстановите тестовую копию. Если нужны PDF, установите системный шрифт DejaVu Sans с кириллицей.

```sh
python3.13 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
alembic upgrade head
uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

В отдельном терминале запустите интерфейс на `http://localhost:5173`.

```sh
npm --prefix frontend ci
npm --prefix frontend run dev
```

Для проверок выполните `python -m pytest`, `npm --prefix frontend run lint` и `npm --prefix frontend run build`. Чтобы FastAPI раздавал свежесобранный интерфейс вместо выбранного Windows-артефакта, передайте абсолютный путь к `frontend/dist` через `CUT_FRONTEND_DIST`.
