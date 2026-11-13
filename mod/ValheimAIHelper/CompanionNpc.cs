using System;
using System.IO;
using System.Collections;
using System.Linq;
using BepInEx;
using BepInEx.Configuration;
using HarmonyLib;
using UnityEngine;

namespace ValheimAIHelper;

// A native, networked humanoid with its OWN Player data. Never call SetLocalPlayer,
// swap the singleton, use the human's PlayerProfile or open their InventoryGui.
internal sealed class CompanionNpc : MonoBehaviour
{
    internal const string Tag = "aihelper_npc_v1", Profile = "aihelper_profile_v1";
    internal Player Player = null!;
    private bool loaded, failed, saving, dying;
    internal bool Ready => loaded && !failed;
    private float nextSave;
    private Action<float>[]? updates;
    private static readonly string[] Updates = { "UpdateAwake", "UpdateTargeted", "UpdateActionQueue", "PlayerAttackInput", "UpdateCrouch", "UpdateDodge", "UpdateCover", "UpdateGuardianPower", "UpdateStats", "UpdateStealth" };
    internal static bool Is(Player? p) => p && (p!.GetComponent<CompanionNpc>() ||
        (p.GetComponent<ZNetView>() is ZNetView v && v.IsValid() && v.GetZDO().GetBool(Tag, false)));
    internal static CompanionNpc Attach(Player p)
    {
        var npc = p.GetComponent<CompanionNpc>() ?? p.gameObject.AddComponent<CompanionNpc>();
        npc.Player = p;
        var input = p.GetComponent<PlayerController>(); if (input) input.enabled = false;
        return npc;
    }
    internal static void CheckCompatibility()
    {
        foreach (string name in new[] { "UpdateAwake", "UpdateTargeted", "UpdateActionQueue", "PlayerAttackInput", "UpdateCrouch", "UpdateDodge", "UpdateCover", "UpdateGuardianPower", "UpdateStealth", "UpdateStats" })
            if (AccessTools.Method(typeof(Player), name, new[] { typeof(float) }) == null) throw new MissingMethodException("Player", name + "(float)");
    }
    internal void Tick()
    {
        var p = Player; var view = p.GetComponent<ZNetView>();
        if (!view || !view.IsValid()) return;
        // Only the world host may run this brain. Other peers render native ZSync
        // physics. Reclaim ONLY a tagged companion, never a human player object.
        if (!ZNet.instance || !ZNet.instance.IsServer()) return;
        if (!view.IsOwner()) view.ClaimOwnership();
        if (!view.IsOwner() || failed) return;
        try
        {
            using var scope = new NpcScope();
            if (!loaded)
            {
                var bytes = view.GetZDO().GetByteArray(Profile);
                if (bytes != null && bytes.Length > 0) p.Load(new ZPackage(bytes));
                p.SetIntro(false);
                loaded = true;
            }
            if (!NpcAuthorityPolicy.MayControl(true, true, view.IsOwner(), p == Player.m_localPlayer, !p.IsDead())) return;
            float dt = Time.fixedDeltaTime;
            // Physics, animation, weapons and status effects still run through
            // Character/Humanoid.CustomFixedUpdate. Exclude local-camera, auto-
            // pickup, keyboard, respawn, world-discovery and InventoryGui updates.
            updates ??= Updates.Select(name => NativeMethods.BindFloat(p, name)).ToArray();
            foreach (var update in updates) update(dt);
            var station = p.GetCurrentCraftingStation();
            if (station && !station.InUseDistance(p)) p.SetCraftingStation(null);
            if (Time.realtimeSinceStartup >= nextSave) { Save(); nextSave = Time.realtimeSinceStartup + 1; }
        }
        catch (Exception ex) { failed = true; Stop(); Plugin.NpcError(ex); }
    }
    internal void Apply(ControlIntent input)
    {
        var p = Player;
        if (!p || !NpcAuthorityPolicy.MayControl(Is(p), ZNet.instance && ZNet.instance.IsServer(), p!.IsOwner(), p == Player.m_localPlayer, !p.IsDead())) return;
        if (failed) input = default;
        var aim = input.HasAim ? new Vector3(input.AimX, input.AimY, input.AimZ) : new Vector3(input.X, 0, input.Z);
        if (aim.sqrMagnitude > .001f) p.SetLookDir(aim.normalized);
        AccessTools.Field(typeof(Player), "m_autoRun")?.SetValue(p, false);
        var forward = p.GetLookDir(); forward.y = 0; forward.Normalize();
        var right = Vector3.Cross(Vector3.up, forward);
        var move = new Vector3(input.X, 0, input.Z) * input.Magnitude;
        p.SetControls(new Vector3(Vector3.Dot(move, right), 0, Vector3.Dot(move, forward)),
            input.Attack, input.AttackHold, input.Secondary, false, input.Block, input.Block,
            input.Jump, input.Crouch, input.Run, false, input.Dodge);
    }
    internal void Stop()
    {
        if (!Player || Player == global::Player.m_localPlayer || !Player.IsOwner()) return;
        // Releasing a drawn bow normally fires. Cancel draw and queued attacks
        // before clearing controls, including on F9 or a lost control lease.
        AccessTools.Field(typeof(Humanoid), "m_attackDrawTime")?.SetValue(Player, -1f);
        foreach (string field in new[] { "m_queuedAttackTimer", "m_queuedSecondAttackTimer" }) AccessTools.Field(typeof(Player), field)?.SetValue(Player, 0f);
        var weapon = Player.GetCurrentWeapon();
        var animation = Player.GetComponent<ZSyncAnimation>();
        if (animation && weapon != null && !string.IsNullOrEmpty(weapon.m_shared.m_attack.m_drawAnimationState)) animation.SetBool(weapon.m_shared.m_attack.m_drawAnimationState, false);
        var attack = NativeAccess.Read(Player, "m_currentAttack"); if (attack != null) NativeMethods.Call(attack, "Abort");
        Apply(default);
    }
    internal void Save()
    {
        if (!loaded || saving || !Player || !Player.IsOwner()) return;
        var view = Player.GetComponent<ZNetView>(); if (!view || !view.IsValid()) return;
        saving = true;
        try { var package = new ZPackage(); Player.Save(package); view.GetZDO().Set(Profile, package.GetArray()); }
        finally { saving = false; }
    }
    internal void InventoryChanged()
    {
        using var scope = new NpcScope();
        if (NativeAccess.Read(Player, "m_isLoading") is true) return;
        foreach (var item in Player.GetInventory().GetAllItems())
        { item.m_pickedUp = true; NativeMethods.Call(Player, "AddKnownItem", item); }
        NativeMethods.Call(Player, "UpdateKnownRecipesList");
        NativeMethods.Call(Player, "UpdateAvailablePiecesList");
        Save();
    }
    internal void Die()
    {
        if (!Player.IsOwner() || dying) return;
        dying = true;
        using var scope = new NpcScope();
        Stop();
        Player.GetComponent<ZNetView>().GetZDO().Set("dead", true);
        Save();
        // A separate gravestone belongs to the NPC, not the human's profile.
        if (Player.GetInventory().NrOfItems() > 0)
        {
            var prefab = NativeAccess.Read(Player, "m_tombstone") as GameObject;
            if (!prefab) throw new InvalidOperationException("Не найден надгробный контейнер NPC; инвентарь сохранён, автоматическое возрождение запрещено");
            var grave = Instantiate(prefab!, Player.GetCenterPoint(), Player.transform.rotation);
            grave.GetComponent<TombStone>().Setup(Player.GetPlayerName(), Player.GetPlayerID());
            Player.UnequipAllItems();
            grave.GetComponent<Container>().GetInventory().MoveInventoryToGrave(Player.GetInventory());
        }
        Player.GetComponent<ZNetView>().GetZDO().Set("dead", true);
        Save();
        Plugin.NpcDied(Player);
        ZNetScene.instance.Destroy(gameObject);
    }
    private void OnDestroy() { try { Save(); } catch (Exception ex) { Plugin.NpcError(ex); } }
}

public sealed partial class Plugin
{
    private ConfigEntry<string> npcName = null!;
    private bool npcHooksReady;
    private string npcWorld = "";
    private NpcRecord? npcRecord;
    [Serializable] private sealed class NpcRecord { public string uid = ""; public long user; public uint id; public bool dead; }
    private string NpcRecordFile => Path.Combine(Paths.ConfigPath, "ValheimAIHelper", "companions", npcWorld + ".json");
    private void InitNpc()
    {
        npcName = Config.Bind("Companion", "Name", "Гоша", "NPC name used to address the assistant in chat. The human player remains under manual control.");
        try
        {
            CompanionNpc.CheckCompatibility();
            foreach (var patch in new[] { typeof(NpcAwakePatch), typeof(NpcFixedPatch), typeof(NpcUpdatePatch), typeof(NpcInputPatch), typeof(NpcLocalPatch), typeof(NpcDeathPatch), typeof(NpcInventoryPatch), typeof(NpcMessagePatch), typeof(NpcTutorialPatch), typeof(NpcSleepPatch), typeof(NpcWorldSavePatch), typeof(NpcHudPatch), typeof(NpcDamageScopePatch), typeof(NpcCameraPatch), typeof(NpcTeleportPatch), typeof(NpcOwnershipPatch) }) harmony!.PatchAll(patch);
            npcHooksReady = true;
        }
        catch (Exception ex) { AddLog("npc", "NPC несовместим с игрой: " + ex); }
    }
    private Player? CurrentNpc => Player.GetAllPlayers().FirstOrDefault(p => p && CompanionNpc.Is(p) && !p.IsDead());
    private void LoadNpcRecord()
    {
        string world = ZNet.instance.GetWorldUID().ToString(System.Globalization.CultureInfo.InvariantCulture);
        if (npcWorld == world) return;
        npcWorld = world; npcRecord = null;
        try { if (File.Exists(NpcRecordFile)) npcRecord = JsonUtility.FromJson<NpcRecord>(File.ReadAllText(NpcRecordFile)); }
        catch (Exception ex) { AddLog("npc-registry", "Вспомогательная запись недоступна; NPC проверяется по миру: " + ex.Message); }
    }
    private void WriteNpcRecord()
    {
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(NpcRecordFile)!);
            string temporary = NpcRecordFile + ".tmp";
            File.WriteAllText(temporary, JsonUtility.ToJson(npcRecord));
            if (File.Exists(NpcRecordFile)) File.Replace(temporary, NpcRecordFile, NpcRecordFile + ".bak");
            else File.Move(temporary, NpcRecordFile);
        }
        catch (Exception ex) { AddLog("npc-registry", "Не записан вспомогательный индекс; профиль NPC остаётся в мире: " + ex.Message); }
    }
    private Player? EnsureNpc()
    {
        if (!npcHooksReady) { status = "Запуск NPC заблокирован: несовместимые методы игры. См. журнал."; return null; }
        var human = Player.m_localPlayer;
        if (!human || human.IsDead() || human.IsTeleporting() || human.IsSleeping() || !ZNet.instance || !ZNet.instance.IsServer())
        { status = "NPC создаётся в вашем одиночном мире или мире, который вы запустили как хост."; return null; }
        LoadNpcRecord();
        if (string.IsNullOrWhiteSpace(npcName.Value)) npcName.Value = "Гоша";
        var existing = CurrentNpc;
        if (existing)
        {
            var existingView = existing!.GetComponent<ZNetView>();
            if (existingView && existingView.IsValid() && existingView.IsOwner()) existingView.GetZDO().Set("playerName", ObservationValues.Text(npcName.Value.Trim(), 40));
            var component = CompanionNpc.Attach(existing!);
            component.Tick();
            if (!component.Ready) { status = "NPC не готов: ошибка игрового жизненного цикла. Подробности в журнале."; return null; }
            npcRecord = new NpcRecord { uid = existing.GetZDOID().ToString(), user = existing.GetZDOID().UserID, id = existing.GetZDOID().ID };
            WriteNpcRecord();
            return existing;
        }
        // The world, including unloaded sectors, is authoritative. A copied or
        // rolled-back world must not create a duplicate because a local registry
        // is missing/stale. Scan only on explicit create/find, never per frame.
        var worldObjects = NativeAccess.Read(ZDOMan.instance, "m_objectsByID") as IDictionary;
        if (worldObjects == null) { status = "Не удалось проверить сохранённые NPC в этом мире; создание отменено."; return null; }
        var saved = worldObjects.Values.Cast<ZDO>().FirstOrDefault(z => z.GetBool(CompanionNpc.Tag, false) && !z.GetBool("dead", false));
        if (saved != null)
        { status = "NPC уже сохранён в незагруженной области мира. Вернитесь к нему (" + saved.GetPosition().ToString("F0") + "); второй NPC не создаётся."; return null; }
        // Sample actual ground close to the human before creating a networked
        // object. This is the ONLY initial placement; navigation never teleports.
        Vector3 position = human.transform.position + human.transform.right * 2;
        if (!Physics.Raycast(position + Vector3.up * 4, Vector3.down, out var hit, 8,
            LayerMask.GetMask("Default", "static_solid", "terrain", "piece"), QueryTriggerInteraction.Ignore) ||
            Vector3.Angle(hit.normal, Vector3.up) > 40 || Mathf.Abs(hit.point.y - human.transform.position.y) > 2 ||
            Physics.CheckCapsule(hit.point + Vector3.up * .6f, hit.point + Vector3.up * 1.5f, .35f, LayerMask.GetMask("Default", "static_solid", "piece"), QueryTriggerInteraction.Ignore))
        { status = "Рядом нет безопасного места для появления NPC. Встаньте на ровную землю."; return null; }
        var prefab = ZNetScene.instance.GetPrefab("Player");
        if (!prefab) { status = "Игра не предоставила сетевой prefab Player."; return null; }
        var obj = Instantiate(prefab, hit.point + Vector3.up * .15f, human.transform.rotation);
        var p = obj.GetComponent<Player>();
        var view = obj.GetComponent<ZNetView>();
        if (!p || !view || !view.IsValid() || !view.IsOwner())
        { Destroy(obj); status = "Не удалось создать принадлежащего хосту NPC."; return null; }
        view.GetZDO().Set(CompanionNpc.Tag, true);
        view.GetZDO().Persistent = true;
        AccessTools.Field(typeof(ZNetView), "m_persistent")?.SetValue(view, true);
        long id = BitConverter.ToInt64(Guid.NewGuid().ToByteArray(), 0) & long.MaxValue;
        p.SetPlayerID(id == 0 ? 1 : id, ObservationValues.Text(npcName.Value.Trim(), 40));
        p.SetPlayerModel(0); p.SetIntro(false);
        var npc = CompanionNpc.Attach(p); npc.Tick(); npc.Save();
        if (!npc.Ready) { status = "NPC создан, но остановлен из-за несовместимости. Подробности в журнале."; return null; }
        npcRecord = new NpcRecord { uid = p.GetZDOID().ToString(), user = p.GetZDOID().UserID, id = p.GetZDOID().ID }; WriteNpcRecord();
        if (!leaderId.HasValue && string.IsNullOrWhiteSpace(leaderName.Value)) leaderId = human.GetPlayerID();
        AddLog("npc", "Создан NPC " + p.GetPlayerName() + "; ZDO " + npcRecord.uid + ". Локальный игрок: " + human.GetPlayerName());
        return p;
    }
    private void CreateNpcFromPanel()
    {
        try { var p = EnsureNpc(); if (p) uiMessage = "NPC " + p!.GetPlayerName() + " готов. Команды — в игровом чате."; else uiError = status; }
        catch (Exception ex) { NpcError(ex); uiError = "Создание NPC: " + ex.Message; }
    }
    internal static void NpcError(Exception ex)
    {
        if (instance == null) return;
        instance.AddLog("npc", ex.ToString());
        instance.DisableControl("NPC остановлен: " + ex.GetType().Name + ". Подробности в журнале.");
    }
    internal static void NpcDied(Player p)
    {
        var plugin = instance; if (plugin == null) return;
        plugin.LoadNpcRecord(); plugin.npcRecord = new NpcRecord { uid = p.GetZDOID().ToString(), user = p.GetZDOID().UserID, id = p.GetZDOID().ID, dead = true };
        plugin.WriteNpcRecord(); plugin.ChatReply("Я погиб. Предметы остались в моём надгробии.");
        plugin.DisableControl("NPC погиб. Нового можно создать во вкладке «Персонаж»; надгробие сохраняется.");
    }
}

[HarmonyPatch(typeof(Player), "Awake")] internal static class NpcAwakePatch
{ private static void Postfix(Player __instance) { if (CompanionNpc.Is(__instance)) CompanionNpc.Attach(__instance); } }
[HarmonyPatch(typeof(Player), "FixedUpdate")] internal static class NpcFixedPatch
{ private static bool Prefix(Player __instance) { if (!CompanionNpc.Is(__instance)) return true; CompanionNpc.Attach(__instance).Tick(); return false; } }
[HarmonyPatch(typeof(Player), "Update")] internal static class NpcUpdatePatch
{ private static bool Prefix(Player __instance) => !CompanionNpc.Is(__instance); }
[HarmonyPatch(typeof(Player), "TakeInput")] internal static class NpcInputPatch
{ private static bool Prefix(Player __instance, ref bool __result) { if (!CompanionNpc.Is(__instance)) return true; __result = false; return false; } }
[HarmonyPatch(typeof(Player), "SetLocalPlayer")] internal static class NpcLocalPatch
{ private static bool Prefix(Player __instance) => !CompanionNpc.Is(__instance); }
[HarmonyPatch(typeof(Player), "OnDeath")] internal static class NpcDeathPatch
{ private static bool Prefix(Player __instance) { if (!CompanionNpc.Is(__instance)) return true; try { CompanionNpc.Attach(__instance).Die(); } catch (Exception ex) { Plugin.NpcError(ex); } return false; } }
[HarmonyPatch(typeof(Player), "OnInventoryChanged")] internal static class NpcInventoryPatch
{ private static bool Prefix(Player __instance) { if (!CompanionNpc.Is(__instance)) return true; try { CompanionNpc.Attach(__instance).InventoryChanged(); } catch (Exception ex) { Plugin.NpcError(ex); } return false; } }
[HarmonyPatch(typeof(Player), "Message")] internal static class NpcMessagePatch
{ private static bool Prefix(Player __instance) => !CompanionNpc.Is(__instance); }
[HarmonyPatch(typeof(Player), "ShowTutorial")] internal static class NpcTutorialPatch
{ private static bool Prefix(Player __instance) => !CompanionNpc.Is(__instance); }

// A companion does not demand another human bed or prevent the host from
// skipping the night. It rests when its host sleeps, without using bed UI.
[HarmonyPatch(typeof(Player), "IsSleeping")] internal static class NpcSleepPatch
{
    private static bool Prefix(Player __instance, ref bool __result)
    {
        if (!CompanionNpc.Is(__instance)) return true;
        __result = Player.m_localPlayer && Player.m_localPlayer != __instance && Player.m_localPlayer.IsSleeping(); return false;
    }
}
[HarmonyPatch(typeof(ZNet), "SaveWorld", new[] { typeof(bool) })] internal static class NpcWorldSavePatch
{
    private static void Prefix()
    { foreach (var p in Player.GetAllPlayers().Where(p => p && CompanionNpc.Is(p) && p.IsOwner())) try { CompanionNpc.Attach(p).Save(); } catch (Exception ex) { Plugin.NpcError(ex); } }
}
