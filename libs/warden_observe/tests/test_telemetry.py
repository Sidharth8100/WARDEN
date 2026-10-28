"""Context codec propagation and redaction without a global production provider."""

from itertools import pairwise
from unittest.mock import patch

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from warden_observe.telemetry import carrier, correlation, span

from warden_core.jobs import JobPayload


def test_queue_carrier_retains_trace_and_changes_parent_span() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    with patch(
        "warden_observe.telemetry.trace.get_tracer", return_value=provider.get_tracer("test")
    ):
        with span("api.request"):
            parent = carrier()
            api_id = correlation()["span_id"]
        payload = JobPayload(
            job_id="00000000-0000-0000-0000-000000000001",
            monitor_id="00000000-0000-0000-0000-000000000002",
            url="https://example.org",
            domain="example.org",
            traceparent=parent,
        )
        restored = JobPayload.decode(payload.encode())
        with span("scheduler.dispatch", restored.traceparent):
            scheduler_id = correlation()["span_id"]
            child = carrier()
        with span("fetcher.fetch", child):
            result = carrier()
        with span("processor.process", result):
            delivery = carrier()
        with span("webhook.dispatch", delivery):
            assert correlation()["span_id"] not in (api_id, scheduler_id)
    spans = list(exporter.get_finished_spans())
    assert len(spans) == 5 and len({s.context.trace_id for s in spans}) == 1
    for parent, child in pairwise(spans):
        assert child.parent.span_id == parent.context.span_id
    provider.shutdown()


def test_exception_message_is_never_exported_as_trace_event() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    with (
        patch(
            "warden_observe.telemetry.trace.get_tracer", return_value=provider.get_tracer("test")
        ),
        pytest.raises(ValueError, match="fixture-sensitive-text"),
        span("failure"),
    ):
        raise ValueError("fixture-sensitive-text")
    completed = exporter.get_finished_spans()[0]
    assert completed.status.status_code.name == "ERROR"
    assert not completed.events
    assert "fixture-sensitive-text" not in str(completed.attributes)
    provider.shutdown()
