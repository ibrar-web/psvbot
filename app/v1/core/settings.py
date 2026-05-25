import os

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

MONGO_URL = os.getenv(
    "MONGO_URL",
    "mongodb+srv://alpha_multiteant_user:oJI521378EtcfNEB@multitenant-cluster.krts4k2.mongodb.net/?appName=multitenant-cluster",
)
MONGO_DB = os.getenv("MONGO_DB", "alphagraphics-queue-service")

QUOTE_SUMMARY_STORAGE_ROOT = os.getenv(
    "PRINTSMITH_QUOTE_SUMMARY_STORAGE_ROOT",
    str("estimates"),
).strip()

GOOGLE_APPLICATION_CREDENTIALS = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
BUCKET_NAME = os.getenv("BUCKET_NAME", "").strip()
BUCKET_REGION = os.getenv("BUCKET_REGION", "us-east1").strip()
MACHINE_NAME = os.getenv("MACHINE_NAME", "").strip()
QUEUE_ENFORCE_MACHINE_ASSIGNMENT = (
    os.getenv("QUEUE_ENFORCE_MACHINE_ASSIGNMENT", "true").strip().lower()
    == "true"
)
MAIN_SERVER_API_BASE_URL = os.getenv("MAIN_SERVER_API_BASE_URL", "").strip().rstrip("/")
MAIN_SERVER_API_TOKEN = os.getenv("MAIN_SERVER_API_TOKEN", "").strip()
QUEUE_WORKER_CONCURRENCY = int(os.getenv("QUEUE_WORKER_CONCURRENCY", "4"))
QUEUE_MAX_ATTEMPTS = int(os.getenv("QUEUE_MAX_ATTEMPTS", "4"))
QUEUE_POLL_INTERVAL_SECONDS = float(os.getenv("QUEUE_POLL_INTERVAL_SECONDS", "0.2"))
QUEUE_PROCESSING_STALE_SECONDS = int(
    os.getenv("QUEUE_PROCESSING_STALE_SECONDS", "900")
)
QUEUE_RECOVERY_INTERVAL_SECONDS = int(
    os.getenv("QUEUE_RECOVERY_INTERVAL_SECONDS", "60")
)

PRINTSMITH_URL = os.getenv("PRINTSMITH_URL", "").strip()
PRINTSMITH_USERNAME = os.getenv("PRINTSMITH_USERNAME", "").strip()
PRINTSMITH_PASSWORD = os.getenv("PRINTSMITH_PASSWORD", "").strip()
PRINTSMITH_COMPANY = os.getenv("PRINTSMITH_COMPANY", "").strip()
