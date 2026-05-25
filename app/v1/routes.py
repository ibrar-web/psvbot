from fastapi import APIRouter

from app.v1.modules.bot.api import router as bot_router

api_router = APIRouter()
api_router.include_router(bot_router, prefix="/bot")
