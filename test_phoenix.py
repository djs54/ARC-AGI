import time
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

resource = Resource(attributes={
    "service.name": "ARC-AGI",
    "openinference.project.name": "ARC-AGI"
})
provider = TracerProvider(resource=resource)
processor = SimpleSpanProcessor(OTLPSpanExporter(endpoint="http://127.0.0.1:6006/v1/traces"))
provider.add_span_processor(processor)
trace.set_tracer_provider(provider)

tracer = trace.get_tracer(__name__)
with tracer.start_as_current_span("test-span", attributes={"openinference.span.kind": "AGENT"}):
    time.sleep(0.1)
    
print("Sent trace")
