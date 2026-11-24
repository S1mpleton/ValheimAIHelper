using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Runtime.CompilerServices;
using HarmonyLib;
using UnityEngine;

namespace ValheimAIHelper;

// A human opens the vanilla chest UI against the NPC's actual inventory. The
// inert adapter has no inventory of its own, RPCs, ZDO item saves or loot hooks.
internal static partial class NpcInventoryWindow
{
    private static NpcInventoryProxy? current;
    private static bool hooksReady;
    private static bool fallbackReady;
    private static Player? requested;
    private static int requestedFrame;
    private static readonly Dictionary<Component, Color> equippedColors = new();
    private static string lastColorError = "";
    private static int useFrame = -1;
    internal static bool UseAttempted => useFrame == Time.frameCount;
    internal static void MarkUseAttempt() => useFrame = Time.frameCount;
    internal static void Notify(string text)
    {
        string failure = "HUD недоступен";
        try
        {
            if (Player.m_localPlayer && NativeNotification.TryShow(Player.m_localPlayer, "Message", MessageHud.MessageType.Center, text, out failure)) return;
            // A local HUD is sufficient even if Player.Message changed or is
            // temporarily unavailable. Resolve its singleton without binding a
            // game-version-specific getter into this notification path.
            var type = typeof(MessageHud);
            var hud = UiReflection.Property(type, "instance")?.GetValue(null, null)
                ?? UiReflection.Field(type, "instance")?.GetValue(null)
                ?? UiReflection.Field(type, "m_instance")?.GetValue(null);
            if (hud != null && NativeNotification.TryShow(hud, "ShowMessage", MessageHud.MessageType.Center, text, out failure)) return;
        }
        catch (Exception ex) { failure = ex.GetType().Name; }
        Plugin.NpcInventoryLog(text + " (сообщение HUD недоступно: " + failure + ")");
    }

    internal static void Initialize(Harmony harmony)
    {
        hooksReady = false;
        fallbackReady = false;
        harmony.PatchAll(typeof(NpcInventoryFallbackFocusPatch));
        fallbackReady = true;
        // The E dispatcher and fallback must survive an incompatible vanilla
        // Container/InventoryGui hook. Register them before adapter checks.
        try { harmony.PatchAll(typeof(NpcInventoryUseTargetPatch)); }
        catch (Exception ex) { Plugin.NpcInventoryLog("Прямой обработчик E недоступен: " + ex); }
        try { harmony.PatchAll(typeof(NpcInventoryUseFallbackPatch)); }
        catch (Exception ex) { Plugin.NpcInventoryLog("Резерв наведения NPC недоступен: " + ex); }
        try { harmony.PatchAll(typeof(NpcInventoryHoverPatch)); }
        catch (Exception ex) { Plugin.NpcInventoryLog("Подсказка сумки NPC недоступна: " + ex); }
        foreach (var name in new[] { "m_inventory", "m_nview" })
            if (UiReflection.Field(typeof(Container), name) == null) throw new MissingFieldException(typeof(Container).FullName, name);
        foreach (var patch in new[] { typeof(NpcContainerAwakePatch), typeof(NpcContainerUsePatch),
            typeof(NpcContainerStackPatch), typeof(NpcContainerInteractPatch), typeof(NpcContainerTakePatch),
            typeof(NpcInventorySelectedPatch), typeof(NpcInventoryTakeAllPatch), typeof(NpcInventoryStackAllPatch), typeof(NpcInventoryDropPatch), typeof(NpcInventoryRightClickPatch),
            typeof(NpcInventoryTitlePatch) }) harmony.PatchAll(patch);
        hooksReady = true;
        try { harmony.PatchAll(typeof(NpcInventoryEquippedPatch)); }
        catch (Exception ex) { Plugin.NpcInventoryLog("Маркер экипировки NPC недоступен: " + ex); }
    }

    internal static void Attach(Player npc)
    {
        if (CompanionNpc.Is(npc) && !npc.GetComponent<NpcInventoryInteraction>())
            npc.gameObject.AddComponent<NpcInventoryInteraction>();
    }

    // The native SetInUse flag can be cleared before its GUI finishes closing
    // or while a compatibility adapter is replaced. Hold the motor gate until
    // our session is actually closed, not until that transient flag changes.
    internal static bool IsOpenFor(Player npc) => npc && current && current!.Npc == npc;

    internal static void RequestOpen(Player npc)
    {
        if (!npc) { Plugin.NpcInventoryLog("Запрос отклонён: NPC больше не существует."); return; }
        if (IsOpenFor(npc) || requested == npc) return;
        requested = npc; requestedFrame = Time.frameCount;
        Plugin.NpcInventoryLog("Открытие сумки NPC поставлено в очередь следующего кадра.");
    }

    internal static bool TryOpen(Player npc, Action beforeOpen, out string error)
    {
        error = "";
        bool prepared = false;
        try
        {
            Plugin.NpcInventoryLog("Проверяю доступ к сумке NPC.");
            if (!CanOpen(npc, out error)) return false;
            Close();
            // Temporarily gate input before exposing the bag to human input.
            // Keep the running session/lease, so closing can continue its work.
            beforeOpen();
            prepared = true;
            NativeMethods.Call(npc, "ClearActionQueue");
            OpenNative(npc);
            return true;
        }
        catch (Exception ex)
        {
            Close();
            Plugin.NpcError(ex);
            if (!prepared)
            {
                error = "Не удалось приостановить управление для обмена вещами. См. журнал.";
                Plugin.NpcInventoryLog("Обмен не начат: подготовка не завершилась. " + ex);
                return false;
            }
            Plugin.NpcInventoryLog("Штатный адаптер недоступен: " + ex + ". Открываю резервный обмен.");
            try
            {
                if (!CanOpen(npc, out error)) return false;
                var go = new GameObject("AIHelper_NpcInventoryFallback");
                go.transform.SetParent(npc.transform, false);
                current = go.AddComponent<NpcInventoryProxy>();
                current.Npc = npc; current.InUse = true;
                RememberCursor(current);
                BeginFallback(current);
                error = "";
                return true;
            }
            catch (Exception fallback)
            {
                Close(); Plugin.NpcInventoryLog("Резервный обмен недоступен: " + fallback);
                error = "Не удалось открыть инвентарь NPC: " + fallback.GetType().Name + ". " + fallback.Message;
                return false;
            }
        }
    }

    // Do not inline native UI calls into TryOpen: if game-version access or
    // signatures differ, the CLR can fail while compiling this method, before
    // its body runs. The caller can still catch that and open the real-bag UI.
    [MethodImpl(MethodImplOptions.NoInlining)]
    private static void OpenNative(Player npc)
    {
        if (!hooksReady || !InventoryGui.instance) throw new NotSupportedException("Штатное окно обмена недоступно в этой версии игры");
        var gui = InventoryGui.instance;
        if (InventoryGui.IsVisible()) NativeMethods.Call(gui, "Hide");
        var go = new GameObject("AIHelper_NpcInventory");
        go.transform.SetParent(npc.transform, false);
        var proxy = go.AddComponent<NpcInventoryProxy>();
        proxy.Npc = npc;
        RememberCursor(proxy);
        current = proxy;
        // Marker and owner must be assigned BEFORE AddComponent invokes Awake.
        proxy.Container = go.AddComponent<Container>();
        if (!ReferenceEquals(proxy.Container.GetInventory(), npc.GetInventory()))
            throw new InvalidOperationException("NPC inventory adapter is not connected to the actual bag");
        proxy.Container.enabled = false;
        proxy.InUse = true;
        proxy.RequestFrame = Time.frameCount;
        Plugin.NpcInventoryLog("Запрошен инвентарь " + npc.GetPlayerName() + "; ожидание следующего кадра.");
    }

    private static bool CanOpen(Player npc, out string error, bool existingSession = false)
    {
        var human = Player.m_localPlayer;
        var view = npc ? npc.GetComponent<ZNetView>() : null;
        bool validNpc = npc && CompanionNpc.Is(npc) && npc != human && !npc.IsDead() && view && view!.IsValid();
        bool validHuman = human && !CompanionNpc.Is(human) && !human.IsDead() && !human.IsTeleporting();
        var result = NpcInventoryAccess.Check(hooksReady || fallbackReady, validNpc,
            validNpc && npc.GetComponent<CompanionNpc>() && npc.GetComponent<CompanionNpc>().Ready,
            ZNet.instance && ZNet.instance.IsServer(), validNpc && view!.IsOwner(), validHuman,
            validHuman && human.IsOwner(), InventoryGui.instance || fallbackReady,
            npc && human ? Vector3.Distance(human.transform.position, npc.transform.position) : double.PositiveInfinity, existingSession);
        error = result switch
        {
            NpcInventoryAccessResult.Allowed => "",
            NpcInventoryAccessResult.Unavailable => "Обмен вещами недоступен: игровые обработчики инвентаря не загружены. См. журнал.",
            NpcInventoryAccessResult.InvalidNpc => "Нет живого NPC для обмена вещами.",
            NpcInventoryAccessResult.LoadingNpc => "NPC ещё загружается; повторите открытие инвентаря.",
            NpcInventoryAccessResult.HostOnly => "Инвентарь NPC доступен на клиенте хозяина мира.",
            NpcInventoryAccessResult.InvalidHuman => "Для обмена нужен живой персонаж игрока.",
            NpcInventoryAccessResult.UiUnavailable => "Игровой инвентарь ещё не готов.",
            _ => "Подойдите к NPC ближе (до 3,5 м), чтобы обменяться вещами."
        };
        return result == NpcInventoryAccessResult.Allowed;
    }

    internal static void Update()
    {
        if (requested && requestedFrame != Time.frameCount)
        {
            var npc = requested; requested = null;
            try { Plugin.ProcessNpcInventoryRequest(npc!); }
            catch (Exception ex) { Plugin.NpcInventoryLog("Ошибка выполнения запроса сумки (до входа в обработчик): " + ex); }
        }
        if (!current) { current = null; return; }
        if (!CanOpen(current!.Npc, out string error, true))
        { Plugin.NpcInventoryLog("Обмен закрыт: " + error); Close(); return; }
        if (Input.GetKeyDown(KeyCode.Escape)) { Close(); return; }
        if (current.Fallback)
        {
            Cursor.lockState = CursorLockMode.None; Cursor.visible = true;
            return;
        }
        if (!current.Shown)
        {
            if (Time.frameCount == current.RequestFrame) return;
            try
            {
                var gui = InventoryGui.instance;
                // Optional UI bookkeeping differs between game versions.
                UiReflection.Field(typeof(InventoryGui), "m_firstContainerUpdate")?.SetValue(gui, true);
                UiReflection.Field(typeof(InventoryGui), "m_containerHoldTime")?.SetValue(gui, 0f);
                UiReflection.Field(typeof(InventoryGui), "m_containerHoldState")?.SetValue(gui, 0);
                ZInput.ResetButtonStatus("Use"); ZInput.ResetButtonStatus("JoyUse");
                NativeMethods.Call(gui, "Show", current.Container, 1);
                if (NativeAccess.Read(gui, "m_currentContainer") as Container != current.Container)
                    throw new InvalidOperationException("InventoryGui.Show did not retain the NPC container");
                current.Shown = true;
                current.ShownAt = Time.realtimeSinceStartup;
                current.BecameVisible = InventoryGui.IsVisible();
                Plugin.NpcInventoryLog("Штатное окно инвентаря NPC открыто.");
            }
            catch (Exception ex)
            {
                Plugin.NpcInventoryLog("Ошибка открытия штатного инвентаря: " + ex);
                Notify("Открываю резервное окно обмена с NPC.");
                BeginFallback(current!);
            }
            return;
        }
        bool visible = InventoryGui.IsVisible();
        if (visible) current.BecameVisible = true;
        if (!visible && !current.BecameVisible)
        {
            if (Time.realtimeSinceStartup - current.ShownAt < .75f) return;
            Plugin.NpcInventoryLog("Штатное окно не стало видимым; открываю резервный обмен.");
            BeginFallback(current); return;
        }
        var shownContainer = NativeAccess.Read(InventoryGui.instance, "m_currentContainer") as Container;
        if (visible && !shownContainer)
        {
            // Some game versions clear the inert adapter immediately after
            // Show. The visible native grid is no longer a valid NPC session:
            // replace it with the real-bag fallback instead of dropping the gate.
            Plugin.NpcInventoryLog("Штатное окно потеряло адаптер NPC; открываю резервный обмен.");
            NativeMethods.Call(InventoryGui.instance, "Hide");
            BeginFallback(current);
            return;
        }
        if (!visible || shownContainer != current.Container)
        {
            Plugin.NpcInventoryLog(!InventoryGui.IsVisible() ? "Окно инвентаря закрыто игроком / игрой." : "Открыт другой инвентарь; обмен с NPC закрыт.");
            Close();
        }
    }

    internal static void Close()
    {
        requested = null;
        var proxy = current;
        current = null;
        NpcArmorIndicator.Clear();
        RestoreEquippedColors();
        if (!proxy) return;
        proxy!.InUse = false;
        try
        {
            var gui = InventoryGui.instance;
            if (gui && proxy.Container && NativeAccess.Read(gui, "m_currentContainer") as Container == proxy.Container)
                NativeMethods.Call(gui, "Hide"); // also clears any drag/split references into the bag
            else if (gui && proxy.Npc)
            {
                // Another mod may Show a different chest without closing ours.
                // Remove references into this bag without closing its new UI.
                var bag = proxy.Npc.GetInventory();
                if (ReferenceEquals(NativeAccess.Read(gui, "m_dragInventory"), bag))
                    NativeMethods.Call(gui, "SetupDragItem", null, null, 1);
                if (ReferenceEquals(NativeAccess.Read(gui, "m_splitInventory"), bag))
                {
                    var panel = NativeAccess.Object(NativeAccess.Read(gui, "m_splitPanel"));
                    if (panel) panel!.SetActive(false);
                    UiReflection.Field(typeof(InventoryGui), "m_splitInventory")?.SetValue(gui, null);
                    UiReflection.Field(typeof(InventoryGui), "m_splitItem")?.SetValue(gui, null);
                }
            }
        }
        catch (Exception ex) { Plugin.NpcInventoryLog("Не удалось очистить штатное окно обмена: " + ex); }
        finally
        {
            if (proxy.Fallback) { Cursor.lockState = proxy.OldCursorLock; Cursor.visible = proxy.OldCursorVisible; }
            try
            {
                if (proxy.Npc && proxy.Npc.GetComponent<CompanionNpc>())
                    proxy.Npc.GetComponent<CompanionNpc>().Save();
            }
            catch (Exception ex) { Plugin.NpcError(ex); }
            finally
            {
                if (proxy.Npc) Plugin.NpcInventoryClosed(proxy.Npc);
                UnityEngine.Object.Destroy(proxy.gameObject);
            }
        }
    }

    internal static void ProxyDestroyed(NpcInventoryProxy proxy)
    {
        // Unloading the world can destroy this child before Plugin.Update
        // revalidates the session. Do not leave a fallback cursor unlocked.
        if (!ReferenceEquals(current, proxy)) return;
        current = null;
        NpcArmorIndicator.Clear();
        RestoreEquippedColors();
        if (proxy.Fallback && proxy.OldCursorCaptured)
        { Cursor.lockState = proxy.OldCursorLock; Cursor.visible = proxy.OldCursorVisible; }
    }

    internal static NpcInventoryProxy? Proxy(Container container) => container ? container.GetComponent<NpcInventoryProxy>() : null;

    // Revalidate on the UI event itself, too: death/ownership changes may happen
    // after Plugin.Update in the same frame. Ordinary chests remain native.
    internal static bool BeforeMutation(InventoryGui gui)
    {
        var container = NativeAccess.Read(gui, "m_currentContainer") as Container;
        var proxy = container ? Proxy(container!) : null;
        if (!proxy)
        {
            if (!current) return true;
            // Show(newChest) can leave the old grid/drag alive until its next
            // refresh. Clear it and discard this one stale event.
            Close(); return false;
        }
        if (proxy == current && proxy!.InUse && CanOpen(proxy.Npc, out _, true)) return true;
        Close();
        if (gui && NativeAccess.Read(gui, "m_currentContainer") as Container == container) NativeMethods.Call(gui, "Hide");
        return false;
    }

    internal static void Prepare(ItemDrop.ItemData? item)
    {
        if (item == null || !IsCurrentInventory() || !current!.Npc.GetInventory().ContainsItem(item)) return;
        var npc = current.Npc;
        using var scope = new NpcScope();
        bool equipped = IsEquipped(npc, item);
        npc.RemoveEquipAction(item);
        npc.UnequipItem(item, false);
        if (equipped) npc.GetComponent<CompanionNpc>().Save();
    }

    internal static void PrepareAll()
    {
        if (!IsCurrentInventory()) return;
        foreach (var item in current!.Npc.GetInventory().GetAllItems().Where(i => !i.m_shared.m_questItem).ToArray()) Prepare(item);
    }

    private static bool IsCurrentInventory() => current && current!.Npc && (current.Fallback || InventoryGui.instance &&
        NativeAccess.Read(InventoryGui.instance, "m_currentContainer") as Container == current.Container);

    internal static void PrepareDrag(InventoryGui gui) => Prepare(NativeAccess.Read(gui, "m_dragItem") as ItemDrop.ItemData);
    internal static bool IsEquipped(Player owner, ItemDrop.ItemData item) => item.m_equipped || owner.IsItemEquiped(item) ||
        ReferenceEquals(NativeAccess.Read(owner, "m_hiddenLeftItem"), item) || ReferenceEquals(NativeAccess.Read(owner, "m_hiddenRightItem"), item);

    internal static void UpdateTitle(InventoryGui gui)
    {
        if (!current || !current!.Npc || NativeAccess.Read(gui, "m_currentContainer") as Container != current.Container) return;
        var label = NativeAccess.Read(gui, "m_containerName");
        if (label != null) UiReflection.Write(label, "text", "Инвентарь: " + current.Npc.GetPlayerName());
        NpcArmorIndicator.Update(gui, current.Npc);
    }

    internal static void UpdateEquippedColors(InventoryGrid grid)
    {
        if (!current || !current!.Npc || current.Fallback || !IsCurrentInventory() ||
            !ReferenceEquals(grid.GetInventory(), current.Npc.GetInventory())) return;
        try
        {
            var elements = NativeAccess.Read(grid, "m_elements") as IEnumerable;
            if (elements == null) return;
            foreach (var element in elements)
            {
                if (NativeAccess.Read(element, "m_pos") is not Vector2i pos) continue;
                var mark = NativeAccess.Read(element, "m_equiped", "m_equipped") as Component;
                if (!mark) continue;
                var item = current.Npc.GetInventory().GetItemAt(pos.x, pos.y);
                bool equipped = item != null && IsEquipped(current.Npc, item);
                if (!equippedColors.ContainsKey(mark!) && UiReflection.Read(mark, "color") is Color original)
                    equippedColors[mark!] = original;
                if (equipped) UiReflection.Write(mark!, "color", new Color(.86f, .87f, .84f, .72f));
                else if (equippedColors.TryGetValue(mark!, out var color)) UiReflection.Write(mark!, "color", color);
                mark!.gameObject.SetActive(equipped);
            }
        }
        catch (Exception ex)
        {
            string error = ex.GetType().Name + ": " + ex.Message;
            if (error != lastColorError) { lastColorError = error; Plugin.NpcInventoryLog("Цвет экипировки NPC: " + error); }
        }
    }

    private static void RestoreEquippedColors()
    {
        foreach (var pair in equippedColors)
            if (pair.Key) try { UiReflection.Write(pair.Key, "color", pair.Value); }
                catch (Exception ex) { Plugin.NpcInventoryLog("Не удалось восстановить цвет ячейки: " + ex.GetType().Name); }
        equippedColors.Clear();
    }
}

internal sealed class NpcInventoryProxy : MonoBehaviour
{
    internal Player Npc = null!;
    internal Container Container = null!;
    internal bool InUse;
    internal int RequestFrame;
    internal bool Shown;
    internal bool BecameVisible;
    internal float ShownAt;
    internal bool Fallback;
    internal CursorLockMode OldCursorLock;
    internal bool OldCursorVisible;
    internal bool OldCursorCaptured;
    private void OnGUI() { if (Fallback && InUse) NpcInventoryWindow.DrawFallback(this); }
    private void OnDestroy() => NpcInventoryWindow.ProxyDestroyed(this);
}

internal sealed class NpcInventoryInteraction : MonoBehaviour, Interactable
{
    public bool Interact(Humanoid user, bool hold, bool alt)
    {
        if (hold || user != Player.m_localPlayer) return false;
        var npc = GetComponent<Player>();
        if (!CompanionNpc.Is(npc)) return false;
        Plugin.NpcInventoryLog("E: обращение к компоненту NPC " + npc.GetPlayerName());
        Plugin.OpenNpcInventory(npc);
        return true;
    }
    public bool UseItem(Humanoid user, ItemDrop.ItemData item) => false;
}

[HarmonyPatch(typeof(Player), "Interact", new[] { typeof(GameObject), typeof(bool), typeof(bool) })]
internal static class NpcInventoryUseTargetPatch
{
    private static bool Prefix(Player __instance, GameObject __0, bool __1)
    {
        var go = __0; bool hold = __1;
        if (__instance != Player.m_localPlayer || !go) return true;
        var npc = go.GetComponentInParent<Player>();
        if (!npc || !CompanionNpc.Is(npc)) return true;
        NpcInventoryWindow.MarkUseAttempt();
        if (NpcInventoryAccess.MayDispatchUse(true, true, hold, __instance.InAttack(), __instance.InDodge()))
        {
            var nativeTarget = go.GetComponentInParent<Interactable>();
            Plugin.NpcInventoryLog("E: выбран NPC " + npc.GetPlayerName() + "; цель " + go.name +
                "; штатный Interactable: " + (nativeTarget?.GetType().Name ?? "нет"));
            Plugin.OpenNpcInventory(npc);
        }
        return false;
    }
}

// Some game/UI versions show a creature's hover text without routing that
// creature through Interactable. After native input, use the SAME native hover
// target as a fallback. Never intercept typing, menus, other players or chests.
[HarmonyPatch(typeof(Player), "Update")]
internal static class NpcInventoryUseFallbackPatch
{
    private static readonly System.Reflection.MethodInfo? radial = typeof(Hud).GetMethod("InRadial",
        System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic,
        null, Type.EmptyTypes, null);
    private static void Prefix(Player __instance, out bool __state)
    {
        __state = false;
        if (__instance != Player.m_localPlayer || !(ZInput.GetButtonDown("Use") || ZInput.GetButtonDown("JoyUse"))) return;
        try { __state = radial != null && radial.Invoke(null, null) is false && NativeMethods.Call(__instance, "TakeInput") is true; }
        catch (Exception ex) { Plugin.NpcInventoryLog("Ошибка проверки ввода NPC: " + ex); }
    }
    private static void Postfix(Player __instance, bool __state)
    {
        if (!__state || NpcInventoryWindow.UseAttempted) return;
        try
        {
            var hovered = NativeAccess.Read(__instance, "m_hovering") as GameObject;
            var npc = hovered ? hovered!.GetComponentInParent<Player>() : NativeAccess.Read(__instance, "m_hoveringCreature") as Player;
            if (npc is null || !npc || !CompanionNpc.Is(npc) || NpcInventoryWindow.IsOpenFor(npc)) return;
            if (!NpcInventoryAccess.MayDispatchUse(true, true, false, __instance.InAttack(), __instance.InDodge())) return;
            NpcInventoryWindow.MarkUseAttempt();
            Plugin.NpcInventoryLog("E: наведение на NPC " + npc.GetPlayerName() + "; прямое открытие сумки.");
            Plugin.OpenNpcInventory(npc);
        }
        catch (Exception ex) { Plugin.NpcInventoryLog("Ошибка обработки наведения NPC: " + ex); }
    }
}

[HarmonyPatch(typeof(Container), "Awake")]
internal static class NpcContainerAwakePatch
{
    private static bool Prefix(Container __instance)
    {
        var proxy = NpcInventoryWindow.Proxy(__instance);
        if (!proxy) return true;
        if (!proxy!.Npc || !CompanionNpc.Is(proxy.Npc)) throw new InvalidOperationException("NPC inventory adapter has no owner");
        UiReflection.Field(typeof(Container), "m_inventory")!.SetValue(__instance, proxy.Npc.GetInventory());
        UiReflection.Field(typeof(Container), "m_nview")!.SetValue(__instance, proxy.Npc.GetComponent<ZNetView>());
        __instance.m_name = proxy.Npc.GetPlayerName();
        return false;
    }
}

[HarmonyPatch(typeof(Container), "SetInUse")]
internal static class NpcContainerUsePatch
{
    private static bool Prefix(Container __instance, bool inUse)
    {
        var proxy = NpcInventoryWindow.Proxy(__instance);
        if (!proxy) return true;
        proxy!.InUse = inUse;
        return false;
    }
}

[HarmonyPatch(typeof(Container), "StackAll")]
internal static class NpcContainerStackPatch
{
    private static bool Prefix(Container __instance)
    {
        var proxy = NpcInventoryWindow.Proxy(__instance);
        if (!proxy) return true;
        if (NpcInventoryWindow.IsOpenFor(proxy!.Npc) && Player.m_localPlayer && NpcInventoryWindow.BeforeMutation(InventoryGui.instance))
            NativeMethods.Call(proxy.Npc.GetInventory(), "StackAll", Player.m_localPlayer.GetInventory(), true);
        return false;
    }
}

[HarmonyPatch(typeof(Container), "Interact")]
internal static class NpcContainerInteractPatch
{
    private static bool Prefix(Container __instance, Humanoid character, bool hold, ref bool __result)
    {
        var proxy = NpcInventoryWindow.Proxy(__instance);
        if (!proxy) return true;
        __result = false;
        if (!hold && character == Player.m_localPlayer) { Plugin.OpenNpcInventory(proxy!.Npc); __result = true; }
        return false;
    }
}

[HarmonyPatch(typeof(Container), "TakeAll")]
internal static class NpcContainerTakePatch
{
    private static bool Prefix(Container __instance, Humanoid character, ref bool __result)
    {
        var proxy = NpcInventoryWindow.Proxy(__instance);
        if (!proxy) return true;
        __result = false;
        if (character == Player.m_localPlayer && NpcInventoryWindow.IsOpenFor(proxy!.Npc) && NpcInventoryWindow.BeforeMutation(InventoryGui.instance))
        {
            var source = proxy.Npc.GetInventory();
            foreach (var item in source.GetAllItems().Where(i => !i.m_shared.m_questItem).ToArray())
            {
                NpcInventoryWindow.Prepare(item);
                Player.m_localPlayer.GetInventory().MoveItemToThis(source, item);
            }
            proxy.Npc.GetComponent<CompanionNpc>().Save();
            __result = true;
        }
        return false;
    }
}

[HarmonyPatch(typeof(InventoryGui), "OnSelectedItem")]
internal static class NpcInventorySelectedPatch
{
    private static bool Prefix(InventoryGui __instance, ItemDrop.ItemData? item)
    {
        if (!NpcInventoryWindow.BeforeMutation(__instance)) return false;
        NpcInventoryWindow.Prepare(item); NpcInventoryWindow.PrepareDrag(__instance); return true;
    }
}

[HarmonyPatch(typeof(InventoryGui), "OnTakeAll")]
internal static class NpcInventoryTakeAllPatch
{
    private static bool Prefix(InventoryGui __instance)
    { if (!NpcInventoryWindow.BeforeMutation(__instance)) return false; NpcInventoryWindow.PrepareAll(); return true; }
}

[HarmonyPatch(typeof(InventoryGui), "OnStackAll")]
internal static class NpcInventoryStackAllPatch
{ private static bool Prefix(InventoryGui __instance) => NpcInventoryWindow.BeforeMutation(__instance); }

[HarmonyPatch(typeof(InventoryGui), "OnDropOutside")]
internal static class NpcInventoryDropPatch
{
    private static bool Prefix(InventoryGui __instance)
    { if (!NpcInventoryWindow.BeforeMutation(__instance)) return false; NpcInventoryWindow.PrepareDrag(__instance); return true; }
}

[HarmonyPatch(typeof(InventoryGui), "OnRightClickItem")]
internal static class NpcInventoryRightClickPatch
{
    private static bool Prefix(InventoryGui __instance, ItemDrop.ItemData? item)
    { if (!NpcInventoryWindow.BeforeMutation(__instance)) return false; NpcInventoryWindow.Prepare(item); return true; }
}

[HarmonyPatch(typeof(Player), "GetHoverText")]
internal static class NpcInventoryHoverPatch
{
    private static void Postfix(Player __instance, ref string __result)
    {
        if (!CompanionNpc.Is(__instance) || __instance.IsDead()) return;
        __result = Localization.instance.Localize(__instance.GetPlayerName() + "\n[<color=yellow><b>$KEY_Use</b></color>] Инвентарь NPC");
    }
}

[HarmonyPatch(typeof(InventoryGui), "UpdateContainer")]
internal static class NpcInventoryTitlePatch
{ private static void Postfix(InventoryGui __instance) => NpcInventoryWindow.UpdateTitle(__instance); }
