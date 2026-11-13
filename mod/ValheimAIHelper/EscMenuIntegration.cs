using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using HarmonyLib;
using UnityEngine;
using UnityEngine.Events;

namespace ValheimAIHelper;

internal sealed class EscMenuIntegration : IDisposable
{
    private GameObject? buttonObject;
    private Component? button, sourceButton;
    private Menu? owner;
    private readonly Action open;
    private readonly Dictionary<RectTransform, Vector2> manualPositions = new();

    public EscMenuIntegration(Action open) { this.open = open; }
    public bool Installed => buttonObject;
    public bool Matches(Menu menu) => owner == menu && buttonObject && button;
    public void RefreshNavigation() { if (sourceButton && button) LinkNavigation(sourceButton!, button!); }

    public void Install(Menu menu)
    {
        if (Matches(menu)) return;
        // Reflect UI-only fields so changes to TMPro/UGUI assembly versions don't
        // introduce another runtime dependency into the mod.
        var source = GameUi.MenuButton(menu);
        if (source == null || !source) return;
        var entry = GameUi.EntryRoot(menu, source);
        if (owner != menu || !buttonObject)
        {
            Dispose();
            owner = menu; sourceButton = source;
            buttonObject = UnityEngine.Object.Instantiate(entry.gameObject, entry.parent, false);
            buttonObject.name = "ValheimAIHelperButton";
            buttonObject.transform.SetSiblingIndex(entry.GetSiblingIndex() + 1);
            buttonObject.SetActive(true);
            button = buttonObject.GetComponentInChildren(GameUi.FindType("UnityEngine.UI.Button"), true);
            if (button == null) { Dispose(); return; }
            foreach (var behaviour in buttonObject.GetComponentsInChildren<Behaviour>(true))
            {
                if (behaviour.GetType().Name is "Localize" or "UIGroupHandler" or "UIInputHint" or "UIInputHandler") behaviour.enabled = false;
            }
            var clickProperty = button.GetType().GetProperty("onClick");
            // Replace the whole event, including prefab persistent callbacks.
            // RemoveAllListeners alone would leave the player-list callback behind.
            var click = Activator.CreateInstance(clickProperty!.PropertyType) as UnityEvent;
            click!.AddListener(() => open());
            clickProperty.SetValue(button, click, null);
            foreach (var b in buttonObject.GetComponentsInChildren<Behaviour>(true))
                if (b.GetType().Name is "Localize" or "UIGroupHandler" or "UIInputHint" or "UIInputHandler") b.enabled = false;
            foreach (var c in buttonObject.GetComponentsInChildren<Component>(true))
            {
                if (!c) continue;
                if (c.GetType().Name == "Localize" && c is Behaviour behaviour) behaviour.enabled = false;
                if (GameUi.IsText(c))
                {
                    c.GetType().GetProperty("text")?.SetValue(c, "УПРАВЛЕНИЕ ПЕРСОНАЖЕМ", null);
                    float size = NativeAccess.Read(c, "fontSize") is float rendered ? rendered : 32;
                    float configured = NativeAccess.Read(c, "fontSizeMax") is float maximum ? maximum : size;
                    float ceiling = UiLayout.FontCeiling(size, configured);
                    c.GetType().GetProperty("enableWordWrapping")?.SetValue(c, false, null);
                    c.GetType().GetProperty("enableAutoSizing")?.SetValue(c, true, null);
                    c.GetType().GetProperty("fontSizeMax")?.SetValue(c, ceiling, null);
                    c.GetType().GetProperty("fontSizeMin")?.SetValue(c, ceiling * .65f, null);
                }
            }
            button.gameObject.AddComponent<MenuEntryFeedback>().Initialize(button);
            UiReflection.Field(menu.GetType(), "m_rebuildLayout")?.SetValue(menu, true);
        }
        if (!buttonObject || button == null || !source.gameObject.activeSelf)
        {
            // Single-player menus hide the player-list item; insert after Continue instead.
            var root = NativeAccess.Object(NativeAccess.Read(menu, "m_menuDialog"))?.transform;
            var fallback = root != null && root ? root.Find("MenuEntries/Continue") : null;
            if (fallback != null && fallback) source = fallback.GetComponent(source.GetType()) ?? source;
            var save = NativeAccess.Read(menu, "saveButton") as Component;
            if (save != null && save && save.gameObject.activeSelf &&
                save.GetType().GetProperty("interactable")?.GetValue(save, null) is true) source = save;
        }
        if (buttonObject == null || !buttonObject || button == null) return;
        buttonObject.SetActive(true);
        LayoutWithoutGroup(entry);
        LinkNavigation(source, button);
    }

    private void LayoutWithoutGroup(Transform entry)
    {
        var parent = entry.parent;
        var layoutType = GameUi.FindType("UnityEngine.UI.LayoutGroup");
        if (!parent || (layoutType != null && parent.GetComponent(layoutType))) return;
        var sourceRect = entry as RectTransform;
        var inserted = buttonObject!.transform as RectTransform;
        if (sourceRect == null || inserted == null) return;
        var buttonType = GameUi.FindType("UnityEngine.UI.Button");
        var siblings = parent.Cast<Transform>().Where(t => t != inserted && buttonType != null && t.GetComponentInChildren(buttonType, true))
            .OfType<RectTransform>().ToArray();
        foreach (var rect in siblings) if (!manualPositions.ContainsKey(rect)) manualPositions[rect] = rect.anchoredPosition;
        var origin = manualPositions[sourceRect];
        float spacing = siblings.Where(t => t != sourceRect).Select(t => Mathf.Abs(manualPositions[t].y - origin.y))
            .Where(distance => distance > 20 && distance < 100).DefaultIfEmpty(50).Min();
        inserted.anchoredPosition = origin + new Vector2(0, -spacing);
        foreach (var rect in siblings)
            rect.anchoredPosition = manualPositions[rect] + (rect.GetSiblingIndex() > inserted.GetSiblingIndex() ? new Vector2(0, -spacing) : Vector2.zero);
    }

    private static void LinkNavigation(Component source, Component inserted)
    {
        var property = UiReflection.Property(source.GetType(), "navigation");
        if (property == null) return;
        object sourceNavigation = property.GetValue(source, null);
        var type = sourceNavigation.GetType();
        var up = UiReflection.Property(type, "selectOnUp");
        var down = UiReflection.Property(type, "selectOnDown");
        var next = down?.GetValue(sourceNavigation, null) as Component;
        if (next == inserted) return;
        object insertedNavigation = property.GetValue(inserted, null);
        up?.SetValue(insertedNavigation, source, null);
        down?.SetValue(insertedNavigation, next, null);
        property.SetValue(inserted, insertedNavigation, null);
        down?.SetValue(sourceNavigation, inserted, null);
        property.SetValue(source, sourceNavigation, null);
        if (next)
        {
            object nextNavigation = property.GetValue(next, null);
            up?.SetValue(nextNavigation, inserted, null);
            property.SetValue(next, nextNavigation, null);
        }
    }

    public void Dispose()
    {
        if (buttonObject) UnityEngine.Object.Destroy(buttonObject);
        buttonObject = null;
        button = sourceButton = null;
        owner = null;
        manualPositions.Clear();
    }
}

[HarmonyPatch]
internal static class EscMenuPatch
{
    private static IEnumerable<MethodBase> TargetMethods()
    {
        foreach (string name in new[] { "Start", "Show" })
        {
            var method = AccessTools.Method(typeof(Menu), name);
            if (method != null) yield return method;
        }
    }
    private static void Postfix(Menu __instance) => Plugin.InstallMenuEntry(__instance);
}

[HarmonyPatch(typeof(Menu), nameof(Menu.IsVisible))]
internal static class PanelInputFocusPatch
{
    // Native player/camera input honours the same menu-focus check.
    private static void Postfix(ref bool __result) { if (Plugin.PanelOpen) __result = true; }
}

// Hover/controller navigation may run every frame. Only repair cached links;
// never rediscover the menu hierarchy from this callback.
[HarmonyPatch(typeof(Menu), "UpdateNavigation")]
internal static class EscNavigationPatch
{
    private static void Postfix(Menu __instance) => Plugin.RefreshMenuNavigation(__instance);
}
