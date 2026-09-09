"""Фабрика booking-коннекторов."""

from __future__ import annotations

from phoneagent.config import PhoneAgentSettings, get_settings
from phoneagent.connectors.base import BaseBookingConnector
from phoneagent.utils import get_logger

logger = get_logger(__name__)


def build_booking_connector(
    settings: PhoneAgentSettings | None = None,
) -> BaseBookingConnector:
    """Создаёт booking connector по настройкам."""
    settings = settings or get_settings()
    connector = settings.booking_connector
    logger.info("building_booking_connector", connector=connector.value)

    if connector.value == "mock":
        from phoneagent.connectors.mock import MockBookingConnector
        return MockBookingConnector()

    if connector.value == "generic_api":
        from phoneagent.connectors.generic_api import GenericAPIBookingConnector
        return GenericAPIBookingConnector()

    if connector.value == "yclients":
        msg = "YClients connector — в разработке"
        raise NotImplementedError(msg)

    if connector.value == "dikidi":
        msg = "Dikidi connector — в разработке"
        raise NotImplementedError(msg)

    if connector.value == "altegio":
        msg = "Altegio connector — в разработке"
        raise NotImplementedError(msg)

    msg = f"Unknown booking connector: {connector}"
    raise ValueError(msg)