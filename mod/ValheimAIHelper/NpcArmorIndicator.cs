using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using UnityEngine;

namespace ValheimAIHelper;

// Reuse the loaded game's shield, font and colors. The clone belongs to the
// container grid, so it follows the inventory canvas scale and NPC bag size.
// It never replaces InventoryGui.m_armor (the human's statistic).
internal static class NpcArmorIndicator
{
    private static InventoryGui? owner;
    private static GameObject? widget;
    private static Component? label;
    private static RectTransform? gridAnchor, layoutParent;
    private static bool attempted;
    private static string lastError = "";

    internal static string Value(Player npc)
    {
        try
        {
            // Native GetBodyArmor includes equipped-item quality and status
            // effects. Summing inventory entries would give the wrong result.
            var result = NativeMethods.Call(npc, "GetBodyArmor")
                ?? throw new InvalidOperationException("NPC armor statistic is missing");
            float value = Convert.ToSingle(result, CultureInfo.InvariantCulture);
            if (float.IsNaN(value) || float.IsInfinity(value))
                throw new InvalidOperationException("NPC returned an invalid armor statistic");
            return value.ToString(CultureInfo.CurrentCulture);
        }
        catch (Exception ex)
        {
            Log("Значение брони NPC недоступно: " + ex.GetType().Name);
            return "—";
        }
    }

    internal static void Update(InventoryGui gui, Player npc)
    {
        if (!gui || !npc) { Clear(); return; }
        if (owner != gui) Clear();
        owner = gui;
        if (!widget)
        {
            // Missing optional UI members are retried on the next opening,
            // rather than scanning/cloning the hierarchy every rendered frame.
            if (attempted) return;
            attempted = true;
            try { Build(gui); }
            catch (Exception ex)
            {
                if (widget) UnityEngine.Object.Destroy(widget);
                widget = null; label = null;
                Log("Индикатор брони NPC недоступен: " + ex.GetType().Name + ". " + ex.Message);
                return;
            }
        }
        if (label) UiReflection.Write(label!, "text", Value(npc));
        PositionBadge();
    }

    private static void Build(InventoryGui gui)
    {
        var sourceLabel = NativeAccess.Read(gui, "m_armor") as Component;
        var grid = NativeAccess.Read(gui, "m_containerGrid") as Component;
        var gridRoot = NativeAccess.Read(grid, "m_gridRoot") as RectTransform ?? grid?.transform as RectTransform;
        if (!sourceLabel || !gridRoot) throw new NotSupportedException("Штатные элементы брони/сумки не найдены");
        var panel = NativeAccess.Read(gui, "m_container") as RectTransform ?? gridRoot!.parent as RectTransform;
        if (!panel) throw new NotSupportedException("Панель сумки NPC не найдена");

        // The smallest bounded parent with a native image is the armor badge.
        // Refuse to clone a whole information panel or a player inventory.
        Transform? badge = sourceLabel!.transform.parent;
        Transform? template = null;
        for (int depth = 0; badge && depth < 3; depth++, badge = badge!.parent)
        {
            if (badge is not RectTransform bounds || bounds.rect.width > 220 || bounds.rect.height > 220) break;
            var components = badge.GetComponentsInChildren<Component>(true).Where(c => c).ToArray();
            int texts = components.Count(GameUi.IsText);
            bool icon = components.Any(c => c.GetType().FullName == "UnityEngine.UI.Image" && NativeAccess.Read(c, "sprite") != null);
            if (icon && texts <= 2) { template = badge; break; }
        }
        if (!template) throw new NotSupportedException("Не найден отдельный штатный значок щита");

        var route = new Stack<int>();
        for (var child = sourceLabel.transform; child != template; child = child.parent)
        {
            if (!child || !child.parent) throw new NotSupportedException("Значение брони находится вне значка");
            route.Push(child.GetSiblingIndex());
        }
        // The grid can live in a masked scroll view. Put the external badge on
        // the container panel, then position it against the grid in panel space.
        widget = UnityEngine.Object.Instantiate(template!.gameObject, panel!, false);
        gridAnchor = gridRoot; layoutParent = panel;
        widget.name = "AIHelper_NpcArmor";
        var clonedLabel = widget.transform;
        foreach (int index in route) clonedLabel = clonedLabel.GetChild(index);
        label = clonedLabel.GetComponent(sourceLabel.GetType());
        if (!label) throw new NotSupportedException("Копия значения брони не найдена");
        foreach (var component in widget.GetComponentsInChildren<Behaviour>(true))
        {
            string ns = component.GetType().Namespace ?? "";
            if (ns != "UnityEngine.UI" && ns != "TMPro") component.enabled = false;
            UiReflection.Write(component, "raycastTarget", false);
        }
        var rect = widget.GetComponent<RectTransform>();
        rect.anchorMin = rect.anchorMax = panel!.pivot;
        rect.pivot = new Vector2(0, 1);
        rect.sizeDelta = ((RectTransform)template).rect.size;
        rect.localScale = template.localScale;
        rect.localRotation = template.localRotation;
        widget.SetActive(true);
        PositionBadge();
    }

    private static void PositionBadge()
    {
        if (!widget || !gridAnchor || !layoutParent) return;
        var topRight = gridAnchor!.TransformPoint(new Vector3(gridAnchor.rect.xMax, gridAnchor.rect.yMax, 0));
        var local = layoutParent!.InverseTransformPoint(topRight);
        widget!.GetComponent<RectTransform>().anchoredPosition = new Vector2(local.x + 10, local.y - 8);
    }

    internal static void Clear()
    {
        if (widget) UnityEngine.Object.Destroy(widget);
        widget = null; label = null; owner = null; gridAnchor = layoutParent = null; attempted = false;
    }

    private static void Log(string message)
    {
        if (message == lastError) return;
        lastError = message;
        Plugin.NpcInventoryLog(message);
    }
}
