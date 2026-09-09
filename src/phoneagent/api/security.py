"""Общие FastAPI-зависимости для авторизации входящих запросов.

ponytail: общий shared-secret вместо честной per-provider проверки подписи
(HMAC у Twilio, отдельный механизм у Voximplant/Asterisk). Апгрейд —
реализовать `X-Twilio-Signature` по алгоритму Twilio, когда появится
интеграция с реальным провайдером.

Пустой секрет = проверка отключена. В APP_ENV=production приложение с пустыми
секретами не стартует (см. main.lifespan) — fail-closed.
"""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, status

from phoneagent.config import get_settings


def secret_matches(provided: str | None, expected: str) -> bool:
    """Constant-time сравнение секретов. bytes — compare_digest на str падает
    TypeError на не-ASCII (→ 500 вместо 401/4401). Публичная — используется и
    HTTP-зависимостями ниже, и WS-роутом моста (api/media_ws.py), у которого
    нет Depends() на HTTP-заголовки."""
    return hmac.compare_digest((provided or "").encode(), expected.encode())


async def require_api_token(authorization: str | None = Header(default=None)) -> None:
    """Требует `Authorization: Bearer <api_auth_token>` для клиентских эндпоинтов."""
    expected = get_settings().api_auth_token
    if not expected:
        return

    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:]

    if not secret_matches(token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API token",
        )


async def require_webhook_secret(x_webhook_secret: str | None = Header(default=None)) -> None:
    """Требует `X-Webhook-Secret` для входящих вебхуков от провайдеров телефонии."""
    expected = get_settings().webhook_secret
    if not expected:
        return

    if not secret_matches(x_webhook_secret, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing webhook secret",
        )


__all__ = ["require_api_token", "require_webhook_secret", "secret_matches"]
