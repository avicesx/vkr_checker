# API загрузки и статуса проверки

API принимает ВКР в формате DOCX и методичку в формате DOCX или PDF. Размер
каждого файла ограничен 20 МиБ. Формат проверяется по расширению и содержимому;
заголовку `Content-Type` запроса API не доверяет.

Для запуска необходим PostgreSQL. Задайте строку подключения в `DATABASE_URL`
и каталог для загруженных файлов в `UPLOAD_STORAGE_DIR`:

```bash
python -m pip install -r requirements.txt
DATABASE_URL='postgresql+psycopg://vkr_checker:password@localhost:5432/vkr_checker_db' \
UPLOAD_STORAGE_DIR=/path/to/persistent/uploads \
python -m uvicorn api.main:app --host 0.0.0.0 --port 8000
```

Статусы проверок хранятся в PostgreSQL, файлы — в `UPLOAD_STORAGE_DIR`.
В Docker Compose каталог загрузок подключён как постоянный том.

## Эндпоинты

`POST /api/v1/checks` принимает multipart-поля `thesis` (`.docx`) и
`guidelines` (`.docx` или `.pdf`). В ответе `201 Created` возвращаются UUID,
начальный статус `uploaded` и время создания в UTC.

`GET /api/v1/checks/{check_id}` возвращает текущий статус проверки или `404`,
если UUID неизвестен.

В перечислении статусов контракта OpenAPI также зарезервированы значения
`queued`, `processing`, `completed` и `failed` для будущего фонового обработчика.
Сейчас загрузка создаёт проверку со статусом `uploaded`; переходы между статусами
и поля результата или ошибки пока не реализованы.

FastAPI публикует интерактивную документацию по адресу `/docs`, а схему OpenAPI —
по адресу `/openapi.json`. Файл `openapi.json` в репозитории проверяется на
соответствие схеме приложения. Обновляйте его при изменении эндпоинтов или
моделей.

Пересоздать файл из каталога `backend/` можно командой:

```bash
python -c 'import json; from pathlib import Path; from api.main import app; Path("openapi.json").write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")'
```
