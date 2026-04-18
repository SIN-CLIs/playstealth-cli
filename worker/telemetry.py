"""Optional OpenTelemetry integration.

Tracing is **opt-in**: unless ``OTEL_ENABLED=true`` is set and the
``opentelemetry`` packages are installed, every call in this module is a
zero-cost no-op. This keeps the default install footprint small.

Activate with::

    pip install -e '.[otel]'
    export OTEL_ENABLED=true
    export OTEL_EXPORTER_OTLP_ENDPOINT=http://collector:4317

Usage in application code::

    from worker.telemetry import trace_span

    async with trace_span("vision.classify", model="llama-3.2-90b-vision"):
        await call_nvidia_nim(...)
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any, Protocol

from worker.logging import get_logger

_log = get_logger(__name__)


class _SpanLike(Protocol):
    def set_attribute(self, key: str, value: Any) -> None: ...
    def record_exception(self, exc: BaseException) -> None: ...
    def end(self) -> None: ...


_TRACER: Any = None  # opentelemetry.trace.Tracer | None — lazy-loaded


def _enabled() -> bool:
    return os.environ.get("OTEL_ENABLED", "").lower() in {"1", "true", "yes"}


def _tracer() -> Any:
    global _TRACER  # noqa: PLW0603
    if _TRACER is not None:
        return _TRACER
    if not _enabled():
        return None
    try:
        from opentelemetry import (
            trace,  # type: ignore[import-not-found,import-untyped,unused-ignore]
        )
        from opentelemetry.sdk.resources import (
            Resource,  # type: ignore[import-not-found,import-untyped,unused-ignore]
        )
        from opentelemetry.sdk.trace import (
            TracerProvider,  # type: ignore[import-not-found,import-untyped,unused-ignore]
        )
        from opentelemetry.sdk.trace.export import (  # type: ignore[import-not-found,import-untyped,unused-ignore]
            BatchSpanProcessor,
        )
    except ImportError:
        _log.info("otel_disabled_missing_packages")
        return None

    service_name = os.environ.get("OTEL_SERVICE_NAME", "heypiggy-vision-worker")
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (  # type: ignore[import-not-found,import-untyped,unused-ignore]
                OTLPSpanExporter,
            )

            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        except ImportError:
            _log.warning("otel_exporter_missing", endpoint=endpoint)

    trace.set_tracer_provider(provider)
    _TRACER = trace.get_tracer("heypiggy.worker")
    _log.info("otel_enabled", service_name=service_name, endpoint=endpoint)
    return _TRACER


@contextmanager
def trace_span_sync(name: str, /, **attributes: Any) -> Iterator[_SpanLike | None]:
    """Sync variant of :func:`trace_span`. Returns ``None`` when OTel is off."""
    tracer = _tracer()
    if tracer is None:
        yield None
        return
    with tracer.start_as_current_span(name) as span:
        for k, v in attributes.items():
            span.set_attribute(k, v)
        try:
            yield span
        except BaseException as exc:
            span.record_exception(exc)
            raise


@asynccontextmanager
async def trace_span(name: str, /, **attributes: Any) -> AsyncIterator[_SpanLike | None]:
    """Async context manager for a trace span.

    Safe to use whether or not OpenTelemetry is installed/configured — it
    simply becomes a transparent pass-through.
    """
    tracer = _tracer()
    if tracer is None:
        yield None
        return
    with tracer.start_as_current_span(name) as span:
        for k, v in attributes.items():
            span.set_attribute(k, v)
        try:
            yield span
        except BaseException as exc:
            span.record_exception(exc)
            raise


__all__ = ["trace_span", "trace_span_sync"]
