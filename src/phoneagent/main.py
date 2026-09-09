"""FastAPI application entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

import uvicorn
from fastapi import FastAPI

from phoneagent import __version__
from phoneagent.api import admin_router, trigger_router, webhooks_router
from phoneagent.config import get_settings
from phoneagent.utils import configure_logging, get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Lifespan-обработчик FastAPI."""
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
    yield
    logger.info("phoneagent_shutting_down")


def create_app() -> FastAPI:
    """Создаёт FastAPI приложение."""
    settings = get_settings()
    app = FastAPI(
        title="PhoneAgent",
        version=__version__,
        description="Универсальный микросервис для автоматизации телефонных записей с AI-ассистентом",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
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