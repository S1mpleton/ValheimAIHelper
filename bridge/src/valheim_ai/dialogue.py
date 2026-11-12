"""Bounded, memory-only model I/O. No HTTP headers or reasoning fields."""
from collections import OrderedDict
import json
import re
import textwrap
import time

PART_LIMIT = 262144
PAGE_LINES = 12
LINE_WIDTH = 94
EXPORT_CHUNK = 16000


class Dialogue:
    def __init__(self, secrets):
        self.secrets = secrets
        self.records = OrderedDict()
        self.next_id = 0
        self.revision = 0

    def redact(self, text, credential=""):
        for secret in sorted((s for s in [*self.secrets(), credential] if s), key=len, reverse=True):
            text = text.replace(secret, "[REDACTED]")
        text = re.sub(r"(?:sk-|AIza|AQ\.)[A-Za-z0-9_-]{20,}", "[REDACTED]", text)
        text = re.sub(r"(?i)Bearer\s+[^\s\"\\]+", "Bearer [REDACTED]", text)
        text = re.sub(r"(?i)(api[_-]?key|access_token)\s*[=:]\s*[^\s,;\"\\]+", r"\1=[REDACTED]", text)
        return text

    def begin(self, provider, model, purpose, credential=""):
        self.next_id += 1
        record = Exchange(self, self.next_id, provider, model, purpose, credential)
        self.records[record.id] = record
        while len(self.records) > 8: self.records.popitem(last=False)
        self.revision += 1
        return record

    def clear(self):
        self.records.clear()
        self.revision += 1

    def index(self):
        return {"revision": self.revision, "entries": [r.summary() for r in reversed(self.records.values())]}

    def page(self, record, part, page):
        text = self.redact(getattr(record, part))
        if not text:
            text = "Запрос к модели ещё не отправлен." if part == "request" else "Ответ ещё не получен." if part == "response" else "Ожидается завершение запроса и проверка плана."
            if record.status != "running":
                text = "Запрос генерации не был отправлен. См. «Проверка»." if part == "request" else "Текст ответа не получен. Причина — в разделе «Проверка»." if part == "response" else text
        lines = []
        for line in text.splitlines():
            lines.extend(textwrap.wrap(line, LINE_WIDTH, expand_tabs=False, replace_whitespace=False, drop_whitespace=False) or [""])
        pages = max(1, (len(lines) + PAGE_LINES - 1) // PAGE_LINES)
        page = min(page, pages - 1)
        return {"id": record.id, "part": part, "page": page, "pages": pages,
                "text": "\n".join(lines[page * PAGE_LINES:(page + 1) * PAGE_LINES]), "status": record.status}

    def export(self, record, offset):
        # Completed exchanges are immutable, so chunks belong to one snapshot.
        text = self.redact(f"Valheim AI Helper — диалог #{record.id}\n{record.time} · {record.provider}/{record.model} · {record.purpose} · {record.status}\n"
            f"Длительность: {record.elapsed():.1f} с\nОбрезано: {record.truncated}\n\n"
            "=== ЗАПРОС К МОДЕЛИ (без HTTP-заголовков) ===\n" + record.request +
            "\n\n=== ИСХОДНЫЙ ОТВЕТ МОДЕЛИ ===\n" + record.response +
            "\n\n=== ПРОВЕРКА И ПРОВЕРЕННЫЙ ПЛАН ===\n" + record.result +
            "\n\nПроверенный план не подтверждает применение или выполнение. Результаты игры — в журнале и следующем наблюдении.\n")
        chunk = text[offset:offset + EXPORT_CHUNK]
        return {"id": record.id, "offset": offset, "next_offset": offset + len(chunk),
                "complete": offset + len(chunk) >= len(text), "text": chunk}


class Exchange:
    def __init__(self, owner, id, provider, model, purpose, credential):
        self.owner, self.id = owner, id
        self.credential = credential
        self.provider, self.model, self.purpose = provider, owner.redact(model, credential), purpose
        self.time = time.strftime("%H:%M:%S")
        self.started = time.monotonic()
        self.seconds = None
        self.status = "running"
        self.request = self.response = self.result = ""
        self.truncated = False

    def elapsed(self):
        return self.seconds if self.seconds is not None else time.monotonic() - self.started

    def set(self, part, value):
        if self.owner.records.get(self.id) is not self: return
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
        text = self.owner.redact(text, self.credential)
        if len(text) > PART_LIMIT:
            text = text[:PART_LIMIT] + "\n[ОБРЕЗАНО: превышен лимит журнала диалога]"
            self.truncated = True
        setattr(self, part, text)
        self.owner.revision += 1

    def finish(self, status, result):
        self.status = status
        self.seconds = time.monotonic() - self.started
        self.set("result", result)
        self.credential = ""

    def summary(self):
        return {"id": self.id, "time": self.time, "provider": self.provider, "model": self.owner.redact(self.model),
                "purpose": self.purpose, "status": self.status, "seconds": round(self.elapsed(), 1),
                "truncated": self.truncated}
