"""Reject literal request echoes, without classifying user intent by keywords."""
import re


def words(text):
    return re.findall(r"[^\W_]+", text.casefold().replace("ё", "е"))


def validate_reply(text, objective, chat, actor_name):
    tokens = words(text)
    if not tokens: return
    name = words(actor_name)
    def without_name(value):
        return value[len(name):] if name and value[:len(name)] == name else value
    answer = without_name(tokens)
    requests = [objective, *(m["text"] for m in chat if m.get("pending") and m.get("speaker") != "assistant")]
    # The mod wraps the first chat command when starting AI from stopped/follow
    # mode. This is our own envelope, not a natural-language intent rule.
    if objective.startswith("Поручение игрока ") and ": " in objective:
        requests.append(objective.split(": ", 1)[1])
    if len(answer) >= 3 and any(answer == without_name(words(value)) for value in requests):
        raise ValueError("Model repeated request instead of answering")
    if len(tokens) >= 8 and any(all(tokens[i] == tokens[i % size] for i in range(len(tokens) - 1))
                                 and tokens[(len(tokens) - 1) % size].startswith(tokens[-1])
                                 for size in range(1, min(12, len(tokens) // 2) + 1)):
        raise ValueError("Model returned repetitive speech")
