using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using BepInEx;
using UnityEngine;

namespace ValheimAIHelper;

// Read prefab definitions from the loaded game's database (including other
// mods). Never instantiate, grant items, roll drop tables or scan world state.
internal static class ItemCatalog
{
    private static ObjectDB? database;
    private static Localization? localization;
    private static List<GameObject>? source;
    private static string language = "";
    private static int sourceCount = -1, fingerprint;
    private static float nextCheck;
    private static ItemCatalogEntry[] entries = Array.Empty<ItemCatalogEntry>();
    private static ItemCatalogEntry[] snapshotEntries = Array.Empty<ItemCatalogEntry>();
    private static readonly Dictionary<ItemDrop.ItemData.SharedData, string> sharedPrefabs = new();
    internal static string Version { get; private set; } = "";
    internal static int Count => entries.Length;
    internal static ItemCatalogEntry[] SnapshotEntries => snapshotEntries;
    internal static string ExportPath => Path.Combine(Paths.ConfigPath, "ValheimAIHelper", "item-catalog.json");
    internal static string ExportError { get; private set; } = "";

    internal static void Refresh()
    {
        var db = ObjectDB.instance;
        if (!db || db.m_items == null) return;
        var loc = Localization.instance;
        string selected = loc != null ? loc.GetSelectedLanguage() : "";
        bool sameSource = database == db && ReferenceEquals(source, db.m_items) && sourceCount == db.m_items.Count &&
            ReferenceEquals(localization, loc) && language == selected;
        float now = Time.realtimeSinceStartup;
        if (sameSource && now < nextCheck) return;
        int signature = 17;
        foreach (var prefab in db.m_items)
            unchecked { signature = signature * 31 + (prefab ? prefab.GetInstanceID() : 0); }
        nextCheck = now + 2;
        if (sameSource && signature == fingerprint) return;
        database = db; localization = loc; source = db.m_items; sourceCount = source.Count;
        language = selected; fingerprint = signature;
        sharedPrefabs.Clear();
        var rows = new List<ItemCatalogEntry>();
        foreach (var prefab in source)
        {
            if (!prefab) continue;
            var drop = prefab.GetComponent<ItemDrop>();
            var item = drop ? drop.m_itemData : null;
            if (item?.m_shared == null || string.IsNullOrWhiteSpace(prefab.name)) continue;
            string id = prefab.name;
            if (!sharedPrefabs.ContainsKey(item.m_shared)) sharedPrefabs.Add(item.m_shared, id);
            string displayName = Observation.Name(item.m_shared.m_name);
            rows.Add(new ItemCatalogEntry { item_prefab = id, name = displayName.Length > 0 ? displayName : id,
                token = ObservationValues.Text(item.m_shared.m_name, 160), category = item.m_shared.m_itemType.ToString() });
        }
        entries = ItemCatalogPolicy.Normalize(rows);
        snapshotEntries = entries.Take(ItemCatalogPolicy.SnapshotLimit).ToArray();
        using (var hash = SHA256.Create())
            Version = BitConverter.ToString(hash.ComputeHash(Encoding.UTF8.GetBytes(language + "\n" + WireJson.Serialize(entries)))).Replace("-", "").ToLowerInvariant();
        Export();
    }

    internal static string Prefab(ItemDrop.ItemData item)
    {
        if (item.m_dropPrefab) return ItemCatalogPolicy.PrefabName(item.m_dropPrefab.name);
        return item.m_shared != null && sharedPrefabs.TryGetValue(item.m_shared, out var id) ? id : "";
    }

    internal static ItemDrop? ProducedItem(Component observed)
    {
        if (observed is Pickable plant && plant.m_itemPrefab) return plant.m_itemPrefab.GetComponent<ItemDrop>();
        if (observed is PickableItem pickable)
        {
            if (pickable.m_itemPrefab) return pickable.m_itemPrefab;
            // Only the already selected visual instance is examined. A random
            // prefab table is not a list of items actually present in the world.
            if (NativeAccess.Read(pickable, "m_instance") is GameObject visual && visual)
                return visual.GetComponent<ItemDrop>() ?? visual.GetComponentInChildren<ItemDrop>();
        }
        return null;
    }

    internal static string[] Produces(Component observed)
    {
        // Only the already observed plant/pickable's declared output is read.
        // Random tables are not rolled and unobserved objects are not inspected.
        var item = ProducedItem(observed);
        if (!item || item!.m_itemData == null) return Array.Empty<string>();
        string prefab = Prefab(item.m_itemData);
        if (prefab.Length == 0) prefab = ItemCatalogPolicy.PrefabName(item.gameObject.name);
        return prefab.Length > 0 ? new[] { prefab } : Array.Empty<string>();
    }

    private static void Export()
    {
        try
        {
            ItemCatalogFile.Write(ExportPath, global::Version.GetVersionString(), language, Version, entries);
            ExportError = "";
        }
        catch (Exception ex)
        {
            ExportError = ex.GetType().Name + ": " + ex.Message;
        }
    }
}
