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
3. Cloud Tasks calls `POST /execute-task` on this VM
4. The task payload can include `callback_url`; after the bot finishes, this service posts the result payload to that URL

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
.venv/bin/python -m uvicorn main:app --reload
```

## Required Env Vars

```bash
API_BEARER_TOKEN=change-me
MONGO_URL=mongodb://localhost:27017
MONGO_DB=alphagraphics_db
BUCKET_NAME=your-gcs-bucket
GOOGLE_APPLICATION_CREDENTIALS=/absolute/path/to/service-account.json
PRINTSMITH_URL=https://your-company.printsmithvision.com/PrintSmith/PrintSmith.html
PRINTSMITH_USERNAME=psv-user
PRINTSMITH_PASSWORD=psv-password
PRINTSMITH_COMPANY=your-company
```

## Example Cloud Task Request

```bash
curl -X POST http://127.0.0.1:8000/execute-task \
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

`POST /api/v1/bot/execute-task` is also available for callers that prefer the versioned API prefix. Both task endpoints are allowlisted for Cloud Tasks delivery.
# psvbot
