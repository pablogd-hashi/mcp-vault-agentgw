"""OpenTelemetry bootstrap for the agent: traces only, straight to the collector.

Same shape as init_telemetry() in pablogd-hashi/smolagents-otel-spiffe
(agents/shared/telemetry.py), without the metrics and log pipelines.
"""

from __future__ import annotations

import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

_initialised = False


def init_telemetry(service_name: str) -> trace.Tracer:
    global _initialised
    if not _initialised:
        provider = TracerProvider(
            resource=Resource.create(
                {"service.name": service_name, "deployment.environment": os.environ.get("DEPLOY_ENV", "local")}
            )
        )
        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(
                    endpoint=os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317"),
                    insecure=True,
                ),
                schedule_delay_millis=500,
            )
        )
        trace.set_tracer_provider(provider)
        try:
            from openinference.instrumentation.smolagents import SmolagentsInstrumentor

            SmolagentsInstrumentor().instrument(tracer_provider=provider)
        except ImportError:
            pass  # the tests use this module without the agent extra installed
        _initialised = True
    return trace.get_tracer(service_name)


def flush() -> None:
    provider = trace.get_tracer_provider()
    if hasattr(provider, "force_flush"):
        provider.force_flush()
