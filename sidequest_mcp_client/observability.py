"""Observability bridge for HippoCampy/Campy helpers consumed by ARC_AGI.

This module provides a small observability shim that optionally integrates
with Phoenix (OpenTelemetry) for live trace capturing.
"""

import os
import datetime
from contextlib import AbstractContextManager
from typing import Any, Mapping, Optional

# OTEL imports (optional dependencies)
try:
    from opentelemetry import trace
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    HAS_OTEL = True
except ImportError:
    HAS_OTEL = False
    SERVICE_NAME = "service.name"
    Resource = None
    TracerProvider = None
    SimpleSpanProcessor = None
    OTLPSpanExporter = None


class _NoopSpan(AbstractContextManager):
    def __enter__(self) -> "_NoopSpan":
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def set_attribute(self, *args, **kwargs):
        return None

    def set_attributes(self, attrs: Mapping[str, Any]):
        return None

    def add_event(self, *args, **kwargs):
        return None


class Observability:
    """Observability surface with optional Phoenix OTEL support.

    Enabled when either:
      - PHOENIX_ENABLE=1 is set in the environment, OR
      - config["observability"]["enabled"] is True.

    ARC smoke runs auto-enable this via run_single_puzzle.py when the
    phoenix and opentelemetry packages are importable, unless the user
    explicitly sets [observability] enabled = false in their config.
    """

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        # Support both env and explicit config
        self.enabled = str(os.environ.get("PHOENIX_ENABLE", "0")) == "1"
        if not self.enabled:
             self.enabled = bool(self.config.get("observability", {}).get("enabled", False))
        
        self._tracer = None

        if self.enabled and HAS_OTEL:
            endpoint = os.environ.get("PHOENIX_ENDPOINT", "http://127.0.0.1:6006/v1/traces")
            # Canonical Phoenix project for ARC_AGI smoke runs; override via PHOENIX_PROJECT env.
            project = os.environ.get("PHOENIX_PROJECT", "ARC-AGI")
            
            try:
                resource = Resource(attributes={
                    SERVICE_NAME: project,
                    "openinference.project.name": project
                })
                provider = TracerProvider(resource=resource)
                processor = SimpleSpanProcessor(OTLPSpanExporter(endpoint=endpoint))
                provider.add_span_processor(processor)
                
                # Set the global tracer provider if not already set
                if not isinstance(trace.get_tracer_provider(), TracerProvider):
                    trace.set_tracer_provider(provider)
                self._tracer = trace.get_tracer(__name__)
            except Exception:
                import logging
                logger = logging.getLogger("observability")
                if os.environ.get("_A016_AUTO_ENABLED_PHOENIX"):
                    logger.warning("Auto-enabled Phoenix tracer failed to initialize; disabling.", exc_info=True)
                else:
                    logger.exception("Failed to initialize OTEL/Phoenix tracer")
                self.enabled = False
        else:
            if self.enabled and not HAS_OTEL:
                import logging
                logging.getLogger("observability").warning("PHOENIX_ENABLE=1 but opentelemetry-sdk not installed.")
                self.enabled = False

    def _sanitize_attrs(self, attrs: Optional[Mapping[str, Any]]) -> dict[str, Any]:
        if not attrs:
            return {}
        sanitized = {}
        for k, v in attrs.items():
            if isinstance(v, (str, int, float, bool)):
                sanitized[k] = v
            elif isinstance(v, (list, tuple)) and all(isinstance(x, (str, int, float, bool)) for x in v):
                sanitized[k] = list(v)
            elif v is None:
                continue
            else:
                import json
                try:
                    sanitized[k] = json.dumps(v)
                except Exception:
                    sanitized[k] = str(v)
        return sanitized

    def span(self, name: str, attrs: Optional[Mapping[str, Any]] = None) -> AbstractContextManager:
        if self.enabled and self._tracer:
            return self._tracer.start_as_current_span(name, attributes=self._sanitize_attrs(attrs))
        return _NoopSpan()

    def emit_structured_event(self, name: str, attrs: Optional[Mapping[str, Any]] = None) -> None:
        if self.enabled and self._tracer:
            # For point-in-time trace events, we create a very short-lived span.
            # If there's an active context, OpenTelemetry will naturally attach this as a child.
            with self._tracer.start_as_current_span(name, attributes=self._sanitize_attrs(attrs)):
                pass
        return None

    def shutdown(self) -> None:
        if self.enabled and HAS_OTEL:
            provider = trace.get_tracer_provider()
            if hasattr(provider, "force_flush"):
                try:
                    provider.force_flush()
                except Exception:
                    pass
            if hasattr(provider, "shutdown"):
                try:
                    provider.shutdown()
                except Exception:
                    pass


REQUIRED_DECISION_FIELDS: list[str] = []
REQUIRED_OUTCOME_FIELDS: list[str] = []


def canonical_span_name(name: str) -> str:
    """Normalize a span name into a canonical dotted form."""
    return name.replace(" ", ".")


def ensure_contract_fields(data: Mapping[str, Any], fields: list[str], strict: bool = False, defaults: Optional[Mapping[str, Any]] = None) -> Mapping[str, Any]:
    """No-op contract validator."""
    res = dict(data)
    if defaults:
        for k, v in defaults.items():
            res.setdefault(k, v)
    return res


def build_observability(config: Optional[dict] = None) -> Observability:
    """Return a production-ready Observability implementation."""
    return Observability(config)
