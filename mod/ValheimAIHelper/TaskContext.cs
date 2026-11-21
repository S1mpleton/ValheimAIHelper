using System;
using System.Collections;
using System.Globalization;
using UnityEngine;

namespace ValheimAIHelper;

public sealed partial class Plugin
{
    // This read-only context has its own identity. It must never advance or
    // replace the active control loop's session/sequence or issue a command.
    private readonly string taskContextSession = "context-" + Guid.NewGuid().ToString("N");
    private int taskContextSequence;
    private bool taskContextPending;
    private float nextTaskContext, lastTaskContextErrorAt;
    private string lastTaskContextError = "", lastPublishedTaskContext = "";

    private void UpdateTaskContext()
    {
        if (!showPanel || active || uiBusy || taskContextPending || uiClient == null ||
            bridgeToken.Value.Length < 32 || Time.realtimeSinceStartup < nextTaskContext) return;
        nextTaskContext = Time.realtimeSinceStartup + 2;
        if (!TryTaskContextScope(out var npc, out string world, out string actorId)) return;
        try
        {
            var observation = Observation.Capture(npc!, FindLeader(), taskContextSession, ++taskContextSequence, "Контекст пула задач; управление отключено");
            observation.Data.task_pool_supported = true;
            observation.Data.world_id = world;
            observation.Data.listen_chat = allowChat.Value;
            observation.Data.simulation_paused = Time.timeScale < .01f;
            observation.Data.background_planning = false;
            taskContextPending = true;
            StartCoroutine(SendTaskContext(observation.Data, uiClient, uiOperation, world, actorId));
        }
        catch (Exception ex)
        {
            taskContextPending = false;
            TaskContextError("Не удалось прочитать контекст NPC: " + ex);
        }
    }

    private bool TryTaskContextScope(out Player? npc, out string world, out string actorId)
    {
        npc = null; world = actorId = "";
        var human = Player.m_localPlayer;
        if (!human || human.IsDead() || !ZNet.instance || !ZNet.instance.IsServer()) return false;
        npc = CurrentNpc;
        if (npc is null || !npc || npc == human || npc.IsDead() || !npc.IsOwner()) return false;
        var companion = npc.GetComponent<CompanionNpc>();
        if (!companion || !companion.Ready) return false;
        world = ZNet.instance.GetWorldUID().ToString(CultureInfo.InvariantCulture);
        actorId = "player:" + npc.GetPlayerID().ToString(CultureInfo.InvariantCulture);
        return true;
    }

    private bool TaskContextStillCurrent(BridgeUiClient client, int operation, string world, string actorId)
    {
        return showPanel && !active && operation == uiOperation && ReferenceEquals(client, uiClient) &&
            TryTaskContextScope(out _, out string currentWorld, out string currentActor) &&
            currentWorld == world && currentActor == actorId;
    }

    private IEnumerator SendTaskContext(Snapshot snapshot, BridgeUiClient client, int operation, string world, string actorId)
    {
        try
        {
            yield return client.Request<BridgeOk>("/api/tasks/context", snapshot, result =>
            {
                if (!TaskContextStillCurrent(client, operation, world, actorId)) return;
                if (!result.ok) { TaskContextError("Служба не приняла контекст NPC для пула задач."); return; }
                lastTaskContextError = "";
                nextUiPoll = 0;
                string scope = world + ":" + actorId;
                if (lastPublishedTaskContext != scope)
                {
                    lastPublishedTaskContext = scope;
                    AddLog("task-context", "Контекст NPC передан для просмотра сохранённых задач; управление не запускалось.");
                }
            }, message =>
            {
                if (TaskContextStillCurrent(client, operation, world, actorId)) TaskContextError(message);
            }, 8);
        }
        finally
        {
            taskContextPending = false;
            nextTaskContext = Time.realtimeSinceStartup + 2;
        }
    }

    private void TaskContextError(string message)
    {
        float now = Time.realtimeSinceStartup;
        if (message != lastTaskContextError || now - lastTaskContextErrorAt >= 60)
        {
            AddLog("task-context", message);
            lastTaskContextError = message;
            lastTaskContextErrorAt = now;
        }
    }
}
