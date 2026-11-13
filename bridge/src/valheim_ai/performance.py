"""Request timing without retaining prompts, answers or credentials."""
import math
import time


def ollama_statistics(data: dict) -> dict:
    def number(key):
        value = data.get(key)
        return value if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1e15 else None
    result = {}
    for source, target in (("load_duration", "load_seconds"), ("prompt_eval_duration", "prompt_seconds"),
                           ("eval_duration", "generation_seconds")):
        value = number(source)
        if value is not None: result[target] = round(value / 1e9, 3)
    for source, target in (("prompt_eval_count", "prompt_tokens"), ("eval_count", "output_tokens")):
        value = number(source)
        if value is not None: result[target] = int(value)
    duration, count = number("eval_duration"), number("eval_count")
    if duration and count is not None: result["tokens_per_second"] = round(count * 1e9 / duration, 2)
    return result


def accumulate_statistics(target, values):
    """One plan may resolve intent, check bulk scope and generate its action."""
    for key in ("load_seconds", "prompt_seconds", "generation_seconds", "prompt_tokens", "output_tokens"):
        if key in values: target[key] = round(target.get(key, 0) + values[key], 3)
    if target.get("generation_seconds", 0) > 0 and "output_tokens" in target:
        target["tokens_per_second"] = round(target["output_tokens"] / target["generation_seconds"], 2)


class Performance:
    def __init__(self):
        self.current = None
        self.started = 0.0
        self.last = None

    def begin(self, provider, model, purpose):
        record = {"provider": provider, "model": model, "purpose": purpose, "status": "running"}
        self.current = record
        self.started = time.monotonic()
        return record

    def finish(self, record, status):
        if self.current is not record: return
        record.update(status=status, request_seconds=round(time.monotonic() - self.started, 3))
        self.last = record.copy()
        self.current = None

    def snapshot(self):
        current = {**self.current, "elapsed_seconds": round(time.monotonic() - self.started, 1)} if self.current else None
        return {"current": current, "last": self.last}
