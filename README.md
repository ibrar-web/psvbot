# PSVBot API

Standalone FastAPI service for the Selenium PrintSmith Vision bot.

## Project Structure

- `app/__init__.py`: FastAPI app factory
- `app/db/mongo.py`: MongoDB client setup
- `app/v1/common/storage_service.py`: GCP Cloud Storage upload helpers
- `app/v1/core/settings.py`: environment-backed settings
- `app/v1/middleware/auth.py`: bearer-token auth middleware
- `app/v1/routes.py`: API v1 route registration
- `app/v1/modules/bot/`: Selenium bot and queue APIs
- `app/v1/schemas/jobqueuemodel.py`: `job_queue` Beanie document
- `main.py`: local entrypoint

## Service Flow

1. Send `Authorization: Bearer <API_BEARER_TOKEN>` on protected routes
2. Call `POST /api/v1/bot/run-estimate` for direct bot execution, or `POST /api/v1/bot/process-queue` from the main server
3. The app also polls MongoDB every 5 minutes for `job_queue` records with `status=pending`

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

## Example Bot Request

```bash
curl -X POST http://127.0.0.1:8000/api/v1/bot/run-estimate \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer <API_BEARER_TOKEN>' \
  -d '{
    "credentials": {
      "printsmith_url": "https://your-company.printsmithvision.com/PrintSmith/PrintSmith.html",
      "username": "psv-user",
      "password": "psv-password",
      "company": "your-company"
    },
    "quote_record": {
      "quote_id": "sample-quote-1"
    }
  }'
```

## Example Queue Request

```bash
curl -X POST http://127.0.0.1:8000/api/v1/bot/process-queue \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer <API_BEARER_TOKEN>' \
  -d '{
    "queue": {
      "quotation_id": "quote-123",
      "tenant_id": "tenant-1",
      "user_email": "customer@example.com",
      "account_name": "Acme",
      "contact_person": "John Doe",
      "contact_email": "customer@example.com",
      "contact_phone": "+1-555-0100",
      "requirements": {
        "stock_search": "13oz vinyl",
        "quantity": "1",
        "job_charges": []
      }
    }
  }'
```
# psvbot
