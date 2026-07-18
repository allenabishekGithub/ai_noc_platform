"""Prometheus metrics and optional OpenTelemetry tracing for the agent API."""
import logging
import os
import time
from typing import Any

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.responses import Response

LOGGER = logging.getLogger(__name__)

HTTP_REQUESTS = Counter(
    "tfs_agent_http_requests_total",
    "Completed HTTP requests handled by the Agentic Module.",
    ("method", "path", "status"),
)
HTTP_DURATION = Histogram(
    "tfs_agent_http_request_duration_seconds",
    "HTTP request duration handled by the Agentic Module.",
    ("method", "path"),
)


def route_path(request: Any) -> str:
    """Return a low-cardinality route template after FastAPI routing."""
    route = request.scope.get("route")
    return getattr(route, "path", None) or request.url.path


def observe_http_request(request: Any, status_code: int, started_at: float) -> None:
    """Record one request using its route template instead of unbounded IDs."""
    path = route_path(request)
    method = request.method
    HTTP_REQUESTS.labels(method=method, path=path, status=str(status_code)).inc()
    HTTP_DURATION.labels(method=method, path=path).observe(
        max(0.0, time.perf_counter() - started_at)
    )


def metrics_response() -> Response:
    """Return the Prometheus text exposition payload for ``/metrics``."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


def instrument_fastapi(app: Any, service_name: str) -> None:
    """Enable OTLP traces when an endpoint is configured.

    Metrics do not depend on an OTLP collector. Leaving
    ``OTEL_EXPORTER_OTLP_ENDPOINT`` empty deliberately disables tracing so the
    application remains usable before the observability platform is deployed.
    """
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if not endpoint:
        LOGGER.info("OpenTelemetry tracing disabled: no OTLP endpoint configured")
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        resource = Resource.create({"service.name": service_name})
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True))
        )
        trace.set_tracer_provider(provider)
        FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
        HTTPXClientInstrumentor().instrument(tracer_provider=provider)
        LOGGER.info("OpenTelemetry tracing enabled for %s -> %s", service_name, endpoint)
    except Exception as exc:  # observability must never stop control-plane startup
        LOGGER.warning("OpenTelemetry setup failed; continuing without traces: %s", exc)
