using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using HarmonyLib;
using UnityEngine;

namespace ValheimAIHelper;

internal static class NpcChat
{
    private static readonly BindingFlags StaticFlags = BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic;

    internal static bool Send(Player npc, string text, Action<string> error)
    {
        if (!npc || !CompanionNpc.Is(npc) || !ZNet.instance || !ZNet.instance.IsServer()) return false;
        var view = npc.GetComponent<ZNetView>();
        if (!view || !view.IsValid() || !view.IsOwner()) return false;
        try
        {
            var permissions = typeof(Chat).GetMethod("CheckPermissionsAndSendChatMessageRPCsAsync", StaticFlags);
            var messageData = typeof(Chat).GetMethod("GetChatMessageData", StaticFlags);
            if (permissions == null || messageData == null) throw new MissingMethodException("Native chat permission/data API unavailable");
            Chat.SendChatMessageRPCHandler send = (recipient, filter) =>
            {
                try
                {
                    if (!npc || !view || !view.IsValid() || !view.IsOwner()) return;
                    object?[] args = { text, filter, null, null };
                    messageData.Invoke(null, args);
                    if (args[2] == null || args[3] is not string filtered) throw new InvalidOperationException("Native chat did not prepare text");
                    var user = NpcChatContext.NamedUser(args[2]!, npc.GetPlayerName());
                    // Same RPC and permission-selected recipients as Talker.Say.
                    // The GameObject is the NPC; no human singleton is swapped.
                    view.InvokeRPC(recipient, "Say", (int)Talker.Type.Normal, user, filtered);
                }
                catch (Exception ex) { error("Не удалось отправить реплику NPC: " + ex.GetType().Name); }
            };
            permissions.Invoke(null, new object[] { send });
            return true;
        }
        catch (Exception ex) { error("Не удалось подготовить реплику NPC: " + ex.GetType().Name); return false; }
    }

    internal static void Local(string name, string text)
    {
        if (!Chat.instance) return;
        NativeMethods.Call(Chat.instance, "AddString", NpcChatContext.CleanName(name), text, Talker.Type.Normal, false);
    }

    internal static string? Speaker(GameObject go)
    {
        var npc = go ? go.GetComponent<Player>() : null;
        if (!npc || !CompanionNpc.Is(npc)) return null;
        // The character is the authoritative speaker. UserInfo belongs to the
        // host account for native permissions and must not choose its label.
        return NpcChatContext.CleanName(npc!.GetPlayerName());
    }
}

[HarmonyPatch(typeof(Chat), "OnNewChatMessage")]
internal static class NpcChatReceivePatch
{
    private static bool Prefix(GameObject __0, ref long __1, ref UserInfo __4, out NpcChatContext.Scope __state)
    {
        __state = NpcChatContext.Enter(null);
        string? name = NpcChat.Speaker(__0);
        if (name == null) return true;
        var npc = __0.GetComponent<Player>();
        var view = npc.GetComponent<ZNetView>();
        // NPC ownership authenticates speech; the displayed key below is never
        // used as a transport account or permission identity.
        if (!view || !view.IsValid() || view.GetZDO().GetOwner() != __1) return false;
        if (__4 == null) return false;
        __4 = (UserInfo)NpcChatContext.NamedUser(__4, name);
        __state.Dispose();
        __state = NpcChatContext.Enter(name);
        return true;
    }
    private static Exception? Finalizer(Exception? __exception, NpcChatContext.Scope __state)
    { __state.Dispose(); return __exception; }
}

[HarmonyPatch(typeof(Chat), "AddInworldText")]
internal static class NpcChatBubblePatch
{
    private static void Prefix(GameObject __0, ref long __1)
    {
        var npc = __0 ? __0.GetComponent<Player>() : null;
        if (!npc || !CompanionNpc.Is(npc)) return;
        var view = npc!.GetComponent<ZNetView>();
        if (!view || !view.IsValid()) return;
        // Only the display bubble uses an NPC-specific key. Keep the authentic
        // sender ID intact until native OnNewChatMessage grants permission.
        string id = NativeAccess.Read(view.GetZDO(), "m_uid")?.ToString() ?? "npc:" + npc.GetPlayerID();
        __1 = NpcChatContext.SpeakerId(id, __1);
    }
}

// Native Chat performs asynchronous permission checks before adding a line.
// Capture the real callback frame so the scoped sender survives that delay.
[HarmonyPatch]
internal static class NpcChatPermissionPatch
{
    private static IEnumerable<MethodBase> TargetMethods() => NpcChatContext.PermissionCallbacks(typeof(Chat));
    private static bool Prepare() => TargetMethods().Any();
    private static void Prefix(object __instance, out NpcChatContext.Scope __state)
    {
        var go = NativeAccess.Read(__instance, "go") as GameObject;
        __state = NpcChatContext.Enter(go ? NpcChat.Speaker(go!) : null);
    }
    private static Exception? Finalizer(Exception? __exception, NpcChatContext.Scope __state)
    { __state.Dispose(); return __exception; }
}

[HarmonyPatch]
internal static class NpcChatLinePatch
{
    private static IEnumerable<MethodBase> TargetMethods() => typeof(Terminal)
        .GetMethods(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance)
        .Where(method => method.Name == "AddString" && method.GetParameters().Length == 4 &&
            method.GetParameters()[0].ParameterType != typeof(string) && method.GetParameters()[1].ParameterType == typeof(string));
    private static bool Prefix(Terminal __instance, string __1, Talker.Type __2, bool __3)
    {
        return !NpcChatContext.TryNamedLine(__instance, Chat.instance, __1, __2, __3);
    }
}
