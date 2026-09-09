"""API endpoints PhoneAgent."""

from phoneagent.api.admin import router as admin_router
from phoneagent.api.media_ws import router as media_ws_router
from phoneagent.api.trigger import router as trigger_router
from phoneagent.api.webhooks import router as webhooks_router

__all__ = ["admin_router", "media_ws_router", "trigger_router", "webhooks_router"]
