using System;
using System.Linq;
using System.IO;
using ValheimAIHelper;
using Xunit;

public class ItemCatalogTests
{
    [Fact] public void DuplicateNamesDoNotCollapseDistinctPrefabTypesOrObservedStacks()
    {
        var rows = ItemCatalogPolicy.Normalize(new[] {
            new ItemCatalogEntry { item_prefab="Stone",name="Камень",token="$item_stone",category="Material" },
            new ItemCatalogEntry { item_prefab="ModStone",name="Камень",token="$item_stone",category="Material" },
            new ItemCatalogEntry { item_prefab="Stone",name="Repeated prefab" },
            new ItemCatalogEntry { item_prefab="",name="Invalid unregistered item" }
        });
        Assert.Equal(new[] {"ModStone","Stone"},rows.Select(e=>e.item_prefab));
        var snapshot = new Snapshot { item_catalog=rows, item_catalog_total=2,
            actor=new Actor { inventory=new[] {
                new InventoryEntry {id="inventory:1",item_prefab="Stone",count=5},
                new InventoryEntry {id="inventory:2",item_prefab="Stone",count=3}
            } } };
        var wire = WireJson.Deserialize<Snapshot>(WireJson.Serialize(snapshot));
        Assert.Equal(2,wire.item_catalog.Length);
        Assert.Equal(2,wire.actor.inventory.Length);
        Assert.NotEqual(wire.actor.inventory[0].id,wire.actor.inventory[1].id);
        Assert.Empty(wire.entities); // A type catalog must never create available targets.
    }

    [Theory]
    [InlineData("Material")]
    [InlineData("Consumable")]
    [InlineData("Trophy")]
    [InlineData("Misc")]
    public void NonEquipmentDoesNotExposeSharedDataDefaultCombatStats(string category)
    {
        Assert.False(ItemCatalogPolicy.HasArmor(category));
        Assert.False(ItemCatalogPolicy.HasBlock(category,false));
        Assert.False(ItemCatalogPolicy.HasDamage(category,false));
        Assert.False(ItemCatalogPolicy.HasSkill(category,false));
        Assert.False(ItemCatalogPolicy.HasToolTier(category,false));
    }

    [Fact] public void ArmorWeaponsAndProjectilesKeepTheirMeaningfulDifferentStats()
    {
        Assert.True(ItemCatalogPolicy.HasArmor("Helmet"));
        Assert.False(ItemCatalogPolicy.HasArmor("Shield"));
        Assert.True(ItemCatalogPolicy.HasBlock("Shield",false));
        Assert.False(ItemCatalogPolicy.HasSkill("Shield",false));
        Assert.True(ItemCatalogPolicy.HasBlock("OneHandedWeapon",true));
        Assert.True(ItemCatalogPolicy.HasSkill("OneHandedWeapon",true));
        Assert.False(ItemCatalogPolicy.HasBlock("Bow",true));
        Assert.True(ItemCatalogPolicy.HasDamage("Ammo",false));
        Assert.False(ItemCatalogPolicy.HasSkill("Ammo",false)); // Attacks use the bow's skill, not SharedData's ammo default.
        Assert.False(ItemCatalogPolicy.HasArmor("Utility")); // Native Player.GetBodyArmor excludes belts/wishbones.
    }

    [Fact] public void CatalogExportCanRetainAllModItemsWhileSnapshotIsBounded()
    {
        var all = ItemCatalogPolicy.Normalize(Enumerable.Range(0,4100).Select(i=>new ItemCatalogEntry {item_prefab="ModItem"+i}));
        Assert.Equal(4100,all.Length);
        Assert.Equal(4096,all.Take(ItemCatalogPolicy.SnapshotLimit).Count());
    }

    [Fact] public void HumanReadableExportContainsAllItemsAndReplacesAnExistingVersion()
    {
        string directory = Path.Combine(Path.GetTempPath(),"aihelper-catalog-"+Guid.NewGuid().ToString("N"));
        string path = Path.Combine(directory,"item-catalog.json");
        try
        {
            var rows = ItemCatalogPolicy.Normalize(Enumerable.Range(0,4100).Select(i=>new ItemCatalogEntry
            {item_prefab="ModItem"+i,name="Предмет "+i,token="$mod_item_"+i,category="Material"}));
            ItemCatalogFile.Write(path,"1.0.21","Russian","old",Array.Empty<ItemCatalogEntry>());
            ItemCatalogFile.Write(path,"1.0.21","Russian","new",rows);
            string text = File.ReadAllText(path);
            Assert.Contains("\n",text); Assert.Contains("Предмет 4000",text);
            var exported = WireJson.Deserialize<ItemCatalogExport>(text);
            Assert.Equal(4100,exported.total); Assert.Equal(4100,exported.items.Length);
            Assert.Equal("new",exported.version); Assert.Equal("Russian",exported.language);
            Assert.Equal("$mod_item_4000",exported.items.Single(i=>i.item_prefab=="ModItem4000").token);
            Assert.False(File.Exists(path+".tmp"));
        }
        finally { if(Directory.Exists(directory)) Directory.Delete(directory,true); }
    }

    [Fact] public void FailedCatalogWritePreservesPreviousCompleteFile()
    {
        string directory = Path.Combine(Path.GetTempPath(),"aihelper-catalog-"+Guid.NewGuid().ToString("N"));
        string path = Path.Combine(directory,"item-catalog.json");
        try
        {
            ItemCatalogFile.Write(path,"1.0.21","Russian","original",Array.Empty<ItemCatalogEntry>());
            string original = File.ReadAllText(path);
            Directory.CreateDirectory(path+".tmp"); // A filesystem failure before the new file is committed.
            var error = Record.Exception(()=>ItemCatalogFile.Write(path,"1.0.21","Russian","new",Array.Empty<ItemCatalogEntry>()));
            Assert.True(error is IOException || error is UnauthorizedAccessException);
            Assert.Equal(original,File.ReadAllText(path));
        }
        finally { if(Directory.Exists(directory)) Directory.Delete(directory,true); }
    }
}
