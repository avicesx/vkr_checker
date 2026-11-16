# API загрузки и статуса проверки

API принимает ВКР в формате DOCX и методичку в формате DOCX или PDF. Размер
каждого файла ограничен 20 МиБ. Формат проверяется по расширению и содержимому;
заголовку `Content-Type` запроса API не доверяет.

Запуск из каталога `backend/`:

```bash
python -m pip install -r requirements.txt
CHECK_STORAGE_DIR=/path/to/persistent/check-data \
  python -m uvicorn api.main:app --host 0.0.0.0 --port 8000
```

В каталоге хранятся база `checks.sqlite3` и папка `files/`. При запуске в Docker
задайте `CHECK_STORAGE_DIR`, указав постоянный том.

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
