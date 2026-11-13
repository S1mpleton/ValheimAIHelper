using System;
using System.Collections.Generic;
using System.Linq;
using HarmonyLib;
using UnityEngine;

namespace ValheimAIHelper;

internal static class NativeAccess
{
    public static object? Read(object? target, params string[] names)
    {
        if (target == null) return null;
        foreach (string name in names)
        {
            var property = UiReflection.Property(target.GetType(), name);
            if (property != null && property.CanRead)
            { var value = property.GetValue(target, null); if (value != null) return value; }
            var field = UiReflection.Field(target.GetType(), name);
            if (field != null) { var value = field.GetValue(target); if (value != null) return value; }
        }
        return null;
    }
    public static object? Call(object target, string name, params object?[] args) => NativeMethods.Call(target, name, args);
    public static ItemDrop.ItemData? HeldItem(Player player) => Call(player, "GetRightItem") as ItemDrop.ItemData;
    public static GameObject? Object(object? value) => value is GameObject go ? go : (value as Component)?.gameObject;
}

internal static class GameUi
{
    public static Type? FindType(string name) => UiReflection.Resolve(name, AccessTools.TypeByName);
    public static bool IsText(Component component) => FindType("TMPro.TMP_Text")?.IsInstanceOfType(component) == true || FindType("UnityEngine.UI.Text")?.IsInstanceOfType(component) == true;
    public static IEnumerable<Component> Loaded(string type)
    {
        var t = FindType(type);
        return t == null ? Enumerable.Empty<Component>() : Resources.FindObjectsOfTypeAll(t).OfType<Component>().Where(c => c);
    }
    public static IEnumerable<GameObject> Roots(Menu menu)
    {
        yield return menu.gameObject;
        foreach (string name in new[] { "m_root", "m_menuDialog", "m_settingsPrefab", "m_settingsInstance", "menuEntriesParent", "saveButton", "menuCurrentPlayersListButton" })
        {
            var obj = NativeAccess.Object(NativeAccess.Read(menu, name));
            if (obj) yield return obj!;
        }
    }
    public static IEnumerable<Component> Components(Menu menu, string type)
    {
        var t = FindType(type);
        if (t == null) return Enumerable.Empty<Component>();
        return Roots(menu).SelectMany(r => r.GetComponentsInChildren(t, true)).Distinct().Where(c => c);
    }
    public static string Path(Transform t)
    {
        string path = t.name;
        for (int i = 0; t.parent && i < 8; i++) { t = t.parent; path = t.name + "/" + path; }
        return path;
    }
    public static Component? MenuButton(Menu menu)
    {
        var known = NativeAccess.Read(menu, "menuCurrentPlayersListButton", "saveButton", "m_firstMenuButton") as Component;
        var buttons = Components(menu, "UnityEngine.UI.Button").Where(c => Path(c.transform).IndexOf("ValheimAIHelperButton", StringComparison.OrdinalIgnoreCase) < 0).ToList();
        if (known && !buttons.Contains(known!)) buttons.Add(known!);
        if (buttons.Count == 0) buttons = Loaded("UnityEngine.UI.Button").Where(c => Path(c.transform).IndexOf("menu", StringComparison.OrdinalIgnoreCase) >= 0).ToList();
        return buttons.OrderByDescending(c => UiSelection.MenuButtonScore(c.name, Path(c.transform), c.gameObject.activeInHierarchy)).FirstOrDefault();
    }
    public static Transform EntryRoot(Menu menu, Component source)
    {
        var entries = NativeAccess.Object(NativeAccess.Read(menu, "menuEntriesParent"))?.transform;
        var current = source.transform;
        for (int i = 0; current.parent && i < 5; i++)
        {
            var parent = current.parent;
            if (parent == entries || new[] { "MenuEntries", "MenuList", "Buttons" }.Any(n => parent.name.Equals(n, StringComparison.OrdinalIgnoreCase))) return current;
            current = parent;
        }
        return source.transform;
    }
    public static Component? Font(Menu menu) => Components(menu, "TMPro.TMP_Text").FirstOrDefault(c => NativeAccess.Read(c, "font") != null)
        ?? Loaded("TMPro.TMP_Text").FirstOrDefault(c => NativeAccess.Read(c, "font") != null);
    public static Component? Canvas(Menu menu)
    {
        var type = FindType("UnityEngine.Canvas");
        if (type == null) return null;
        foreach (var root in Roots(menu))
        {
            var canvas = root.GetComponentInParent(type);
            if (canvas) return NativeAccess.Read(canvas, "rootCanvas") as Component ?? canvas;
        }
        return null;
    }
    public static float InterfaceScale(Menu menu)
    {
        var canvas = Canvas(menu);
        float scale = NativeAccess.Read(canvas, "scaleFactor") is float value ? value : 1;
        var reference = NativeAccess.Object(NativeAccess.Read(menu, "menuEntriesParent", "m_menuDialog", "m_root"));
        return UiLayout.EffectiveScale(scale, canvas ? Math.Abs(canvas!.transform.lossyScale.x) : 0,
            reference ? Math.Abs(reference!.transform.lossyScale.x) : 0);
    }
    public static Vector2 SettingsSize(Menu menu)
    {
        var settings = NativeAccess.Object(NativeAccess.Read(menu, "m_settingsInstance"));
        if (!settings) settings = NativeAccess.Object(NativeAccess.Read(menu, "m_settingsPrefab"));
        if (!settings) return Vector2.zero;
        // Ignore fullscreen dimmers and individual controls. Settings prefabs
        // commonly keep the actual dialog on a child RectTransform.
        var panel = settings!.GetComponentsInChildren<RectTransform>(true)
            .Where(r => r.rect.width >= 600 && r.rect.width <= 1400 && r.rect.height >= 450 && r.rect.height <= 1000)
            .OrderByDescending(r => r.rect.width * r.rect.height).FirstOrDefault();
        return panel ? panel!.rect.size : Vector2.zero;
    }
    public static string Describe(Menu menu) => "Menu roots=" + string.Join(",", Roots(menu).Select(r => r.name).Distinct()) +
        "; buttons=" + Components(menu, "UnityEngine.UI.Button").Count() + "; TMP=" + Components(menu, "TMPro.TMP_Text").Count();
}
