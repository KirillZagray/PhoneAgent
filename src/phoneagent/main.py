"""FastAPI application entry point."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from phoneagent import __version__
from phoneagent.api import admin_router, trigger_router, webhooks_router
from phoneagent.config import Environment, get_settings
from phoneagent.core.orchestrator import build_orchestrator
from phoneagent.utils import configure_logging, get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Lifespan-обработчик FastAPI: поднимает один общий orchestrator на всё приложение."""
    settings = get_settings()
    configure_logging()
    logger.info(
        "phoneagent_starting",
        version=__version__,
        env=settings.app_env.value,
        telephony=settings.telephony_provider.value,
        stt=settings.stt_provider.value,
        tts=settings.tts_provider.value,
        llm=settings.llm_provider.value,
        booking=settings.booking_connector.value,
    )
    app.state.orchestrator = await build_orchestrator()
    yield
    await app.state.orchestrator.aclose()
    logger.info("phoneagent_shutting_down")


def create_app() -> FastAPI:
    """Создаёт FastAPI приложение."""
    settings = get_settings()
    is_dev = settings.app_env == Environment.DEVELOPMENT
    app = FastAPI(
        title="PhoneAgent",
        version=__version__,
        description="Универсальный микросервис для автоматизации телефонных записей с AI-ассистентом",
        lifespan=lifespan,
        docs_url="/docs" if is_dev else None,
        redoc_url="/redoc" if is_dev else None,
        openapi_url="/openapi.json" if is_dev else None,
    )

    # Routers
    app.include_router(admin_router)
    app.include_router(trigger_router)
    app.include_router(webhooks_router)

    return app


# Module-level app для uvicorn
app = create_app()


def main() -> None:
    """CLI entry point."""
    settings = get_settings()
    uvicorn.run(
        "phoneagent.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.app_env.value == "development",
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()