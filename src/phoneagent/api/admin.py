"""Admin API — healthcheck, статистика, конфигурация."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from phoneagent.api.security import require_api_token
from phoneagent.config import get_settings

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/health", summary="Healthcheck")
async def health() -> dict[str, str]:
    """Возвращает статус сервиса."""
    return {"status": "ok", "service": "phoneagent"}


@router.get(
    "/config",
    summary="Текущая конфигурация (без секретов)",
    dependencies=[Depends(require_api_token)],
)
async def config() -> dict[str, str]:
    """Возвращает активную конфигурацию (провайдеры, без API-ключей)."""
    settings = get_settings()
    return {
        "app_env": settings.app_env.value,
        "telephony_provider": settings.telephony_provider.value,
        "stt_provider": settings.stt_provider.value,
        "tts_provider": settings.tts_provider.value,
        "llm_provider": settings.llm_provider.value,
        "llm_model": settings.anthropic_model if settings.llm_provider.value == "anthropic" else settings.openai_model,
        "booking_connector": settings.booking_connector.value,
        "salon_id": settings.salon_id,
        "default_language": settings.default_language,
    }


@router.get("/version", summary="Версия")
async def version() -> dict[str, str]:
    from phoneagent import __version__
    return {"version": __version__}