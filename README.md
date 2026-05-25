# PSVBot API

Standalone FastAPI service for the Playwright PrintSmith Vision bot.

## Project Structure

- `app/__init__.py`: FastAPI app factory
- `app/db/mongo.py`: MongoDB client setup
- `app/v1/common/storage_service.py`: GCP Cloud Storage upload helpers
- `app/v1/core/settings.py`: environment-backed settings
- `app/v1/middleware/auth.py`: bearer-token auth middleware
- `app/v1/routes.py`: API v1 route registration
- `app/v1/modules/bot/`: Playwright bot and Cloud Tasks APIs
- `app/v1/schemas/jobqueuemodel.py`: `job_queue` Beanie document
- `main.py`: local entrypoint

## Service Flow

1. Send `Authorization: Bearer <API_BEARER_TOKEN>` on protected routes
2. Create a Google Cloud Tasks HTTP task from the main server
3. Cloud Tasks calls `POST /enqueue-task` on this service
4. The service stores the payload in MongoDB and immediately returns `200`
5. Background workers claim MongoDB `tasks` documents and run Playwright in parallel

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
.venv/bin/python -m uvicorn main:app
```

Each container starts a MongoDB-backed worker pool. The default
`QUEUE_WORKER_CONCURRENCY=4` runs four Playwright jobs concurrently per
container. Cloud Tasks is only a dispatcher; it should call `/enqueue-task` and
does not wait for the Playwright flow to finish.

## Required Env Vars

```bash
API_BEARER_TOKEN=change-me
MONGO_URL=mongodb+srv://alpha_multiteant_user:oJI521378EtcfNEB@multitenant-cluster.krts4k2.mongodb.net/?appName=multitenant-cluster
MONGO_DB=alphagraphics-queue-service
QUEUE_WORKER_CONCURRENCY=4
QUEUE_MAX_ATTEMPTS=3
BUCKET_NAME=your-gcs-bucket
GOOGLE_APPLICATION_CREDENTIALS=/absolute/path/to/service-account.json
PRINTSMITH_URL=https://your-company.printsmithvision.com/PrintSmith/PrintSmith.html
PRINTSMITH_USERNAME=psv-user
PRINTSMITH_PASSWORD=psv-password
PRINTSMITH_COMPANY=your-company
```

## Example Cloud Task Request

```bash
curl -X POST http://127.0.0.1:8000/enqueue-task \
  -H 'Content-Type: application/json' \
  -d '{
    "queue_id": "job-queue-id",
    "callback_url": "https://service-a/api/result",
    "tenant_credentials": {
      "printsmith_url": "https://your-company.printsmithvision.com/PrintSmith/PrintSmith.html",
      "printsmith_username": "psv-user",
      "printsmith_password": "psv-password",
      "printsmith_company": "your-company"
    },
    "quote": {
      "_id": "quote-123",
      "tenant_id": "tenant-1",
      "contact_person": "John Doe",
      "contact_email": "customer@example.com"
    },
    "requirements": [
      {
        "stock_search": "13oz vinyl",
        "quantity": "1",
        "job_method": "Digital Color",
        "job_charges": []
      }
    ]
  }'
```

`POST /api/v1/bot/enqueue-task` is also available for callers that prefer the versioned API prefix. `POST /execute-task` and `POST /api/v1/bot/execute-task` remain as enqueue-compatible aliases.
# psvbot

# PSVBot API

Standalone FastAPI service for the Playwright PrintSmith Vision bot.

## Project Structure

- `app/__init__.py`: FastAPI app factory
- `app/db/mongo.py`: MongoDB client setup
- `app/v1/common/storage_service.py`: GCP Cloud Storage upload helpers
- `app/v1/core/settings.py`: environment-backed settings
- `app/v1/middleware/auth.py`: bearer-token auth middleware
- `app/v1/routes.py`: API v1 route registration
- `app/v1/modules/bot/`: Playwright bot and Cloud Tasks APIs
- `app/v1/schemas/jobqueuemodel.py`: `job_queue` Beanie document
- `main.py`: local entrypoint

## Service Flow

1. Send `Authorization: Bearer <API_BEARER_TOKEN>` on protected routes
2. Create a Google Cloud Tasks HTTP task from the main server
3. Cloud Tasks calls `POST /enqueue-task` on this service
4. The service stores the payload in MongoDB and immediately returns `200`
5. Background workers claim MongoDB `tasks` documents and run Playwright in parallel

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
.venv/bin/python -m uvicorn main:app
```

Each container starts a MongoDB-backed worker pool. The default
`QUEUE_WORKER_CONCURRENCY=4` runs four Playwright jobs concurrently per
container. Cloud Tasks is only a dispatcher; it should call `/enqueue-task` and
does not wait for the Playwright flow to finish.

## Required Env Vars

```bash
API_BEARER_TOKEN=change-me
MONGO_URL=mongodb+srv://alpha_multiteant_user:oJI521378EtcfNEB@multitenant-cluster.krts4k2.mongodb.net/?appName=multitenant-cluster
MONGO_DB=alphagraphics-queue-service
QUEUE_WORKER_CONCURRENCY=4
QUEUE_MAX_ATTEMPTS=3
BUCKET_NAME=your-gcs-bucket
GOOGLE_APPLICATION_CREDENTIALS=/absolute/path/to/service-account.json
PRINTSMITH_URL=https://your-company.printsmithvision.com/PrintSmith/PrintSmith.html
PRINTSMITH_USERNAME=psv-user
PRINTSMITH_PASSWORD=psv-password
PRINTSMITH_COMPANY=your-company
```

## Example Cloud Task Request

```bash
curl -X POST http://127.0.0.1:8000/enqueue-task \
  -H 'Content-Type: application/json' \
  -d '{
    "queue_id": "job-queue-id",
    "callback_url": "https://service-a/api/result",
    "tenant_credentials": {
      "printsmith_url": "https://your-company.printsmithvision.com/PrintSmith/PrintSmith.html",
      "printsmith_username": "psv-user",
      "printsmith_password": "psv-password",
      "printsmith_company": "your-company"
    },
    "quote": {
      "_id": "quote-123",
      "tenant_id": "tenant-1",
      "contact_person": "John Doe",
      "contact_email": "customer@example.com"
    },
    "requirements": [
      {
        "stock_search": "13oz vinyl",
        "quantity": "1",
        "job_method": "Digital Color",
        "job_charges": []
      }
    ]
  }'
```

`POST /api/v1/bot/enqueue-task` is also available for callers that prefer the versioned API prefix. `POST /execute-task` and `POST /api/v1/bot/execute-task` remain as enqueue-compatible aliases.
# psvbot
