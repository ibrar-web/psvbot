import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

APP_NAME = os.getenv("APP_NAME", "PSVBot API")
APP_VERSION = os.getenv("APP_VERSION", "0.1.0")
DEBUG = os.getenv("DEBUG", "false").lower() == "true"

API_BEARER_TOKEN = os.getenv("API_BEARER_TOKEN", "change-me")
API_BEARER_SUBJECT = os.getenv("API_BEARER_SUBJECT", "psvbot-service")
API_BEARER_ROLE = os.getenv("API_BEARER_ROLE", "service")

CORS_ALLOW_ORIGINS = os.getenv(
    "CORS_ALLOW_ORIGINS",
    os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"),
)

MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "alphagraphics-multitenant")

BOT_STORAGE_ROOT = Path(os.getenv("BOT_STORAGE_ROOT", "storage")).resolve()
QUOTE_SUMMARY_STORAGE_ROOT = os.getenv(
    "PRINTSMITH_QUOTE_SUMMARY_STORAGE_ROOT",
    str(BOT_STORAGE_ROOT / "estimates"),
).strip()

GOOGLE_APPLICATION_CREDENTIALS = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
BUCKET_NAME = os.getenv("BUCKET_NAME", "").strip()
BUCKET_REGION = os.getenv("BUCKET_REGION", "us-east1").strip()
QUEUE_POLL_INTERVAL_SECONDS = int(os.getenv("QUEUE_POLL_INTERVAL_SECONDS", "60"))
MAIN_SERVER_API_BASE_URL = os.getenv("MAIN_SERVER_API_BASE_URL", "").strip().rstrip("/")
MAIN_SERVER_API_TOKEN = os.getenv("MAIN_SERVER_API_TOKEN", "").strip()

PRINTSMITH_URL = os.getenv("PRINTSMITH_URL", "").strip()
PRINTSMITH_USERNAME = os.getenv("PRINTSMITH_USERNAME", "").strip()
PRINTSMITH_PASSWORD = os.getenv("PRINTSMITH_PASSWORD", "").strip()
PRINTSMITH_COMPANY = os.getenv("PRINTSMITH_COMPANY", "").strip()
