using System;
using System.Linq;
using System.Text;

namespace ValheimAIHelper;

public static class TaskPoolText
{
    public const int PageSize = 5;
    public static BridgeTask[] Entries(BridgeTaskPool? pool, bool history) =>
        (history ? pool?.history : pool?.tasks) ?? Array.Empty<BridgeTask>();
    public static bool MatchesScope(BridgeTaskPool? pool, string worldId, string actorId) =>
        pool != null && !string.IsNullOrEmpty(worldId) && !string.IsNullOrEmpty(actorId) &&
        string.Equals(pool.world_id, worldId, StringComparison.Ordinal) && string.Equals(pool.actor_id, actorId, StringComparison.Ordinal);
    public static string Status(string? status) => status switch
    {
        "queued" => "В очереди", "running" => "Выполняется", "paused" => "Пауза",
        "blocked" => "Заблокирована", "completed" => "Выполнена", "failed" => "Ошибка",
        "cancelled" => "Отменена", _ => "Неизвестно"
    };
    public static bool Terminal(BridgeTask? task) => task == null || task.status is "completed" or "failed" or "cancelled";
    public static string Short(string? value, int limit) => limit < 1 || string.IsNullOrEmpty(value) ? "" :
        value!.Length <= limit ? value : value.Substring(0, Math.Max(0, limit - 1)) + "…";
    public static string Summary(BridgeTaskPool? pool, bool history = false) => pool == null ?
        "Пул задач недоступен. Дождитесь связи со службой AI." :
        (history ? "История: " + Entries(pool, true).Length : "Задачи: " + Entries(pool, false).Length + "/" + (pool.limit > 0 ? pool.limit : 15)) +
        " · " + (pool.paused ? "Общая пауза" : "Автономное выполнение") +
        (!string.IsNullOrEmpty(pool.notice) ? " · " + Short(pool.notice, 120) : "");
    public static string Row(BridgeTask task, bool active = false) =>
        (active ? "▶ " : "") + "[" + task.priority + "] " + Short(task.title, 42) + " · " + Status(task.status);
    public static string Dependencies(BridgeTask task, BridgeTaskPool? pool)
    {
        var ids = task.depends_on ?? Array.Empty<string>();
        if (ids.Length == 0) return "Нет";
        var known = Entries(pool, false).Concat(Entries(pool, true)).ToArray();
        return string.Join("; ", ids.Take(6).Select(id =>
        {
            var dep = known.FirstOrDefault(t => t.id == id);
            return dep == null ? id : Short(dep.title, 45) + " (" + Status(dep.status).ToLowerInvariant() + ")";
        })) + (ids.Length > 6 ? "; …" : "");
    }
    public static string Details(BridgeTask? task, BridgeTaskPool? pool)
    {
        if (task == null) return "Выберите задачу слева.\n\nНовые поручения добавляются через игровой чат.";
        var text = new StringBuilder(Status(task.status) + " · приоритет " + task.priority);
        if (!string.IsNullOrEmpty(pool?.notice)) text.Insert(0, pool!.notice + "\n");
        if (!string.IsNullOrEmpty(task.progress)) text.Append("\nХод: ").Append(Short(task.progress, 160));
        if (!string.IsNullOrEmpty(task.target)) text.Append("\nЦель: ").Append(Short(task.target, 150));
        if (!string.IsNullOrEmpty(task.reason)) text.Append("\nПричина: ").Append(Short(task.reason, 420));
        text.Append("\nЗависит от: ").Append(Dependencies(task, pool));
        return text.ToString();
    }
    public static bool ValidControl(string action, string? id, int priority) => action switch
    {
        "pause" or "resume" => true,
        "cancel" => !string.IsNullOrEmpty(id),
        "priority" => !string.IsNullOrEmpty(id) && priority >= 0 && priority <= 90,
        "clear" => string.IsNullOrEmpty(id), _ => false
    };
    public static string Hud(BridgeTaskPool pool)
    {
        var tasks = Entries(pool, false).Where(t => !Terminal(t)).ToArray();
        var active = tasks.FirstOrDefault(t => t.id == pool.active_id) ?? tasks.FirstOrDefault(t => t.status == "running");
        var text = new StringBuilder("ЗАДАЧИ · ").Append(tasks.Length).Append('/').Append(pool.limit > 0 ? pool.limit : 15)
            .Append(pool.paused ? " · Пауза" : "");
        if (!string.IsNullOrEmpty(pool.notice)) text.Append("\n").Append(Short(pool.notice, 160));
        if (active != null)
        {
            text.Append("\nСейчас [").Append(active.priority).Append("] ").Append(Short(active.title, 40))
                .Append("\n").Append(Status(active.status));
            if (!string.IsNullOrEmpty(active.progress)) text.Append(" · ").Append(Short(active.progress, 45));
        }
        else text.Append("\nСейчас: ").Append(pool.paused ? "общая пауза" : "нет активной задачи");
        var next = tasks.Where(t => t != active).OrderByDescending(t => t.priority).Take(4).ToArray();
        if (next.Length > 0) text.Append("\nДалее:");
        foreach (var task in next)
            text.Append("\n[").Append(task.priority).Append("] ").Append(Short(task.title, 40)).Append("\n").Append(Status(task.status));
        if (tasks.Length == 0) text.Append("\nПоручения — в игровом чате.");
        return text.ToString();
    }
    public static string Report(BridgeTaskPool? pool)
    {
        if (pool == null) return "Task pool: unavailable";
        var text = new StringBuilder("Task pool: revision ").Append(pool.revision).Append("; paused: ").Append(pool.paused)
            .Append("; active: ").Append(pool.active_id).Append("; limit: ").Append(pool.limit)
            .Append("; world: ").Append(pool.world_id).Append("; actor: ").Append(pool.actor_id).Append("; notice: ").Append(pool.notice);
        foreach (var history in new[] { false, true })
        {
            text.Append(history ? "\nTask history:" : "\nCurrent tasks:");
            foreach (var task in Entries(pool, history).Take(history ? 30 : 15))
                text.Append("\n").Append(task.id).Append(" · ").Append(task.id == pool.active_id ? "▶ " : "")
                    .Append('[').Append(task.priority).Append("] ").Append(task.title).Append(" · ").Append(Status(task.status))
                    .Append("\n").Append(Details(task, pool));
        }
        return text.ToString();
    }
}
