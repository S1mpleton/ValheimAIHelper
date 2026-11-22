using System;
using System.Linq;
using ValheimAIHelper;
using Xunit;

public class Collectible081Tests
{
    [Fact] public void DelayedPickRpcIsIssuedOnceAndWaitsForGameEvidence()
    {
        var receipt = new CollectibleReceipt();
        Assert.True(receipt.TryIssue(10));
        Assert.Equal(CollectibleResult.Waiting,receipt.Observe(10.1f,false));
        Assert.False(receipt.TryIssue(10.5f)); // Same lease/target renewed on next snapshot.
        Assert.Equal(CollectibleResult.Waiting,receipt.Observe(12,false));
        Assert.Equal(CollectibleResult.Confirmed,receipt.Observe(12.2f,true));
        Assert.False(receipt.TryIssue(12.3f));
        receipt.Reset();
        Assert.True(receipt.TryIssue(15)); // A new authorized task may issue a different pick.
    }
    [Fact] public void ATrueInteractReturnWithoutGameChangeIsNotAConfirmedPickup()
    {
        var receipt = new CollectibleReceipt();receipt.TryIssue(0);
        Assert.Equal(CollectibleResult.Waiting,receipt.Observe(5.9f,false));
        Assert.Equal(CollectibleResult.TimedOut,receipt.Observe(6,false));
        Assert.False(receipt.TryIssue(7)); // No automatic repeated mutation after uncertainty.
        Assert.Equal(CollectibleResult.Confirmed,receipt.Observe(7,true)); // Late evidence still takes priority.
    }
    private static Entity Pickup(string id, string prefab, float distance, bool generated = false) => new()
    {
        id = id, name = prefab, item_prefab = prefab, distance = distance,
        position = new Position { x = distance }, kind = generated ? "interactable" : "item",
        category = generated ? "pickable" : "Material", interaction = generated ? "interact" : "pickup",
        produces = generated ? new[] { prefab } : Array.Empty<string>()
    };

    [Fact] public void DenseBerryFieldCannotHideGeneratedAxeTorchOrDandelion()
    {
        var berries = Enumerable.Range(0,120).Select(i=>Pickup("berry:"+i,"Raspberry",1+i*.05f,true));
        var ground = berries.Concat(new[] {
            Pickup("axe","AxeStone",12,true), Pickup("torch","Torch",13,true),
            Pickup("flower","Dandelion",14,true), Pickup("stones","Stone",15)
        });
        var selected = CollectiblePolicy.Select(ground,80);
        Assert.Equal(80,selected.Length);
        Assert.All(new[]{"AxeStone","Torch","Dandelion","Stone"},p=>Assert.Contains(selected,e=>e.item_prefab==p));
        Assert.Contains(selected,e=>e.id=="berry:0");
        Assert.Equal(selected.Length,selected.Select(e=>e.id).Distinct().Count());
    }

    [Fact] public void GeneratedAndDroppedCopiesKeepIndependentTargetsAndUseClosestTypeRepresentative()
    {
        var generated = Pickup("pickable-axe","AxeStone",7,true);
        var drop = Pickup("drop-axe","AxeStone",3);
        var flower = Pickup("dandelion","Dandelion",8,true);
        var selected = CollectiblePolicy.Select(new[]{generated,drop,flower},2);
        Assert.Contains(selected,e=>e.id==drop.id);
        Assert.Contains(selected,e=>e.id==flower.id);
        Assert.DoesNotContain(selected,e=>e.id==generated.id);
        Assert.Equal(3,CollectiblePolicy.Select(new[]{generated,drop,flower},3).Length);
    }

    [Fact] public void UnloadedOrNonCollectibleObjectsNeverBecomeGroundTargets()
    {
        var selected = CollectiblePolicy.Select(new[] {
            Pickup("near","Stone",5), Pickup("far","AxeStone",129,true),
            new Entity {id="rock",kind="resource",category="MineRock",distance=1},
            new Entity {id="station",kind="interactable",category="station",distance=1},
            new Entity {id="unknown",kind="interactable",category="pickable",distance=1},
            Pickup("nonfinite","Stone",float.NaN)
        },80);
        Assert.Single(selected); Assert.Equal("near",selected[0].id);
        Assert.Empty(CollectiblePolicy.Select(selected,0));
    }

    [Theory]
    [InlineData("AxeStone(Clone)","AxeStone")]
    [InlineData("Torch(Clone)(Clone)","Torch")]
    [InlineData("Mod_item_Axe", "Mod_item_Axe")]
    [InlineData("axestone", "axestone")]
    public void ObservedPrefabIdentityStripsUnityInstanceSuffixWithoutGuessingItemNames(string observed,string prefab)
    { Assert.Equal(prefab,ItemCatalogPolicy.PrefabName(observed)); }

    [Fact] public void GeneratedItemCanBeApproachedFromLoadedRadiusButStationsCannot()
    {
        var axe = Pickup("axe","AxeStone",45,true);
        var snapshot = new Snapshot {sequence=1,supported_actions=new[]{"interact"},entities=new[]{axe}};
        var decision = new Decision {protocol_version=1,sequence=1,action="interact",target_id="axe",duration=1,say=""};
        Assert.True(DecisionPolicy.Accept(decision,snapshot,false));
        axe.category="station";
        Assert.False(DecisionPolicy.Accept(decision,snapshot,false));
        axe.category="pickable";axe.produces=Array.Empty<string>();
        Assert.False(DecisionPolicy.Accept(decision,snapshot,false));
        axe.produces=new[]{"AxeStone"};axe.position.x=129;
        Assert.False(DecisionPolicy.Accept(decision,snapshot,false));
    }
}
