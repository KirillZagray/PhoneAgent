"""Общие FastAPI-зависимости для авторизации входящих запросов.

ponytail: общий shared-secret вместо честной per-provider проверки подписи
(HMAC у Twilio, отдельный механизм у Voximplant/Asterisk). Апгрейд —
реализовать `X-Twilio-Signature` по алгоритму Twilio, когда появится
интеграция с реальным провайдером.
"""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, status

from phoneagent.config import get_settings


async def require_api_token(authorization: str | None = Header(default=None)) -> None:
    """Требует `Authorization: Bearer <api_auth_token>` для клиентских эндпоинтов.

    Если `API_AUTH_TOKEN` не задан — проверка пропускается (локальная разработка).
    """
    settings = get_settings()
    if not settings.api_auth_token:
        return

    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:]

    if not hmac.compare_digest(token, settings.api_auth_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API token",
        )


async def require_webhook_secret(x_webhook_secret: str | None = Header(default=None)) -> None:
    """Требует `X-Webhook-Secret` для входящих вебхуков от провайдеров телефонии.

    Если `WEBHOOK_SECRET` не задан — проверка пропускается (локальная разработка).
    """
    settings = get_settings()
    if not settings.webhook_secret:
        return

    if not x_webhook_secret or not hmac.compare_digest(x_webhook_secret, settings.webhook_secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing webhook secret",
        )


__all__ = ["require_api_token", "require_webhook_secret"]
