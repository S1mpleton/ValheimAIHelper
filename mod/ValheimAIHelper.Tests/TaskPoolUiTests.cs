using System;
using ValheimAIHelper;
using Xunit;

public class TaskPoolUiTests
{
    [Fact] public void StatusReadsTaskPoolWithBlockedTaskAndPrerequisite()
    {
        const string json = "{\"mode\":\"ai\",\"task_pool\":{\"limit\":15,\"revision\":42,\"paused\":false,\"active_id\":\"t1\",\"tasks\":[{\"id\":\"t1\",\"title\":\"Добыть руду\",\"kind\":\"gather\",\"priority\":70,\"status\":\"blocked\",\"reason\":\"Нужна кирка\",\"progress\":\"0/5\",\"target\":\"Медь\",\"depends_on\":[\"t2\"]},{\"id\":\"t2\",\"title\":\"Взять кирку\",\"status\":\"queued\",\"priority\":80}],\"history\":[]}}";
        var pool = WireJson.Deserialize<BridgeUiStatus>(json).task_pool!;
        Assert.Equal(42, pool.revision);
        Assert.Equal(15, pool.limit);
        Assert.Equal("t1", pool.active_id);
        Assert.Equal("Нужна кирка", pool.tasks[0].reason);
        string detail = TaskPoolText.Details(pool.tasks[0], pool);
        Assert.Contains("0/5", detail);
        Assert.Contains("Взять кирку (в очереди)", detail);
        Assert.Contains("Медь", detail);
        Assert.Contains("Заблокирована", detail);
    }
    [Fact] public void OldStatusAndMissingOptionalTaskFieldsRemainReadable()
    {
        var status = WireJson.Deserialize<BridgeUiStatus>("{\"mode\":\"stop\"}");
        Assert.Null(status.task_pool);
        Assert.Empty(TaskPoolText.Entries(status.task_pool, false));
        Assert.Contains("недоступен", TaskPoolText.Summary(status.task_pool));
        var pool = WireJson.Deserialize<BridgeTaskPool>("{\"tasks\":[{\"id\":\"x\",\"status\":\"paused\"}]}");
        Assert.Contains("Пауза", TaskPoolText.Details(pool.tasks[0], pool));
        Assert.Contains("Нет", TaskPoolText.Dependencies(pool.tasks[0], pool));
        Assert.Empty(TaskPoolText.Entries(pool, true));
    }
    [Fact] public void ControlRoundTripCarriesOptimisticPoolRevision()
    {
        var original = new TaskControlRequest { action = "priority", task_id = "t9", priority = 90, revision = 51, world_id = "w1", actor_id = "player:17" };
        var copy = WireJson.Deserialize<TaskControlRequest>(WireJson.Serialize(original));
        Assert.Equal(51, copy.revision); Assert.Equal(90, copy.priority);
        Assert.Equal("t9", copy.task_id); Assert.Equal("priority", copy.action);
        Assert.Equal("w1", copy.world_id); Assert.Equal("player:17", copy.actor_id);
    }
    [Fact] public void SnapshotCapabilityDefaultsOffAndWorldScopeRoundTrips()
    {
        var older = new Snapshot();
        Assert.False(older.task_pool_supported);
        Assert.Equal("", older.world_id);
        var snapshot = WireJson.Deserialize<Snapshot>(WireJson.Serialize(new Snapshot { task_pool_supported = true, world_id = "23456789", reflex_active = true }));
        Assert.True(snapshot.task_pool_supported); Assert.Equal("23456789", snapshot.world_id);
        Assert.True(snapshot.reflex_active);
    }
    [Fact] public void SoftStopExplicitlyKeepsPausedTasksOnWire()
    {
        var command = WireJson.Deserialize<BridgeCommand>(WireJson.Serialize(new BridgeCommand { mode = "stop", pause_tasks = true }));
        Assert.True(command.pause_tasks); Assert.Equal("stop", command.mode);
    }
    [Theory]
    [InlineData("priority", "t1", -1, false)]
    [InlineData("priority", "t1", 91, false)]
    [InlineData("priority", "", 50, false)]
    [InlineData("priority", "t1", 90, true)]
    [InlineData("cancel", "", 0, false)]
    [InlineData("cancel", "t1", 0, true)]
    [InlineData("clear", "t1", 0, false)]
    [InlineData("clear", "", 0, true)]
    [InlineData("pause", "", 0, true)]
    [InlineData("resume", "t1", 0, true)]
    [InlineData("delete", "t1", 0, false)]
    public void ControlsRejectInvalidScopeAndPriority(string action, string id, int priority, bool valid)
        => Assert.Equal(valid, TaskPoolText.ValidControl(action, id, priority));
    [Theory]
    [InlineData("completed", true)] [InlineData("failed", true)] [InlineData("cancelled", true)]
    [InlineData("paused", false)] [InlineData("blocked", false)] [InlineData("running", false)]
    public void HistoryStatesCannotBeControlled(string status, bool terminal)
        => Assert.Equal(terminal, TaskPoolText.Terminal(new BridgeTask { status = status }));
    [Fact] public void DependenciesIncludeCompletedHistoryAndUnknownIds()
    {
        var pool = new BridgeTaskPool { history = new[] { new BridgeTask { id = "tool", title = "Получить кирку", status = "completed" } } };
        Assert.Equal("Получить кирку (выполнена); unknown", TaskPoolText.Dependencies(new BridgeTask { depends_on = new[] { "tool", "unknown" } }, pool));
    }
    [Fact] public void ReportIncludesActiveIdAndReasonsAndCapsOversizedHistory()
    {
        var tasks = new BridgeTask[40];
        for (int i = 0; i < tasks.Length; i++) tasks[i] = new BridgeTask { id = "h" + i, title = "История " + i, status = "failed", reason = "Нет маршрута" };
        string report = TaskPoolText.Report(new BridgeTaskPool { active_id = "t1", revision = 7, history = tasks });
        Assert.Contains("active: t1", report); Assert.Contains("Нет маршрута", report);
        Assert.Contains("h29 ·", report); Assert.DoesNotContain("h30 ·", report);
    }
    [Theory]
    [InlineData("world-a", "player:1", true)]
    [InlineData("world-b", "player:1", false)]
    [InlineData("world-a", "player:2", false)]
    [InlineData("", "player:1", false)]
    [InlineData("world-a", "", false)]
    public void TaskControlsAreScopedToExactWorldAndNpc(string world, string actor, bool matches)
        => Assert.Equal(matches, TaskPoolText.MatchesScope(new BridgeTaskPool { world_id = "world-a", actor_id = "player:1" }, world, actor));
    [Fact] public void HudListsCurrentTaskAndNextFourByPriorityWithoutTerminalEntries()
    {
        var pool = new BridgeTaskPool { active_id = "active", tasks = new[]
        {
            new BridgeTask { id = "active", title = "Текущая", priority = 10, status = "running", progress = "2/5" },
            new BridgeTask { id = "low", title = "Низкий", priority = 5, status = "queued" },
            new BridgeTask { id = "high", title = "Высокий", priority = 90, status = "queued" },
            new BridgeTask { id = "mid", title = "Средний", priority = 50, status = "blocked" },
            new BridgeTask { id = "other", title = "Другой", priority = 40, status = "paused" },
            new BridgeTask { id = "last", title = "За пределом", priority = 0, status = "queued" },
            new BridgeTask { id = "done", title = "Выполненная", priority = 90, status = "completed" }
        } };
        string hud = TaskPoolText.Hud(pool);
        Assert.Contains("Сейчас [10] Текущая", hud); Assert.Contains("2/5", hud);
        Assert.True(hud.IndexOf("Высокий", StringComparison.Ordinal) < hud.IndexOf("Средний", StringComparison.Ordinal));
        Assert.Contains("Низкий", hud); Assert.DoesNotContain("За пределом", hud); Assert.DoesNotContain("Выполненная", hud);
    }
    [Fact] public void EmptyPausedHudShowsPauseAndChatHint()
    {
        string hud = TaskPoolText.Hud(new BridgeTaskPool { paused = true });
        Assert.Contains("0/15", hud); Assert.Contains("общая пауза", hud); Assert.Contains("Поручения", hud);
    }
    [Fact] public void ReflexPriorityNoticeIsVisibleInHudAndReport()
    {
        var pool = new BridgeTaskPool { notice = "Приоритет 100: защита / выживание" };
        Assert.Contains(pool.notice, TaskPoolText.Hud(pool));
        Assert.Contains(pool.notice, TaskPoolText.Report(pool));
    }
}
