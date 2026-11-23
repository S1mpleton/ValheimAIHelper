"""Move process-local task saves into stable storage without reviving old work."""
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re

from .task_pool import MUTATING, TaskPool


@dataclass
class MigrationReport:
    migrated: int = 0
    existing: int = 0
    rejected: list[str] = field(default_factory=list)

    def messages(self):
        if self.migrated:
            yield f"Пулы задач перенесены из прежних папок запуска: {self.migrated}. Восстановление только на паузе."
        if self.rejected:
            yield f"Пропущены неподходящие сохранения задач: {len(self.rejected)}. " + "; ".join(self.rejected[:6])


def migrate_legacy_task_pools(storage: Path) -> MigrationReport:
    """Use the newest valid legacy file per world/actor, preserving stable saves.

    Pairing tokens and provider credentials are never read or copied. Existing
    stable files, including an empty/cancelled pool, always win: importing an
    older pool over them could resurrect tasks the player deliberately removed.
    """
    storage = Path(storage)
    report = MigrationReport()
    candidates: dict[str, list[tuple[int, Path]]] = {}
    try:
        for path in storage.parent.glob("runtime-*/task-pools/*.json"):
            if not re.fullmatch(r"[0-9a-f]{64}\.json", path.name):
                report.rejected.append("Неверное имя файла сохранения")
                continue
            try:
                candidates.setdefault(path.name, []).append((path.stat().st_mtime_ns, path))
            except OSError:
                report.rejected.append(f"{path.parent.parent.name}: файл недоступен")
    except OSError:
        report.rejected.append("Прежние папки запуска недоступны")
        return report

    for name, versions in candidates.items():
        destination = storage / name
        if destination.exists():
            report.existing += 1
            continue
        for _, path in sorted(versions, key=lambda item: (item[0], str(item[1])), reverse=True):
            label = path.parent.parent.name + "/" + name[:12]
            try:
                if path.stat().st_size > 1024 * 1024:
                    raise ValueError("save too large")
                data = json.loads(path.read_text(encoding="utf-8"))
                identity = data.get("world_id") if isinstance(data, dict) else None
                if not isinstance(identity, str) or not identity or len(identity) > 201:
                    report.rejected.append(label + ": отсутствует мир/персонаж")
                    continue
                if hashlib.sha256(identity.encode()).hexdigest() + ".json" != name:
                    report.rejected.append(label + ": не совпадает мир/персонаж")
                    continue
                pool = TaskPool.load(path, identity)
                running = {node["spec"]["id"] for node in data.get("tasks", []) if node.get("status") == "running"}
                for node in pool.tasks:
                    if node.id in running and (node.spec.kind in ("give", "drop") or
                            node.spec.action is not None and node.spec.action.action in MUTATING):
                        node.needs_refresh = True
                # Loading forces a manual pause. Re-saving additionally clears
                # transient entity/item IDs and preserves uncertain-operation
                # flags so an interrupted inventory mutation cannot replay.
                pool.save(destination, identity)
                report.migrated += 1
                break
            except (ValueError, TypeError, KeyError):
                report.rejected.append(label + ": повреждённый или несовместимый пул")
            except OSError:
                report.rejected.append(label + ": не удалось прочитать или перенести пул")
    return report
