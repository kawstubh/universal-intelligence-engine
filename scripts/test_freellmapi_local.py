"""Interactive, secret-safe FreeLLMAPI smoke test. Never writes the key to disk."""
from getpass import getpass
import os
from time import perf_counter

from universal_intelligence_engine.contracts import IntelligenceRequest
from universal_intelligence_engine.providers import OpenAIResponsesReasoningProvider

key = getpass("Paste the FreeLLMAPI unified key (input hidden): ").strip()
if not key:
    raise SystemExit("No key entered.")

os.environ["REASONING_PROVIDER"] = "freellmapi"
os.environ["REASONING_MODEL"] = "auto:balanced"
os.environ["REASONING_BASE_URL"] = "http://127.0.0.1:31415/v1"
os.environ["FREELLMAPI_API_KEY"] = key

request = IntelligenceRequest(
    goal="Reply with exactly ORALYN-FREELLMAPI-OK and nothing else.",
    context={"synthetic_test": "oralyn_freellmapi_smoke_test"},
    constraints={"data_classification": "synthetic"},
)
started = perf_counter()
try:
    result = OpenAIResponsesReasoningProvider().reason(request, [])
except Exception as exc:
    print(f"FreeLLMAPI smoke test failed: {type(exc).__name__}: {exc}")
    raise SystemExit(1)
finally:
    key = None
    os.environ.pop("FREELLMAPI_API_KEY", None)

elapsed_ms = round((perf_counter() - started) * 1000)
print(f"Response: {result.answer}")
print(f"Provider: {result.metadata.get('provider')}")
print(f"Model route: {result.metadata.get('model')}")
print(f"Usage: {result.metadata.get('usage')}")
print(f"Latency ms: {elapsed_ms}")
if result.answer.strip() != "ORALYN-FREELLMAPI-OK":
    raise SystemExit("Unexpected response; do not enable the integration.")
print("PASS: local FreeLLMAPI adapter completed the fixed synthetic test.")
