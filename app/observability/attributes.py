from collections.abc import Mapping
from typing import Any

from opentelemetry.trace import Span

from app.observability.privacy import filter_attributes


def set_safe_attributes(span: Span, attributes: Mapping[str, Any]) -> None:
    """Set only explicitly prepared, low-cardinality observability attributes."""

    for key, value in filter_attributes(attributes).items():
        span.set_attribute(key, value)


def error_attributes(category: object | None) -> dict[str, str]:
    if category is None:
        return {}
    value = getattr(category, "value", category)
    return {"error.category": str(value)}
