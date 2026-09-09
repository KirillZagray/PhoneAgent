"""Pydantic модели для PhoneAgent."""

from phoneagent.models.booking import (
    Booking,
    Master,
    Service,
    Slot,
)
from phoneagent.models.call import (
    CallEvent,
    CallRef,
    CallStatus,
    CallStatusEnum,
)
from phoneagent.models.booking import Master, Service, Slot  # noqa: F401
from phoneagent.models.conversation import (
    ConversationState,
    ConversationStep,
    Message,
    Role,
)

# Разрешаем forward references (Service/Master/Slot определены в другом модуле)
ConversationState.model_rebuild()

__all__ = [
    "Booking",
    "Master",
    "Service",
    "Slot",
    "CallEvent",
    "CallRef",
    "CallStatus",
    "CallStatusEnum",
    "ConversationState",
    "ConversationStep",
    "Message",
    "Role",
]
