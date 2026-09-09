# Salon API OpenAPI Spec (пример)

Это пример ТЗ для системы записи салона. PhoneAgent GenericAPIBookingConnector
использует этот формат для интеграции с любой системой.

## Спецификация (YAML)

```yaml
# salon_api.yaml
endpoints:
  list_services:
    method: GET
    path: /v1/services
    response_path: data

  list_masters:
    method: GET
    path: /v1/masters
    response_path: data

  list_slots:
    method: GET
    path: /v1/slots
    response_path: data

  create_booking:
    method: POST
    path: /v1/bookings
    response_path: data
```

## Использование

```python
from phoneagent.connectors.generic_api import build_generic_connector_from_yaml

connector = build_generic_connector_from_yaml("examples/salon_api.yaml")
```

## Ожидаемые форматы ответов

### list_services
```json
{
  "data": [
    {
      "id": "haircut_women",
      "name": "Стрижка женская",
      "duration_minutes": 60,
      "price": 2500.0,
      "description": "Стрижка любой сложности"
    }
  ]
}
```

### list_masters
```json
{
  "data": [
    {
      "id": "m1",
      "name": "Анна",
      "rating": 4.9,
      "description": "Топ-мастер, 10 лет опыта"
    }
  ]
}
```

### list_slots
```json
{
  "data": [
    {
      "id": "slot-123",
      "master_id": "m1",
      "service_id": "haircut_women",
      "start_at": "2026-03-15T14:00:00",
      "duration_minutes": 60,
      "available": true
    }
  ]
}
```

### create_booking
```json
{
  "data": {
    "id": "booking-123"
  }
}
```

## Готовые адаптеры

Если у вас одна из популярных систем — есть готовый адаптер (в разработке):

| Система | Доля рынка РФ | Адаптер | Статус |
|---------|---------------|---------|--------|
| **YClients** | ~40% | yclients.py | TODO |
| **Dikidi** | ~25% | dikidi.py | TODO |
| **Altegio** | ~15% | altegio.py | TODO |
| **1С:БИТ.Салон** | ~10% | 1c_bit.py | TODO |
| **Своя система** | — | generic_api.py | Ready |

Если ваша система не из списка — используйте `generic_api.py` + YAML-конфиг.