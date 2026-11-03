using HarmonyLib;
using UnityEngine;

namespace ValheimAIHelper;

// Leave PlayerController enabled. Replace only the local helper's input at the same
// SetControls boundary that the native controller uses. Player, Rigidbody and ZSync
// updates continue to run normally on every client, including cross-play clients.
[HarmonyPatch(typeof(Player), nameof(Player.SetControls))]
internal static class ControlInputPatch
{
    private static void Prefix(Player __instance, ref Vector3 movedir,
        ref bool attack, ref bool attackHold, ref bool secondaryAttack, ref bool secondaryAttackHold,
        ref bool block, ref bool blockHold, ref bool jump, ref bool crouch,
        ref bool run, ref bool autoRun, ref bool dodge, ref bool ___m_autoRun)
    {
        if (!Plugin.TryGetInput(__instance, out var intent)) return;
        if (intent.Magnitude > 0)
            __instance.SetLookDir(new Vector3(intent.X, 0, intent.Z));
        // SetControls converts look-relative forward input into world movement itself.
        movedir = new Vector3(0, 0, intent.Magnitude);
        ___m_autoRun = false;
        attack = intent.Attack;
        attackHold = secondaryAttack = secondaryAttackHold = block = blockHold = false;
        jump = crouch = run = autoRun = dodge = false;
    }
}
