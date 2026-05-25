from motor.motor_asyncio import AsyncIOMotorClient

from app.v1.core.settings import MONGO_URL


def get_client() -> AsyncIOMotorClient:
    return AsyncIOMotorClient(MONGO_URL)
