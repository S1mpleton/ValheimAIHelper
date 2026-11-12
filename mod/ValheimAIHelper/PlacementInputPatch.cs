using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using HarmonyLib;

namespace ValheimAIHelper;

internal static class NativeInputScope { public static bool Active, Place; }

// Use the full native placement path, which consumes resources, stamina and
// tool durability. TryPlacePiece alone deliberately is not used by the motor.
[HarmonyPatch(typeof(Player), "UpdatePlacement")]
internal static class PlacementInputPatch
{
    private static void Prefix(Player __instance, ref bool __0)
    {
        NativeInputScope.Active = Plugin.PlacementControl(__instance, out bool pulse);
        NativeInputScope.Place = NativeInputScope.Active && pulse;
        if (NativeInputScope.Active) __0 = true;
    }
    private static void Postfix(Player __instance)
    {
        if (NativeInputScope.Active) Plugin.PlacementInputEnded(__instance);
        NativeInputScope.Active = NativeInputScope.Place = false;
    }
    private static void Finalizer() { NativeInputScope.Active = NativeInputScope.Place = false; }
}
[HarmonyPatch]
internal static class PlacementButtonsPatch
{
    private static IEnumerable<MethodBase> TargetMethods() => typeof(ZInput).GetMethods(BindingFlags.Public | BindingFlags.Static)
        .Where(m => new[] { "GetButton", "GetButtonDown", "GetButtonUp" }.Contains(m.Name) && m.ReturnType == typeof(bool) && m.GetParameters().FirstOrDefault()?.ParameterType == typeof(string));
    private static bool Prefix(MethodBase __originalMethod, string __0, ref bool __result)
    {
        if (!NativeInputScope.Active) return true;
        __result = __originalMethod.Name == "GetButtonDown" && __0 == "Attack" && NativeInputScope.Place;
        return false;
    }
}
[HarmonyPatch(typeof(Player), "TryPlacePiece")]
internal static class PlacementResultPatch
{
    private static void Postfix(Player __instance, bool __result)
    { if (NativeInputScope.Active && NativeInputScope.Place) Plugin.PlacementCompleted(__instance, __result); }
}
[HarmonyPatch(typeof(Player), "Repair")]
internal static class StructureRepairResultPatch
{
    private static void Prefix(Player __instance, out float __state)
    {
        __state = -1;
        if (!NativeInputScope.Active || !NativeInputScope.Place) return;
        var piece = NativeAccess.Call(__instance, "GetHoveringPiece") as Piece;
        var wear = piece ? piece!.GetComponent<WearNTear>() : null;
        if (wear) __state = (float)(NativeAccess.Call(wear!, "GetHealthPercentage") ?? -1f);
    }
    private static void Postfix(Player __instance, float __state)
    {
        if (__state < 0 || !NativeInputScope.Active || !NativeInputScope.Place) return;
        Plugin.StructureRepairAttempted(__instance, __state);
    }
}
