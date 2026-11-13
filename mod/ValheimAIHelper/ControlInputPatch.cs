using HarmonyLib;
using UnityEngine;

namespace ValheimAIHelper;

// Only blocks the HUMAN input while our settings panel has focus. The NPC
// motor applies its own native controls directly, without replacing human input.
[HarmonyPatch(typeof(Player), nameof(Player.SetControls))]
internal static class ControlInputPatch
{
    private static void Prefix(Player __instance, ref Vector3 movedir,
        ref bool attack, ref bool attackHold, ref bool secondaryAttack, ref bool secondaryAttackHold,
        ref bool block, ref bool blockHold, ref bool jump, ref bool crouch,
        ref bool run, ref bool autoRun, ref bool dodge, ref bool ___m_autoRun)
    {
        if (!Plugin.TryGetInput(__instance, out var intent)) return;
        var aim = intent.HasAim ? new Vector3(intent.AimX, intent.AimY, intent.AimZ) : new Vector3(intent.X, 0, intent.Z);
        if (aim.sqrMagnitude > .001f)
        {
            var current = __instance.GetLookDir();
            float yaw = Mathf.Atan2(aim.x, aim.z) * Mathf.Rad2Deg;
            float oldYaw = Mathf.Atan2(current.x, current.z) * Mathf.Rad2Deg;
            float pitch = Mathf.Asin(Mathf.Clamp(aim.normalized.y, -1, 1)) * Mathf.Rad2Deg;
            float oldPitch = Mathf.Asin(Mathf.Clamp(current.normalized.y, -1, 1)) * Mathf.Rad2Deg;
            __instance.SetMouseLook(new Vector2(Mathf.DeltaAngle(oldYaw, yaw), pitch - oldPitch));
            __instance.SetLookDir(aim.normalized);
        }
        // Native SetControls rotates look-relative movement. Keep strafing/retreat
        // independent from aim, instead of rotating the character away from a threat.
        var forward = __instance.GetLookDir(); forward.y = 0; forward.Normalize();
        var right = new Vector3(forward.z, 0, -forward.x);
        var movement = new Vector3(intent.X, 0, intent.Z) * intent.Magnitude;
        movedir = new Vector3(Vector3.Dot(movement, right), 0, Vector3.Dot(movement, forward));
        ___m_autoRun = false;
        attack = intent.Attack;
        attackHold = intent.AttackHold;
        block = blockHold = intent.Block;
        jump = intent.Jump;
        run = intent.Run;
        dodge = intent.Dodge;
        secondaryAttack = intent.Secondary;
        crouch = intent.Crouch;
        secondaryAttackHold = autoRun = false;
    }
}
