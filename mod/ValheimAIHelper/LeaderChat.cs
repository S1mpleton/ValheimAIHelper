using System;
using System.Collections;
using HarmonyLib;
using UnityEngine;

namespace ValheimAIHelper;

public sealed partial class Plugin
{
    private float lastChatCommand;
    internal static void ReceiveLeaderChat(GameObject go, long sender, Talker.Type type, string text)
    {
        var plugin = instance;
        if (plugin == null || !plugin.allowChat.Value || !Player.m_localPlayer || type == Talker.Type.Ping) return;
        string? command = LeaderCommand.Parse(text);
        if (command == null) return;
        var leader = plugin.FindLeader();
        if (leader == null || !leader || leader == Player.m_localPlayer) return;
        var view = leader.GetComponent<ZNetView>();
        // Match native network identity, never a display name supplied in chat.
        if (!view || !view.IsValid() || view.GetZDO().GetOwner() != sender ||
            (go && go.GetComponent<Player>() != leader)) return;
        string normalized = command.ToLowerInvariant();
        if (normalized is "стоп" or "stop" or "стой")
        { plugin.StopFromPanel(); plugin.ChatReply("Остановлен. Управление возвращено игроку."); return; }
        if (Time.realtimeSinceStartup - plugin.lastChatCommand < 1) return;
        plugin.lastChatCommand = Time.realtimeSinceStartup;
        plugin.AddLog("leader", command);
        if (normalized is "" or "помощь" or "help")
        { plugin.ChatReply("!ai следуй · !ai стоп · !ai статус · !ai бой вкл/выкл · !ai <задача>"); return; }
        if (normalized is "статус" or "status")
        { plugin.ChatReply(plugin.active ? plugin.executor.Result : "Остановлен. " + plugin.uiError); return; }
        if (normalized is "бой вкл" or "бой выкл")
        { plugin.allowCombat.Value = normalized == "бой вкл"; plugin.ChatReply(plugin.allowCombat.Value ? "Бой разрешён." : "Бой выключен."); return; }
        if (normalized is "еда вкл" or "еда выкл")
        { plugin.autoSurvival.Value = normalized == "еда вкл"; plugin.ChatReply(plugin.autoSurvival.Value ? "Автоматическая еда включена." : "Автоматическая еда выключена."); return; }
        if (plugin.uiBusy) { plugin.ChatReply("Выполняется настройка AI. Повторите команду через несколько секунд; стоп доступен всегда."); return; }
        string mode = normalized is "следуй" or "за мной" or "follow" ? "follow" : "ai";
        if (mode == "ai") plugin.uiObjective = command;
        plugin.StartCoroutine(plugin.CommandFromChat(mode));
    }
    private IEnumerator CommandFromChat(string mode)
    {
        yield return StartMode(mode);
        ChatReply(uiError.Length > 0 ? uiError : mode == "follow" ? "Следую за вами." : "Задача принята. Выполняю.");
    }
    private void ChatReply(string text)
    {
        string reply = journal.Redact(text);
        // Upstream diagnostics stay in the local journal; chat acknowledgements are bounded.
        if (reply.Length > 220) reply = reply.Substring(0, 220);
        if (Chat.instance) Chat.instance.SendText(Talker.Type.Normal, "[AI] " + reply);
    }
    private void SelectNextLeader()
    {
        var local = Player.m_localPlayer;
        var players = Player.GetAllPlayers().FindAll(p => p && p != local && !p.IsDead());
        players.Sort((a, b) => string.Compare(a.GetPlayerName(), b.GetPlayerName(), StringComparison.Ordinal));
        if (players.Count == 0) { uiError = "Других игроков рядом нет. Войдите с лидером в один мир."; return; }
        int index = players.FindIndex(p => leaderId == p.GetPlayerID());
        var selected = players[(index + 1) % players.Count];
        leaderName.Value = selected.GetPlayerName(); leaderId = selected.GetPlayerID();
        if (active) StopFromPanel();
        // Stop releases control, but the operator's explicit choice survives it.
        leaderId = selected.GetPlayerID();
        AddLog("leader", "Команды разрешены от персонажа " + selected.GetPlayerName());
    }
}

// This hook runs after Valheim's communication permission check and text filtering.
[HarmonyPatch(typeof(Chat), "AddInworldText")]
internal static class LeaderChatPatch
{
    private static void Postfix(GameObject __0, long __1, Talker.Type __3, string __5)
    {
        try { Plugin.ReceiveLeaderChat(__0, __1, __3, __5); }
        catch (Exception) { /* A UI/network teardown must not break native chat. */ }
    }
}
