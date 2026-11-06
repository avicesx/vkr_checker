# Upload and check status API

The API accepts a thesis DOCX and a formatting guide in DOCX or PDF format.
Each file is limited to 20 MiB. File extensions and document signatures are
validated; the request's `Content-Type` header is not trusted.

Run the API from `backend/`:

```bash
python -m pip install -r requirements.txt
CHECK_STORAGE_DIR=/path/to/persistent/check-data \
  python -m uvicorn api.main:app --host 0.0.0.0 --port 8000
```

The storage directory contains `checks.sqlite3` and a `files/` directory. Set
`CHECK_STORAGE_DIR` to a persistent Docker volume when containerizing the API.

## Endpoints

`POST /api/v1/checks` accepts multipart form fields `thesis` (`.docx`) and
`guidelines` (`.docx` or `.pdf`). It returns `201 Created` with a UUID, initial
status `uploaded`, and a UTC creation timestamp.

`GET /api/v1/checks/{check_id}` returns that check's current status, or `404`
when the UUID is unknown.

The lifecycle enum in the OpenAPI contract also reserves `queued`, `processing`,
`completed`, and `failed` for the future background processor. Uploads currently
remain in `uploaded`; state transitions and result/error payloads are not part
of this upload-only implementation yet.

FastAPI serves the interactive docs at `/docs` and generated OpenAPI at
`/openapi.json`. The checked-in `openapi.json` is tested against the application
schema and should be regenerated when endpoints or models change.

Regenerate it from `backend/` with:

```bash
python -c 'import json; from pathlib import Path; from api.main import app; Path("openapi.json").write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")'
```
