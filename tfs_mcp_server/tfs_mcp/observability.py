"""Prometheus metrics and optional OTLP tracing for the TFS MCP server."""
import logging
import os
import time
from typing import Any

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.responses import Response

LOGGER = logging.getLogger(__name__)

HTTP_REQUESTS = Counter(
    "tfs_mcp_http_requests_total",
    "Completed HTTP requests handled by the TFS MCP server.",
    ("method", "path", "status"),
)
HTTP_DURATION = Histogram(
    "tfs_mcp_http_request_duration_seconds",
    "HTTP request duration handled by the TFS MCP server.",
    ("method", "path"),
)
TOOL_CALLS = Counter(
    "tfs_mcp_tool_calls_total",
    "MCP tool calls handled by the TFS MCP server.",
    ("tool", "outcome"),
)


def metrics_response() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


def instrument_starlette(app: Any, service_name: str) -> None:
    """Add Prometheus request metrics and opt-in OTLP tracing to Starlette."""
    @app.middleware("http")
    async def prometheus_metrics(request, call_next):
        started_at = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            route = request.scope.get("route")
            path = getattr(route, "path", None) or request.url.path
            HTTP_REQUESTS.labels(
                method=request.method, path=path, status=str(status_code)
            ).inc()
            HTTP_DURATION.labels(method=request.method, path=path).observe(
                max(0.0, time.perf_counter() - started_at)
            )

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if not endpoint:
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(
            resource=Resource.create({"service.name": service_name})
        )
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True))
        )
        trace.set_tracer_provider(provider)
        app.add_middleware(OpenTelemetryMiddleware, tracer_provider=provider)
        LOGGER.info("OpenTelemetry tracing enabled for %s -> %s", service_name, endpoint)
    except Exception as exc:
        LOGGER.warning("OpenTelemetry setup failed; continuing without traces: %s", exc)
