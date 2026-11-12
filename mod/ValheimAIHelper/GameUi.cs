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
            var property = AccessTools.Property(target.GetType(), name);
            if (property != null && property.CanRead)
            { var value = property.GetValue(target, null); if (value != null) return value; }
            var field = AccessTools.Field(target.GetType(), name);
            if (field != null) { var value = field.GetValue(target); if (value != null) return value; }
        }
        return null;
    }
    public static object? Call(object target, string name, params object?[] args)
    {
        var method = target.GetType().GetMethods(System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic)
            .FirstOrDefault(m => m.Name == name && m.GetParameters().Length == args.Length &&
                m.GetParameters().Select((v, i) => args[i] == null || v.ParameterType.IsInstanceOfType(args[i])).All(v => v));
        if (method == null) throw new MissingMethodException(target.GetType().Name, name);
        return method.Invoke(target, args);
    }
    public static GameObject? Object(object? value) => value is GameObject go ? go : (value as Component)?.gameObject;
}

internal static class GameUi
{
    public static Type? FindType(string name) => AccessTools.TypeByName(name);
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
    public static Component? Font(Menu menu) => Components(menu, "TMPro.TMP_Text").Concat(Loaded("TMPro.TMP_Text"))
        .FirstOrDefault(c => NativeAccess.Read(c, "font") != null);
    public static string Describe(Menu menu) => "Menu roots=" + string.Join(",", Roots(menu).Select(r => r.name).Distinct()) +
        "; buttons=" + Components(menu, "UnityEngine.UI.Button").Count() + "; TMP=" + Components(menu, "TMPro.TMP_Text").Count();
}
