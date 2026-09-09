"""Generic API booking connector — универсальный REST клиент.

Используется когда у салона своя система записи с REST API.

Требуется конфигурация эндпоинтов в .env или в JSON-конфиге салона:

```json
{
  "list_services": {"method": "GET", "path": "/services", "response_path": "data"},
  "list_masters": {"method": "GET", "path": "/masters", "response_path": "data"},
  "list_slots":   {"method": "GET", "path": "/slots", "response_path": "data", "query": {"master_id": "{master_id}", "date": "{date}"}},
  "create_booking": {"method": "POST", "path": "/bookings", "body": {"master_id": "{master_id}", "service_id": "{service_id}", ...}}
}
```
"""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from phoneagent.config import get_settings
from phoneagent.connectors.base import BaseBookingConnector
from phoneagent.models.booking import (
    Booking,
    BookingRequest,
    Master,
    Service,
    ServiceCategory,
    Slot,
)
from phoneagent.utils import get_logger

logger = get_logger(__name__)


class GenericAPIBookingConnector(BaseBookingConnector):
    """Универсальный REST-коннектор."""

    name = "generic_api"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.settings = get_settings()
        self._client: httpx.AsyncClient | None = None
        # Конфиг эндпоинтов (можно загрузить из файла)
        self._config = config or self._default_config()

    def _default_config(self) -> dict[str, Any]:
        return {
            "list_services": {"method": "GET", "path": "/services", "response_path": "data"},
            "list_masters": {"method": "GET", "path": "/masters", "response_path": "data"},
            "list_slots": {
                "method": "GET",
                "path": "/slots",
                "response_path": "data",
            },
            "create_booking": {"method": "POST", "path": "/bookings", "response_path": "data"},
        }

    async def connect(self) -> None:
        if not self.settings.booking_api_url:
            msg = "BOOKING_API_URL not configured"
            raise RuntimeError(msg)
        headers = {}
        if self.settings.booking_api_token:
            headers["Authorization"] = f"Bearer {self.settings.booking_api_token}"
        self._client = httpx.AsyncClient(
            base_url=self.settings.booking_api_url,
            headers=headers,
            timeout=30.0,
        )
        logger.info("generic_api_connected", url=self.settings.booking_api_url)

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()

    def _fill_template(self, template: Any, params: dict[str, Any]) -> Any:
        """Подставляет `{param}` из params в шаблон body (рекурсивно, dict/list/str).

        `"{master_id}"` как значение целиком заменяется на сам объект params["master_id"]
        (без приведения к строке), внутри более длинной строки — через str.format.
        """
        if isinstance(template, str):
            if template.startswith("{") and template.endswith("}") and template[1:-1] in params:
                return params[template[1:-1]]
            safe = {k: v for k, v in params.items() if v is not None}
            return template.format(**safe)
        if isinstance(template, dict):
            return {k: self._fill_template(v, params) for k, v in template.items()}
        if isinstance(template, list):
            return [self._fill_template(v, params) for v in template]
        return template

    def _extract(self, data: dict[str, Any] | list[Any], path: str) -> Any:
        """Извлекает данные по dot-нотации."""
        if not path:
            return data
        current: Any = data
        for part in path.split("."):
            if isinstance(current, list):
                try:
                    idx = int(part)
                    current = current[idx]
                    continue
                except (ValueError, IndexError):
                    return None
            if isinstance(current, dict):
                current = current.get(part)
            else:
                return None
        return current

    @retry(
        retry=retry_if_exception_type(httpx.ConnectError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, max=4),
        reraise=True,
    )
    async def _call(self, endpoint_name: str, **params: Any) -> Any:
        # Ретраим только ConnectError (соединение не установилось — запрос точно
        # не дошёл до сервера салона). Таймауты/5xx после отправки НЕ ретраим:
        # для create_booking повторная отправка при неясном исходе может
        # задвоить запись.
        if self._client is None:
            msg = "Connector not connected"
            raise RuntimeError(msg)
        cfg = self._config[endpoint_name]
        method = cfg["method"]
        path = cfg["path"]
        response_path = cfg.get("response_path", "")

        # Подставляем параметры в path/query/body
        safe_params = {k: v for k, v in params.items() if v is not None}
        formatted_path = path.format(**safe_params)
        query = {k: v for k, v in safe_params.items() if k not in ("method", "path")}
        body_template = cfg.get("body")
        # Без явного шаблона body в конфиге — шлём все переданные параметры как есть.
        body = self._fill_template(body_template, params) if body_template is not None else safe_params

        if method == "GET":
            response = await self._client.get(formatted_path, params=query)
        elif method == "POST":
            response = await self._client.post(formatted_path, json=body)
        else:
            msg = f"Unsupported method: {method}"
            raise ValueError(msg)

        response.raise_for_status()
        data = response.json()
        return self._extract(data, response_path)

    async def list_services(self) -> list[Service]:
        raw = await self._call("list_services")
        if not raw:
            return []
        return [self._parse_service(s) for s in raw]

    def _parse_service(self, raw: dict[str, Any]) -> Service:
        return Service(
            id=str(raw.get("id")),
            name=str(raw.get("name", raw.get("title", ""))),
            duration_minutes=int(raw.get("duration_minutes", raw.get("duration", 60))),
            price=float(raw["price"]) if raw.get("price") else None,
            description=raw.get("description"),
        )

    async def list_masters(self, service_id: str | None = None) -> list[Master]:
        raw = await self._call("list_masters", service_id=service_id)
        if not raw:
            return []
        return [self._parse_master(m) for m in raw]

    def _parse_master(self, raw: dict[str, Any]) -> Master:
        return Master(
            id=str(raw.get("id")),
            name=str(raw.get("name", raw.get("full_name", ""))),
            rating=float(raw["rating"]) if raw.get("rating") else None,
            description=raw.get("description"),
        )

    async def list_slots(
        self,
        master_id: str,
        target_date: date,
        service_id: str | None = None,
    ) -> list[Slot]:
        raw = await self._call(
            "list_slots",
            master_id=master_id,
            date=target_date.isoformat(),
            service_id=service_id,
        )
        if not raw:
            return []
        return [self._parse_slot(s, master_id, service_id) for s in raw]

    def _parse_slot(
        self,
        raw: dict[str, Any],
        master_id: str,
        service_id: str | None,
    ) -> Slot:
        from datetime import datetime
        return Slot(
            id=str(raw.get("id")),
            master_id=str(raw.get("master_id", master_id)),
            service_id=str(raw.get("service_id", service_id)) if service_id else None,
            start_at=datetime.fromisoformat(raw["start_at"]),
            duration_minutes=int(raw.get("duration_minutes", 60)),
            available=bool(raw.get("available", True)),
        )

    async def create_booking(self, request: BookingRequest) -> Booking:
        from datetime import datetime
        raw = await self._call(
            "create_booking",
            master_id=request.master_id,
            service_id=request.service_id,
            date=request.date.isoformat(),
            time=request.time,
            client_phone=request.client_phone,
            client_name=request.client_name,
        )
        # Парсим ответ от API салона
        booking_id = str(raw.get("id", "")) if isinstance(raw, dict) else ""
        return Booking(
            id=booking_id,
            salon_id=self.settings.salon_id,
            service=Service(id=request.service_id, name="Service", duration_minutes=60),
            master=Master(id=request.master_id, name="Master"),
            slot=Slot(
                id=f"{request.master_id}-{request.date.isoformat()}",
                master_id=request.master_id,
                service_id=request.service_id,
                start_at=datetime.combine(request.date, datetime.min.time()),
                duration_minutes=60,
            ),
            client_phone=request.client_phone,
            client_name=request.client_name,
        )

    async def get_booking(self, booking_id: str) -> Booking | None:
        msg = "GenericAPI: get_booking — реализация зависит от API салона"
        raise NotImplementedError(msg)


def build_generic_connector_from_yaml(yaml_path: str) -> GenericAPIBookingConnector:
    """Загружает конфиг эндпоинтов из YAML и создаёт коннектор."""
    import yaml  # type: ignore
    with open(yaml_path) as f:
        config = yaml.safe_load(f)
    return GenericAPIBookingConnector(config=config)


__all__ = ["GenericAPIBookingConnector", "ServiceCategory", "build_generic_connector_from_yaml"]