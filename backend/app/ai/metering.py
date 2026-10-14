"""One bounded analysis budget, propagated into worker threads by asyncio."""
from contextvars import ContextVar

meter = ContextVar("logiq_ai_meter", default=None)
allow_provider = ContextVar("logiq_ai_allowed", default=True)


def record(input_tokens=0, output_tokens=0, embedding_tokens=0, reasoning=True):
    current = meter.get()
    if current is not None:
        current["input_tokens"] += input_tokens
        current["output_tokens"] += output_tokens
        current["embedding_tokens"] += embedding_tokens
        current["successes"] += 1
        if reasoning:
            current["reasoning_successes"] = current.get("reasoning_successes", 0) + 1
