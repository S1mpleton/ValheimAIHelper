"""Task storage follows the saved world/actor, rather than the game process."""
import hashlib
import json
import os

import httpx
import pytest

from valheim_ai.app import create_app, load_token
from valheim_ai.protocol import Decision, Snapshot
from valheim_ai.task_pool import TaskPool, TaskSpec
from valheim_ai.task_storage import migrate_legacy_task_pools


IDENTITY = "world_1:player:1234"


def filename(identity=IDENTITY):
    return hashlib.sha256(identity.encode()).hexdigest() + ".json"


def legacy(root, pid, title, *, identity=IDENTITY, modified=1):
    path = root / f"runtime-{pid}" / "task-pools" / filename(identity)
    pool = TaskPool()
    pool.apply_batch([TaskSpec(kind="follow", priority=37)], source_request=title)
    pool.save(path, identity)
    os.utime(path, (modified, modified))
    return path


def test_newest_valid_legacy_pool_is_migrated_paused_and_originals_retained(tmp_path):
    old = legacy(tmp_path, 100, "Старое поручение", modified=1)
    latest = legacy(tmp_path, 200, "Последнее поручение", modified=2)
    broken = tmp_path / "runtime-300" / "task-pools" / filename()
    broken.parent.mkdir(parents=True)
    broken.write_text("{invalid", encoding="utf-8")
    os.utime(broken, (3, 3))
    token = load_token(tmp_path / "runtime-100")
    storage = tmp_path / "task-pools"

    report = migrate_legacy_task_pools(storage)
    restored = TaskPool.load(storage / filename(), IDENTITY)

    assert report.migrated == 1 and len(report.rejected) == 1
    assert "повреждённый" in next(message for message in report.messages() if "Пропущены" in message)
    assert restored.paused and len(restored.tasks) == 1
    assert restored.tasks[0].source_request == "Последнее поручение"
    assert restored.tasks[0].spec.priority == 37
    assert restored.tasks[0].manual_paused
    assert old.exists() and latest.exists() and broken.exists()
    assert list(storage.iterdir()) == [storage / filename()]
    assert token not in (storage / filename()).read_text(encoding="utf-8")


def test_mismatching_world_or_actor_and_invalid_nodes_are_explained(tmp_path):
    wrong = legacy(tmp_path, 100, "Неверный мир", identity="other_world:player:1234", modified=3)
    wrong = wrong.rename(wrong.with_name(filename()))
    invalid = legacy(tmp_path, 200, "Повреждённый узел", modified=2)
    data = json.loads(invalid.read_text(encoding="utf-8"))
    data["tasks"][0]["spec"]["priority"] = 999
    invalid.write_text(json.dumps(data), encoding="utf-8")
    os.utime(invalid, (2, 2))
    valid = legacy(tmp_path, 300, "Правильное поручение", modified=1)

    report = migrate_legacy_task_pools(tmp_path / "task-pools")

    assert report.migrated == 1 and len(report.rejected) == 2
    assert any("не совпадает мир/персонаж" in reason for reason in report.rejected)
    assert any("повреждённый" in reason for reason in report.rejected)
    assert wrong.exists() and invalid.exists() and valid.exists()
    restored = TaskPool.load(tmp_path / "task-pools" / filename(), IDENTITY)
    assert restored.tasks[0].source_request == "Правильное поручение"


@pytest.mark.parametrize("empty", [False, True])
def test_existing_stable_save_wins_even_when_legacy_is_newer_or_stable_empty(tmp_path, empty):
    legacy(tmp_path, 100, "Устаревшее поручение", modified=100)
    stable = tmp_path / "task-pools" / filename()
    pool = TaskPool()
    if not empty:
        pool.apply_batch([TaskSpec(kind="follow", title="Сохранённое поручение", priority=12)])
    pool.save(stable, IDENTITY)
    os.utime(stable, (1, 1))
    original = stable.read_bytes()

    report = migrate_legacy_task_pools(stable.parent)

    assert report.migrated == 0 and report.existing == 1
    assert stable.read_bytes() == original


def test_legacy_transient_targets_are_cleared_and_uncertain_transfers_stay_blocked(tmp_path):
    path = tmp_path / "runtime-100" / "task-pools" / filename()
    pool = TaskPool()
    action, transfer = pool.apply_batch([
        TaskSpec(kind="action", action=Decision(action="interact", target_id="world:transient", duration=1, say="")),
        TaskSpec(kind="give", item_prefabs=["Stone"]),
    ])
    transfer.needs_refresh = True
    pool.save(path, IDENTITY)
    # Also handle older files that still contain instance IDs, without trusting
    # their running flag or allowing a transfer with an uncertain outcome.
    data = json.loads(path.read_text(encoding="utf-8"))
    data["tasks"][0]["spec"]["action"]["target_id"] = "world:old-instance"
    data["tasks"][0]["status"] = "running"
    data["tasks"][1]["status"] = "running"
    data["tasks"][1]["needs_refresh"] = False
    path.write_text(json.dumps(data), encoding="utf-8")

    report = migrate_legacy_task_pools(tmp_path / "task-pools")
    destination = tmp_path / "task-pools" / filename()
    restored = TaskPool.load(destination, IDENTITY)

    assert report.migrated == 1
    assert "world:old-instance" not in destination.read_text(encoding="utf-8")
    assert restored.get(action.id).needs_refresh and restored.get(transfer.id).needs_refresh
    assert all(node.manual_paused and node.status == "paused" for node in restored.tasks)


async def test_explicit_storage_survives_game_pid_change_and_remains_world_scoped(tmp_path, snapshot):
    storage = tmp_path / "task-pools"
    observed = Snapshot.model_validate({**snapshot, "world_id": "world_1"})
    async with httpx.AsyncClient() as upstream:
        first = create_app(token="first-token-long-enough", client=upstream,
                           data_dir=tmp_path / "runtime-111", task_pool_dir=storage).state.bridge
        first.tasks.bind(observed)
        node = first.tasks.pool.apply_batch([TaskSpec(kind="follow", priority=44)])[0]
        await first.tasks.close()

        second = create_app(token="second-token-long-enough", client=upstream,
                            data_dir=tmp_path / "runtime-222", task_pool_dir=storage).state.bridge
        second.tasks.bind(observed)
        assert second.tasks.storage == storage and second.tasks.pool.paused
        assert second.tasks.pool.get(node.id).manual_paused
        assert "на паузе" in second.tasks.notice
        assert second.token != first.token
        other_world = observed.model_copy(update={"world_id": "another_world"})
        second.tasks.bind(other_world)
        assert second.tasks.pool.tasks == []
        another_actor = observed.model_copy(update={"actor": observed.actor.model_copy(update={"id": "player:999"})})
        second.tasks.bind(another_actor)
        assert second.tasks.pool.tasks == []
        await second.tasks.close()


async def test_developer_default_keeps_task_pools_below_data_dir(tmp_path):
    async with httpx.AsyncClient() as upstream:
        state = create_app(token="developer-test-token", client=upstream, data_dir=tmp_path).state.bridge
        assert state.tasks.storage == tmp_path / "task-pools"
        await state.tasks.close()


async def test_cli_passes_stable_storage_separately_from_runtime_pairing_dir(tmp_path, monkeypatch):
    from valheim_ai import __main__ as cli

    data = tmp_path / "runtime-123"
    storage = tmp_path / "task-pools"
    monkeypatch.setattr("sys.argv", ["valheim_ai", "--data-dir", str(data), "--task-pool-dir", str(storage)])
    captured = {}
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **options: captured.update(app=app))

    cli.main()

    state = captured["app"].state.bridge
    assert state.tasks.storage == storage
    assert (data / "bridge-token").exists() and not (storage / "bridge-token").exists()
    await state.tasks.close()
    await state.client.aclose()
