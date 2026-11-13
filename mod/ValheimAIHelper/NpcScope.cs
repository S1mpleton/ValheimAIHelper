using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using HarmonyLib;

namespace ValheimAIHelper;

internal sealed class NpcScope : IDisposable
{
    [ThreadStatic] private static int depth;
    internal static bool Active => depth > 0;
    internal NpcScope() { depth++; }
    public void Dispose() { depth--; }
}

// Suppress owner-only HUD/stat side effects while native methods execute for
// the NPC. The real player's vitals, discoveries and profile remain separate.
[HarmonyPatch] internal static class NpcHudPatch
{
    private static IEnumerable<MethodBase> TargetMethods()
    {
        foreach (var type in new[] { typeof(MessageHud), typeof(Hud), typeof(Game) })
            foreach (var m in type.GetMethods(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic))
                if (new[] { "QueueUnlockMsg", "ShowBiomeFoundMsg", "DamageFlash", "StaminaBarEmptyFlash", "StaminaBarUppgradeFlash", "EitrBarUppgradeFlash", "FlashHealthBar", "AdrenalineBarFlash", "IncrementPlayerStat" }.Contains(m.Name)) yield return m;
    }
    private static bool Prefix() => !NpcScope.Active;
}
[HarmonyPatch(typeof(Player), "OnDamaged")] internal static class NpcDamageScopePatch
{
    private static void Prefix(Player __instance, out NpcScope? __state) { __state = CompanionNpc.Is(__instance) ? new NpcScope() : null; }
    private static void Finalizer(NpcScope? __state) { __state?.Dispose(); }
}
[HarmonyPatch(typeof(Player), "DoDamageCameraShake")] internal static class NpcCameraPatch
{ private static bool Prefix(Player __instance) => !CompanionNpc.Is(__instance); }
[HarmonyPatch(typeof(Player), "TeleportTo")] internal static class NpcTeleportPatch
{ private static bool Prefix(Player __instance, ref bool __result) { if (!CompanionNpc.Is(__instance)) return true; __result = false; return false; } }

// Keep companion authority on the world host even when another player enters
// its sector. Native ZSync continues broadcasting normal movement to peers.
[HarmonyPatch] internal static class NpcOwnershipPatch
{
    private static IEnumerable<MethodBase> TargetMethods() => new[] { "SetOwner", "SetOwnerInternal" }
        .Select(name => AccessTools.Method(typeof(ZDO), name, new[] { typeof(long) })).Where(m => m != null).Cast<MethodBase>();
    private static void Prefix(ZDO __instance, ref long __0)
    {
        if (ZNet.instance && ZNet.instance.IsServer() && __instance.GetBool(CompanionNpc.Tag, false)) __0 = ZNet.GetUID();
    }
}
