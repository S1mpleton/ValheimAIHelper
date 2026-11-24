using System;
using System.Globalization;
using System.Linq;
using HarmonyLib;
using UnityEngine;

namespace ValheimAIHelper;

// Compatibility fallback for game versions whose vanilla container UI changed.
// It operates on the two REAL inventories through native inventory methods;
// no copied bags, item spawning, RPC container or human-profile swapping.
internal static partial class NpcInventoryWindow
{
    internal static bool FallbackVisible => current && current!.Fallback && current.InUse;
    private static Inventory? selectedBag;
    private static ItemDrop.ItemData? selectedItem;
    private static string selectedAmount = "1", fallbackMessage = "";
    private static Vector2 fallbackScroll;
    private static Rect fallbackRect;
    private static float fallbackScale = 1;
    private static GUIStyle? fallbackSlot, fallbackLabel;

    private static void RememberCursor(NpcInventoryProxy proxy)
    {
        if (proxy.OldCursorCaptured) return;
        proxy.OldCursorCaptured = true;
        proxy.OldCursorLock = Cursor.lockState;
        proxy.OldCursorVisible = Cursor.visible;
    }

    private static void BeginFallback(NpcInventoryProxy proxy)
    {
        RememberCursor(proxy);
        var gui = InventoryGui.instance;
        if (gui && NativeAccess.Read(gui, "m_currentContainer") as Container == proxy.Container)
            try { NativeMethods.Call(gui, "Hide"); }
            catch (Exception ex) { Plugin.NpcInventoryLog("Штатное окно не закрылось перед резервным обменом: " + ex); }
        RestoreEquippedColors();
        proxy.Fallback = proxy.InUse = proxy.Shown = true;
        selectedBag = null; selectedItem = null; selectedAmount = "1"; fallbackMessage = "";
        fallbackScroll = default;
        fallbackScale = Mathf.Clamp(Menu.instance ? GameUi.InterfaceScale(Menu.instance) : 1, .25f, 4);
        fallbackScale = Mathf.Max(.1f, Mathf.Min(fallbackScale, Screen.width / 560f, Screen.height / 360f));
        float width = Mathf.Min(1040, Screen.width / fallbackScale - 32), height = Mathf.Min(680, Screen.height / fallbackScale - 32);
        fallbackRect = new Rect((Screen.width / fallbackScale - width) / 2, (Screen.height / fallbackScale - height) / 2, width, height);
        Cursor.lockState = CursorLockMode.None; Cursor.visible = true;
        Plugin.NpcInventoryLog("Открыто резервное окно обмена: сумка игрока + настоящая сумка NPC. Экипировка NPC выделена серым.");
    }

    internal static void DrawFallback(NpcInventoryProxy proxy)
    {
        if (current != proxy || !proxy.Npc || !Player.m_localPlayer) return;
        var matrix = GUI.matrix;
        try
        {
            GUI.matrix = Matrix4x4.TRS(Vector3.zero, Quaternion.identity, Vector3.one * fallbackScale);
            fallbackSlot ??= new GUIStyle(GUI.skin.button) { alignment = TextAnchor.LowerRight, fontSize = 12, padding = new RectOffset(3, 3, 3, 3) };
            fallbackLabel ??= new GUIStyle(GUI.skin.label) { wordWrap = true, fontSize = 14 };
            fallbackRect = GUI.Window(172918, fallbackRect, _ =>
            {
                try { DrawFallbackContents(proxy); }
                catch (Exception ex)
                {
                    Plugin.NpcInventoryLog("Ошибка резервного окна обмена: " + ex);
                    Close(); Notify("Окно обмена закрыто из-за ошибки. Подробности в журнале.");
                }
            }, "Обмен вещами — " + proxy.Npc.GetPlayerName());
        }
        finally { GUI.matrix = matrix; }
    }

    private static void DrawFallbackContents(NpcInventoryProxy proxy)
    {
        var human = Player.m_localPlayer;
        if (!human || !proxy.Npc) return;
        var humanBag = human.GetInventory(); var npcBag = proxy.Npc.GetInventory();
        float column = (fallbackRect.width - 60) / 2;
        float cell = Mathf.Clamp((column - 8) / Mathf.Max(1, Math.Max(humanBag.GetWidth(), npcBag.GetWidth())), 24, 56);
        float rows = Math.Max(humanBag.GetHeight(), npcBag.GetHeight());
        GUI.Label(new Rect(18, 26, fallbackRect.width - 36, 36), "Выберите предмет и количество. Shift + нажатие — перенести стопку. Серые ячейки — экипировка NPC.", fallbackLabel);
        fallbackScroll = GUI.BeginScrollView(new Rect(16, 65, fallbackRect.width - 32, fallbackRect.height - 200), fallbackScroll,
            new Rect(0, 0, fallbackRect.width - 52, Math.Max(64, rows * (cell + 4) + 36)));
        try
        {
            DrawFallbackBag(proxy, humanBag, human, "Ваш инвентарь", 0, column, cell);
            DrawFallbackBag(proxy, npcBag, proxy.Npc, "Инвентарь NPC", column + 16, column, cell);
        }
        finally { GUI.EndScrollView(); }
        if (selectedItem != null && (selectedBag == null || !selectedBag.ContainsItem(selectedItem))) { selectedItem = null; selectedBag = null; }
        float bottom = fallbackRect.height - 126;
        GUI.Label(new Rect(18, bottom, fallbackRect.width - 36, 40), selectedItem == null ? "Выберите вещь в любой сумке." :
            Localization.instance.Localize(selectedItem.m_shared.m_name) + " ×" + selectedItem.m_stack + " · качество " + selectedItem.m_quality +
            (selectedItem.m_equipped ? " · надето" : ""), fallbackLabel);
        GUI.Label(new Rect(18, bottom + 42, 100, 28), "Количество:");
        selectedAmount = GUI.TextField(new Rect(118, bottom + 40, 72, 30), selectedAmount, 6);
        bool enabled = GUI.enabled;
        GUI.enabled = selectedItem != null && int.TryParse(selectedAmount, NumberStyles.None, CultureInfo.InvariantCulture, out int amount) && amount > 0;
        if (GUI.Button(new Rect(204, bottom + 40, 190, 32), selectedBag == npcBag ? "Забрать у NPC" : "Передать NPC"))
            MoveFallbackItem(proxy, selectedBag!, selectedItem!, int.Parse(selectedAmount, CultureInfo.InvariantCulture));
        GUI.enabled = enabled;
        if (GUI.Button(new Rect(fallbackRect.width - 156, bottom + 40, 138, 32), "Закрыть")) { Close(); return; }
        GUI.Label(new Rect(18, bottom + 78, fallbackRect.width - 36, 40), fallbackMessage, fallbackLabel);
        GUI.DragWindow(new Rect(0, 0, fallbackRect.width, 25));
    }

    private static void DrawFallbackBag(NpcInventoryProxy proxy, Inventory bag, Player owner, string title, float x, float width, float cell)
    {
        string armor = NpcArmorIndicator.Value(owner);
        GUI.Label(new Rect(x, 0, width, 28), title + " · Броня: " + armor);
        for (int row = 0; row < bag.GetHeight(); row++)
            for (int col = 0; col < bag.GetWidth(); col++)
            {
                var item = bag.GetItemAt(col, row);
                var rect = new Rect(x + col * (cell + 4), 30 + row * (cell + 4), cell, cell);
                bool equipped = item != null && IsEquipped(owner, item);
                var background = GUI.backgroundColor;
                if (equipped) GUI.backgroundColor = owner == proxy.Npc ? new Color(.83f, .85f, .82f) : new Color(.3f, .58f, .9f);
                if (selectedItem == item && item != null) GUI.backgroundColor = new Color(.9f, .75f, .35f);
                string tooltip = item == null ? "" : Localization.instance.Localize(item.m_shared.m_name) + " ×" + item.m_stack +
                    (equipped ? owner == proxy.Npc ? " · надето на NPC" : " · надето" : "");
                bool clicked = GUI.Button(rect, new GUIContent(item != null && item.m_stack > 1 ? item.m_stack.ToString() : "", tooltip), fallbackSlot!);
                GUI.backgroundColor = background;
                if (item != null)
                {
                    try
                    {
                        var icon = item.GetIcon();
                        if (icon && icon.texture)
                        {
                            var area = icon.textureRect;
                            GUI.DrawTextureWithTexCoords(new Rect(rect.x + 5, rect.y + 3, cell - 10, cell - 12), icon.texture,
                                new Rect(area.x / icon.texture.width, area.y / icon.texture.height, area.width / icon.texture.width, area.height / icon.texture.height));
                            if (item.m_stack > 1) GUI.Label(new Rect(rect.x + cell - 30, rect.y + cell - 18, 28, 18), item.m_stack.ToString());
                        }
                    }
                    catch { GUI.Label(new Rect(rect.x + 3, rect.y + 4, cell - 6, cell - 8), Localization.instance.Localize(item.m_shared.m_name)); }
                }
                if (clicked && item != null)
                {
                    selectedBag = bag; selectedItem = item; selectedAmount = item.m_stack.ToString(CultureInfo.InvariantCulture); fallbackMessage = "";
                    if (Event.current.shift) MoveFallbackItem(proxy, bag, item, item.m_stack);
                }
            }
    }

    private static void MoveFallbackItem(NpcInventoryProxy proxy, Inventory source, ItemDrop.ItemData item, int requestedAmount)
    {
        if (current != proxy || !proxy.Fallback || !CanOpen(proxy.Npc, out string error, true))
        { fallbackMessage = "Обмен закрыт: изменились условия доступа."; Close(); return; }
        var human = Player.m_localPlayer;
        var humanBag = human.GetInventory(); var npcBag = proxy.Npc.GetInventory();
        bool fromNpc = ReferenceEquals(source, npcBag), fromHuman = ReferenceEquals(source, humanBag);
        if (!NpcInventoryAccess.MayTransfer(fromHuman, fromNpc, item.m_shared.m_questItem,
            source.ContainsItem(item) ? item.m_stack : 0, requestedAmount))
        { fallbackMessage = "Нельзя перенести: предмет исчез, количество изменилось или вещь квестовая."; return; }
        var target = fromNpc ? humanBag : npcBag;
        int moved = 0;
        try
        {
            var owner = fromNpc ? proxy.Npc : human;
            if (fromNpc) Prepare(item);
            else { owner.RemoveEquipAction(item); owner.UnequipItem(item, false); }
            // Native positioned transfer can split a stack. Prefer compatible
            // existing stacks, then empty cells, measuring each actual delta.
            var slots = Enumerable.Range(0, target.GetHeight()).SelectMany(y => Enumerable.Range(0, target.GetWidth()).Select(x => new Vector2i(x, y))).ToArray();
            foreach (var slot in slots.OrderBy(pos => target.GetItemAt(pos.x, pos.y) == null ? 1 : 0))
            {
                if (moved >= requestedAmount || !source.ContainsItem(item)) break;
                var existing = target.GetItemAt(slot.x, slot.y);
                if (existing != null && (existing.m_shared.m_name != item.m_shared.m_name || existing.m_quality != item.m_quality || existing.m_variant != item.m_variant)) continue;
                int space = existing == null ? item.m_shared.m_maxStackSize : item.m_shared.m_maxStackSize - existing.m_stack;
                int count = Math.Min(requestedAmount - moved, Math.Min(item.m_stack, space));
                if (count <= 0) continue;
                int before = item.m_stack;
                NativeMethods.Call(target, "MoveItemToThis", source, item, count, slot.x, slot.y);
                int actual = before - (source.ContainsItem(item) ? item.m_stack : 0);
                moved += Math.Max(0, actual);
                if (actual != 0 && actual != count) break;
            }
            proxy.Npc.GetComponent<CompanionNpc>().Save();
            fallbackMessage = moved > 0 ? "Перенесено: " + moved + (moved < requestedAmount ? ". Для остатка нет места." : ".") : "Нет свободного места для этой вещи.";
            Plugin.NpcInventoryLog("Резервный обмен: " + (fromNpc ? "NPC → игрок" : "игрок → NPC") + "; перенесено " + moved + "/" + requestedAmount + ".");
            selectedItem = null; selectedBag = null;
        }
        catch (Exception ex)
        {
            fallbackMessage = "Перенос прерван. Проверьте обе сумки; повтор не выполняется автоматически.";
            Plugin.NpcInventoryLog("Резервный обмен прерван после " + moved + ": " + ex);
            selectedItem = null; selectedBag = null;
        }
    }
}

[HarmonyPatch(typeof(Menu), "IsVisible")]
internal static class NpcInventoryFallbackFocusPatch
{
    private static void Postfix(ref bool __result) { if (NpcInventoryWindow.FallbackVisible) __result = true; }
}

[HarmonyPatch(typeof(InventoryGrid), "UpdateGui")]
internal static class NpcInventoryEquippedPatch
{
    private static void Postfix(InventoryGrid __instance) => NpcInventoryWindow.UpdateEquippedColors(__instance);
}
