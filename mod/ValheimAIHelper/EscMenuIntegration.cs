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
    private Component? button;
    private Menu? owner;
    private readonly Action open;

    public EscMenuIntegration(Action open) { this.open = open; }

    public void Install(Menu menu)
    {
        // Reflect UI-only fields so changes to TMPro/UGUI assembly versions don't
        // introduce another runtime dependency into the mod.
        var source = AccessTools.Field(typeof(Menu), "menuCurrentPlayersListButton")?.GetValue(menu) as Component;
        if (source == null || !source) return;
        if (owner != menu || !buttonObject)
        {
            Dispose();
            owner = menu;
            buttonObject = UnityEngine.Object.Instantiate(source.gameObject, source.transform.parent, false);
            buttonObject.name = "ValheimAIHelperButton";
            buttonObject.transform.SetSiblingIndex(source.transform.GetSiblingIndex() + 1);
            buttonObject.SetActive(true);
            button = buttonObject.GetComponent(source.GetType());
            if (button == null) { Dispose(); return; }
            var clickProperty = button.GetType().GetProperty("onClick");
            // Replace the whole event, including prefab persistent callbacks.
            // RemoveAllListeners alone would leave the player-list callback behind.
            var click = Activator.CreateInstance(clickProperty!.PropertyType) as UnityEvent;
            click!.AddListener(() => open());
            clickProperty.SetValue(button, click, null);
            foreach (var c in buttonObject.GetComponentsInChildren<Component>(true))
            {
                if (!c) continue;
                if (c.GetType().Name == "Localize" && c is Behaviour behaviour) behaviour.enabled = false;
                if (c.GetType().Name is "TextMeshProUGUI" or "Text")
                    c.GetType().GetProperty("text")?.SetValue(c, "AI-ПОМОЩНИК", null);
            }
            AccessTools.Field(typeof(Menu), "m_rebuildLayout")?.SetValue(menu, true);
        }
        if (!buttonObject || button == null || !source.gameObject.activeSelf)
        {
            // Single-player menus hide the player-list item; insert after Continue instead.
            var root = AccessTools.Field(typeof(Menu), "m_menuDialog")?.GetValue(menu) as Transform;
            var fallback = root != null && root ? root.Find("MenuEntries/Continue") : null;
            if (fallback != null && fallback) source = fallback.GetComponent(source.GetType()) ?? source;
            var save = AccessTools.Field(typeof(Menu), "saveButton")?.GetValue(menu) as Component;
            if (save != null && save && save.gameObject.activeSelf &&
                save.GetType().GetProperty("interactable")?.GetValue(save, null) is true) source = save;
        }
        if (buttonObject == null || !buttonObject || button == null) return;
        buttonObject.SetActive(true);
        LinkNavigation(source, button);
    }

    private static void LinkNavigation(Component source, Component inserted)
    {
        var property = source.GetType().GetProperty("navigation");
        if (property == null) return;
        object sourceNavigation = property.GetValue(source, null);
        var type = sourceNavigation.GetType();
        var up = type.GetProperty("selectOnUp");
        var down = type.GetProperty("selectOnDown");
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
        button = null;
        owner = null;
    }
}

[HarmonyPatch]
internal static class EscMenuPatch
{
    private static IEnumerable<MethodBase> TargetMethods()
    {
        foreach (string name in new[] { "Start", "Show", "UpdateNavigation" })
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
