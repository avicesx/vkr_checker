# Автоматический AI-нормоконтролер ВКР для проверки оформления и структуры

## Локальный запуск

Для запуска API и PostgreSQL нужны Docker и Docker Compose:

```bash
docker compose up --build
```

После запуска API доступно по адресу `http://localhost:8000`, документация —
`http://localhost:8000/docs`, проверка готовности — `http://localhost:8000/health`.
Остановить сервисы можно командой `docker compose down`. Данные PostgreSQL и
загруженные файлы сохраняются в Docker volumes.

Для локальной разработки пароль PostgreSQL по умолчанию — `local-dev-password`.
Чтобы переопределить его, задайте `POSTGRES_PASSWORD` в окружении перед запуском.
Значение по умолчанию предназначено только для разработки.

Фронтенда в репозитории пока нет, поэтому Compose запускает API и базу данных.

## Тесты

Тесты используют PostgreSQL. Запустите базу из Compose:

```bash
docker compose up -d db
docker compose exec db createdb -U vkr_checker vkr_checker_test
```

Установите зависимости, задайте адрес PostgreSQL и запустите backend-тесты:

```bash
python -m pip install -r backend/requirements-dev.txt
cd backend
export DATABASE_URL='postgresql+psycopg://vkr_checker:local-dev-password@localhost:5432/vkr_checker_db'
export TEST_DATABASE_URL='postgresql+psycopg://vkr_checker:local-dev-password@localhost:5432/vkr_checker_test'
python -m pytest -q
```

GitHub Actions автоматически выполняет компиляцию backend-модулей и тесты при
каждом push и pull request. В CI запускается PostgreSQL, чтобы проверить работу
API с основной базой данных.
