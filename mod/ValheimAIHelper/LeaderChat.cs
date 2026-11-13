using System;
using System.Collections;
using System.Collections.Generic;
using HarmonyLib;
using UnityEngine;

namespace ValheimAIHelper;

public sealed partial class Plugin
{
    private float lastChatCommand;
    private readonly Queue<LeaderChatRequest> chatQueue = new();
    private bool processingChat;
    private Player? ChatSpeaker(GameObject go, long sender)
    {
        var leader = controlByAllPlayers.Value ? null : FindLeader();
        foreach (var player in Player.GetAllPlayers())
        {
            if (!player || CompanionNpc.Is(player) || player.IsDead() || (go && go.GetComponent<Player>() != player)) continue;
            var view = player.GetComponent<ZNetView>();
            if (view && view.IsValid() && ChatControlPolicy.Allows(controlByAllPlayers.Value, leader ? leader!.GetPlayerID() : (long?)null,
                player.GetPlayerID(), view.GetZDO().GetOwner(), sender)) return player;
        }
        return null;
    }
    private Player? QueuedSpeaker(LeaderChatRequest request)
    {
        var player = Player.GetAllPlayers().Find(p => p && !p.IsDead() && p.GetPlayerID() == request.SenderPlayerId);
        return player && ChatSpeaker(player!.gameObject, request.SenderOwner) == player ? player : null;
    }
    private void QueueChat(Player speaker, string text, bool explicitAddress = false)
    {
        var view = speaker.GetComponent<ZNetView>();
        while (chatQueue.Count >= 6) chatQueue.Dequeue();
        chatQueue.Enqueue(new LeaderChatRequest { actor_name = CurrentNpc ? CurrentNpc!.GetPlayerName() : npcName.Value, speaker = speaker.GetPlayerName(),
            text = text, explicit_address = explicitAddress, SenderPlayerId = speaker.GetPlayerID(), SenderOwner = view.GetZDO().GetOwner() });
        if (!processingChat) StartCoroutine(ProcessOrdinaryChat());
    }
    private void FollowChatSpeaker(Player speaker)
    {
        if (!controlByAllPlayers.Value && leaderId.HasValue && leaderId != speaker.GetPlayerID()) return;
        ChooseLeader(speaker);
        AddLog("leader", "Следование за отправителем команды: " + speaker.GetPlayerName());
    }
    private void ToggleChatControlScope()
    {
        StopFromPanel();
        controlByAllPlayers.Value = !controlByAllPlayers.Value; allowChat.Value = true;
        uiMessage = "Управление персонажем: " + (controlByAllPlayers.Value ? "Все игроки" : "Только лидер") + ". Текущая задача остановлена.";
        AddLog("access", uiMessage);
    }
    internal static void ReceiveLeaderChat(GameObject go, long sender, Talker.Type type, string text)
    {
        var plugin = instance;
        if (plugin == null || !plugin.allowChat.Value || !Player.m_localPlayer || type == Talker.Type.Ping) return;
        if (text.Length == 0 || text.Length > 1000 || text.StartsWith("[AI]", StringComparison.Ordinal)) return;
        var speaker = plugin.ChatSpeaker(go, sender);
        if (!speaker) return;
        bool addressed = LeaderCommand.Addressed(text, plugin.CurrentNpc ? plugin.CurrentNpc!.GetPlayerName() : plugin.npcName.Value, out string command);
        if (!addressed && LeaderCommand.IsDirectFollow(text)) { addressed = true; command = text.Trim(); }
        if (!addressed)
        {
            if (plugin.CanInferChat)
            {
                plugin.QueueChat(speaker!, text);
            }
            return;
        }
        string normalized = command.ToLowerInvariant();
        if (normalized is "стоп" or "stop" or "стой" or "остановись")
        { plugin.StopFromPanel(); plugin.ChatReply("Остановлен."); return; }
        if (Time.realtimeSinceStartup - plugin.lastChatCommand < 1) return;
        plugin.lastChatCommand = Time.realtimeSinceStartup;
        plugin.AddLog("player-chat", speaker!.GetPlayerName() + ": " + command);
        if (normalized is "" or "помощь" or "help")
        { plugin.ChatReply("Обратитесь ко мне по имени: «" + plugin.npcName.Value + ", подбери камни». Можно также продолжать разговор без имени."); return; }
        if (normalized is "статус" or "status")
        { plugin.ChatReply(plugin.active ? plugin.executor.Result : "Остановлен. " + plugin.uiError); return; }
        if (normalized is "бой вкл" or "бой выкл" or "не сражайся" or "не атакуй" or "избегай боя")
        { plugin.allowCombat.Value = normalized == "бой вкл"; plugin.ChatReply(plugin.allowCombat.Value ? "Бой разрешён." : "Бой выключен."); return; }
        if (normalized is "еда вкл" or "еда выкл")
        { plugin.autoSurvival.Value = normalized == "еда вкл"; plugin.ChatReply(plugin.autoSurvival.Value ? "Автоматическая еда включена." : "Автоматическая еда выключена."); return; }
        if (plugin.uiBusy) { plugin.ChatReply("Выполняется настройка AI. Повторите команду через несколько секунд; стоп доступен всегда."); return; }
        string mode = LeaderCommand.IsDirectFollow(normalized) ? "follow" : "ai";
        if (mode == "ai" && plugin.active && plugin.requestedMode == "ai" && plugin.CanInferChat)
        {
            plugin.QueueChat(speaker, text, true);
            return;
        }
        if (mode == "follow") plugin.FollowChatSpeaker(speaker);
        if (mode == "ai") { plugin.FollowChatSpeaker(speaker); plugin.uiObjective = "Поручение игрока " + speaker.GetPlayerName() + ": " + command; }
        plugin.StartCoroutine(plugin.CommandFromChat(mode));
    }
    private IEnumerator ProcessOrdinaryChat()
    {
        processingChat = true;
        try
        {
            while (chatQueue.Count > 0 && allowChat.Value && uiClient != null)
            {
                var request = chatQueue.Dequeue(); int epoch = uiOperation;
                if (!QueuedSpeaker(request)) continue;
                LeaderChatReply? reply = null;
                yield return uiClient.Request<LeaderChatReply>("/api/chat", request, value => reply = value,
                    error => { AddLog("chat", error); if (request.explicit_address && epoch == uiOperation) ChatReply("Не удалось передать обращение: " + error); }, AiProviders.UiTimeout(uiProvider));
                if (!allowChat.Value || epoch != uiOperation || reply == null || !reply.handled || !QueuedSpeaker(request)) continue;
                if (reply.mode == "stop") { StopFromPanel(); if (reply.reply.Length > 0) ChatReply(reply.reply); }
                else if (reply.mode is "task" or "follow")
                {
                    if (uiBusy) continue;
                    if (reply.mode == "follow") FollowChatSpeaker(QueuedSpeaker(request)!);
                    if (reply.objective.Length > 0) uiObjective = reply.objective;
                    yield return CommandFromChat(reply.mode == "follow" ? "follow" : "ai");
                }
                else if (reply.reply.Length > 0) ChatReply(reply.reply);
            }
        }
        finally { processingChat = false; }
    }
    private IEnumerator CommandFromChat(string mode)
    {
        yield return StartMode(mode);
        if (uiError.Length > 0) ChatReply("Не могу начать: " + uiError);
        else if (mode == "follow") ChatReply("Следую за вами.");
    }
    private void ChatReply(string text)
    {
        string reply = journal.Redact(text);
        // Upstream diagnostics stay in the local journal; chat acknowledgements are bounded.
        if (reply.Length > 220) reply = reply.Substring(0, 220);
        var npc = actor ? actor : CurrentNpc;
        var talker = npc ? npc!.GetComponent<Talker>() : null;
        if (talker) talker!.Say(Talker.Type.Normal, "[AI] " + reply);
        else if (Chat.instance) Chat.instance.AddString("[AI: " + npcName.Value + "] " + reply);
    }
    private void SelectNextLeader()
    {
        var local = Player.m_localPlayer;
        var players = Player.GetAllPlayers().FindAll(p => p && !CompanionNpc.Is(p) && !p.IsDead());
        players.Sort((a, b) => string.Compare(a.GetPlayerName(), b.GetPlayerName(), StringComparison.Ordinal));
        if (players.Count == 0) { uiError = "Игроков в мире нет."; return; }
        int index = players.FindIndex(p => leaderId == p.GetPlayerID());
        var selected = players[(index + 1) % players.Count];
        ChooseLeader(selected);
        if (active) StopFromPanel();
        // Stop releases control, but the operator's explicit choice survives it.
        leaderId = selected.GetPlayerID();
        AddLog("leader", "Выбран лидер " + selected.GetPlayerName() + (controlByAllPlayers.Value ? "; управление разрешено всем игрокам" : "; команды разрешены только лидеру"));
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
