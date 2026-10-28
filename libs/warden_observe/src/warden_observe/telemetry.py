"""Explicit tracing and low-cardinality metrics; no database/secret access."""

import logging
import os
from collections.abc import Iterator, MutableMapping
from contextlib import contextmanager

import structlog
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from prometheus_client import Counter, Gauge, Histogram, start_http_server

OPERATIONS = Counter(
    "warden_operations_total", "Completed pipeline operations", ("service", "outcome")
)
LATENCY = Histogram(
    "warden_operation_seconds",
    "Operation duration",
    ("service",),
    buckets=(0.01, 0.05, 0.1, 0.5, 1, 5, 15, 30, 60, 120, 300),
)
BACKLOG = Gauge("warden_crawl_queued", "Durable queued crawl receipts")
OLDEST = Gauge("warden_crawl_oldest_queued_seconds", "Age of oldest queued receipt")
SLO_BAD = Gauge("warden_crawl_slo_bad_ratio", "Fraction missing completion deadline", ("window",))
SLO_TOTAL = Gauge("warden_crawl_slo_population", "Eligible receipts in SLO window", ("window",))
SLO_SAMPLE_TIME = Gauge("warden_slo_sample_timestamp_seconds", "Last successful durable SLO sample")
REPLICA_LAG = Gauge(
    "warden_replica_replay_lag_seconds", "Replica replay lag; updates only after successful query"
)
_provider: TracerProvider | None = None
_metrics_started = False


def configure(service: str, exporter: SpanExporter | None = None) -> None:
    global _provider, _metrics_started
    if service != "warden-api":
        structlog.configure(
            processors=[
                structlog.contextvars.merge_contextvars,
                log_correlation,
                structlog.processors.add_log_level,
                structlog.processors.TimeStamper(fmt="iso", utc=True),
                structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        )
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
    if _provider is None and (endpoint or exporter):
        _provider = TracerProvider(resource=Resource.create({"service.name": service}))
        selected = exporter or OTLPSpanExporter(endpoint=endpoint, timeout=3)
        _provider.add_span_processor(BatchSpanProcessor(selected))
        trace.set_tracer_provider(_provider)
    port = os.environ.get("WARDEN_METRICS_PORT")
    if port and not _metrics_started:
        start_http_server(int(port), addr=os.environ.get("WARDEN_METRICS_BIND", "127.0.0.1"))
        _metrics_started = True


def flush() -> None:
    if _provider:
        _provider.force_flush(timeout_millis=5000)


def carrier() -> str | None:
    headers: dict[str, str] = {}
    TraceContextTextMapPropagator().inject(headers)
    return headers.get("traceparent")


@contextmanager
def span(name: str, parent: str | None = None) -> Iterator[trace.Span]:
    context = TraceContextTextMapPropagator().extract({"traceparent": parent}) if parent else None
    with trace.get_tracer("warden", "1").start_as_current_span(
        name, context=context, record_exception=False, set_status_on_exception=False
    ) as active:
        try:
            yield active
        except BaseException:
            active.set_status(trace.Status(trace.StatusCode.ERROR))
            raise


def correlation() -> dict[str, str]:
    current = trace.get_current_span().get_span_context()
    if not current.is_valid:
        return {}
    return {"trace_id": f"{current.trace_id:032x}", "span_id": f"{current.span_id:016x}"}


def log_correlation(
    logger: object, method: str, event: MutableMapping[str, object]
) -> MutableMapping[str, object]:
    event.update(correlation())
    return event
