using System;
using System.Collections;
using System.Linq;
using System.Globalization;
using UnityEngine;

namespace ValheimAIHelper;

public sealed partial class Plugin
{
    private bool taskControlBusy, uiTaskPool;
    private int fallbackTaskPage;
    private bool fallbackTaskHistory;
    private string fallbackTaskSelection = "";
    private string TaskPoolReport() => TaskPoolText.Report(bridgeStatus?.task_pool);
    private BridgeTaskPool? CurrentTaskPool => TryTaskPoolScope(out _, out _) ? bridgeStatus?.task_pool : null;
    private string TaskPoolUnavailableMessage => bridgeStatus?.task_pool != null && CurrentTaskPool == null ?
        "Сначала создайте / подключите NPC в этом мире." : TaskPoolText.Summary(null);

    private bool TryTaskPoolScope(out string world, out string actorId)
    {
        world = actorId = "";
        var npc = CurrentNpc;
        if (!ZNet.instance || !ZNet.instance.IsServer() || !npc || !npc!.IsOwner() ||
            !npc.GetComponent<CompanionNpc>() || !npc.GetComponent<CompanionNpc>().Ready) return false;
        world = ZNet.instance.GetWorldUID().ToString(CultureInfo.InvariantCulture);
        actorId = "player:" + npc.GetPlayerID().ToString(CultureInfo.InvariantCulture);
        return TaskPoolText.MatchesScope(bridgeStatus?.task_pool, world, actorId);
    }

    private void HandleTaskControl(string action, string taskId = "", int priority = 0)
    {
        if (taskControlBusy || uiBusy) return;
        if (!TaskPoolText.ValidControl(action, taskId, priority)) { uiError = "Некорректное управление задачей."; return; }
        if (bridgeStatus?.task_pool == null) { uiError = "Пул задач недоступен. Дождитесь связи со службой AI."; nextUiPoll = 0; return; }
        if (!TryTaskPoolScope(out string world, out string actorId)) { uiError = "Сначала создайте / подключите NPC в этом мире."; nextUiPoll = 0; return; }
        StartCoroutine(ControlTask(new TaskControlRequest { action = action, task_id = taskId, priority = priority,
            revision = bridgeStatus.task_pool.revision, world_id = world, actor_id = actorId }));
    }

    private IEnumerator ControlTask(TaskControlRequest command)
    {
        int operation = ++uiOperation;
        taskControlBusy = uiBusy = true;
        uiError = ""; uiMessage = "Обновляю задачи…";
        bool accepted = false, startAi = false;
        try
        {
            if (uiClient == null) { uiError = "Служба AI не подключена."; yield break; }
            yield return uiClient.Request<BridgeOk>("/api/tasks/control", command, value =>
            {
                if (operation != uiOperation) return;
                if (!TryTaskPoolScope(out string currentWorld, out string currentActor) || currentWorld != command.world_id || currentActor != command.actor_id)
                { uiError = "Мир или NPC изменились. Откройте задачи заново."; return; }
                accepted = value.ok;
                if (!accepted) return;
                // A completed UI command invalidates any older in-flight plan.
                requestedRevision = Math.Max(requestedRevision, value.revision);
                executor.Stop(actor, "Пул задач изменён; ожидаю новое действие.");
                if (actor && actor != Player.m_localPlayer && CompanionNpc.Is(actor)) actor!.GetComponent<CompanionNpc>().Stop();
                startAi = command.action == "resume" && !active;
                uiMessage = "Задачи обновлены.";
                AddLog("task-control", command.action + (command.task_id.Length > 0 ? " · " + command.task_id : " · весь пул"));
            }, message => { if (operation == uiOperation) uiError = message; }, 8);
        }
        finally
        {
            taskControlBusy = false;
            if (operation == uiOperation) { uiBusy = false; nextUiPoll = 0; }
        }
        if (operation != uiOperation) yield break;
        if (accepted && startAi)
        {
            uiObjective = CompanionObjective;
            yield return StartMode("ai");
        }
        else yield return RefreshPanelStatus();
    }

    // The native window is preferred; this keeps task management available if
    // a future game version falls back to the existing IMGUI panel.
    private void DrawTaskPool()
    {
        var pool = CurrentTaskPool;
        GUILayout.Label(pool == null ? TaskPoolUnavailableMessage : TaskPoolText.Summary(pool, fallbackTaskHistory), statusStyle!);
        if (GUILayout.Button((TaskHudEnabled ? "✓ " : "") + "Показывать задачи справа", panelButtonStyle)) ToggleTaskHud();
        if (pool == null) return;
        bool wasEnabled = GUI.enabled;
        GUI.enabled = !uiBusy && !taskControlBusy;
        GUILayout.BeginHorizontal();
        if (GUILayout.Button(pool.paused ? "Продолжить всё" : "Пауза всего пула", panelButtonStyle)) HandleTaskControl(pool.paused ? "resume" : "pause");
        if (GUILayout.Button("Отменить все", panelButtonStyle)) HandleTaskControl("clear");
        GUILayout.EndHorizontal();
        var tasks = TaskPoolText.Entries(pool, fallbackTaskHistory);
        fallbackTaskPage = Math.Min(fallbackTaskPage, Math.Max(0, (tasks.Length - 1) / TaskPoolText.PageSize));
        foreach (var task in tasks.Skip(fallbackTaskPage * TaskPoolText.PageSize).Take(TaskPoolText.PageSize))
            if (GUILayout.Button((task.id == fallbackTaskSelection ? "✓ " : "") + TaskPoolText.Row(task, task.id == pool.active_id), panelButtonStyle)) fallbackTaskSelection = task.id;
        GUILayout.BeginHorizontal();
        if (GUILayout.Button("‹", panelButtonStyle)) fallbackTaskPage = Math.Max(0, fallbackTaskPage - 1);
        GUILayout.Label((fallbackTaskPage + 1) + "/" + Math.Max(1, (tasks.Length + 4) / 5), hintStyle!);
        if (GUILayout.Button("›", panelButtonStyle)) fallbackTaskPage = Math.Min(Math.Max(0, (tasks.Length - 1) / 5), fallbackTaskPage + 1);
        if (GUILayout.Button(fallbackTaskHistory ? "Текущие задачи" : "История", panelButtonStyle))
        { fallbackTaskHistory = !fallbackTaskHistory; fallbackTaskPage = 0; fallbackTaskSelection = ""; }
        GUILayout.EndHorizontal();
        var selected = tasks.FirstOrDefault(t => t.id == fallbackTaskSelection);
        GUILayout.Label(TaskPoolText.Details(selected, pool), hintStyle!);
        GUI.enabled = !uiBusy && !taskControlBusy && !fallbackTaskHistory && !TaskPoolText.Terminal(selected);
        GUILayout.BeginHorizontal();
        if (GUILayout.Button(selected?.status == "paused" ? "Продолжить" : "Пауза", panelButtonStyle)) HandleTaskControl(selected?.status == "paused" ? "resume" : "pause", selected!.id);
        if (GUILayout.Button("Отменить", panelButtonStyle)) HandleTaskControl("cancel", selected!.id);
        if (GUILayout.Button("Приоритет −", panelButtonStyle)) HandleTaskControl("priority", selected!.id, Math.Max(0, selected.priority - 10));
        if (GUILayout.Button("Приоритет +", panelButtonStyle)) HandleTaskControl("priority", selected!.id, Math.Min(90, selected.priority + 10));
        GUILayout.EndHorizontal();
        GUI.enabled = wasEnabled;
    }
}
