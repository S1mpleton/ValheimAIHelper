using System;
using ValheimAIHelper;
using Xunit;

public class Actions040Tests
{
    private static Snapshot World() => new()
    {
        sequence = 1, supported_actions = Capabilities.All, container_id = "chest",
        actor = new Actor { inventory = new[] { new InventoryEntry { id = "stones", count = 20 }, new InventoryEntry { id = "weapon", count = 1, equipable = true } } },
        container_inventory = new[] { new InventoryEntry { id = "stored", count = 10 } },
        entities = new[] {
            new Entity { id = "chest", kind = "interactable", category = "container", distance = 2, position = new Position { x = 2 } },
            new Entity { id = "deer", kind = "animal", distance = 2, position = new Position { x = 2 } },
            new Entity { id = "ore", kind = "resource", distance = 2, position = new Position { x = 2 } },
        },
        recipes = new[] { new RecipeOption { id = "axe", craftable = true } },
        build_options = new[] { new BuildOption { id = "campfire", available = true } },
    };
    private static Decision D(string action, string id = "", string item = "", int amount = 1) => new()
    { protocol_version = 1, sequence = 1, action = action, target_id = id, item_id = item, amount = amount, duration = 1, say = "" };

    [Theory]
    [InlineData("drop", "stones", "", 5)]
    [InlineData("take", "chest", "stored", 4)]
    [InlineData("deposit", "chest", "stones", 3)]
    [InlineData("gather", "ore", "", 1)]
    [InlineData("hunt", "deer", "", 1)]
    [InlineData("craft", "axe", "", 1)]
    [InlineData("upgrade", "axe", "weapon", 1)]
    public void NativeActionsRequireObservedCorrectInventory(string action, string id, string item, int amount) => Assert.True(DecisionPolicy.Accept(D(action,id,item,amount),World(),true));

    [Theory]
    [InlineData("drop", "stones", "", 21)]
    [InlineData("drop", "stored", "", 1)]
    [InlineData("take", "chest", "stones", 1)]
    [InlineData("deposit", "chest", "stored", 1)]
    [InlineData("attack", "deer", "", 1)]
    [InlineData("gather", "chest", "", 1)]
    public void WrongTargetsOrInventedQuantitiesFail(string action, string id, string item, int amount) => Assert.False(DecisionPolicy.Accept(D(action,id,item,amount),World(),true));

    [Fact] public void ContainerTransferRequiresAnActuallyOpenContainer()
    { var s = World(); s.container_id = ""; Assert.False(DecisionPolicy.Accept(D("take","chest","stored",1),s,true)); }
    [Fact] public void ModelAvoidPolicyCannotRequestAnAttackEvenWhenCombatIsConfigured()
    { var d = D("hunt","deer"); d.avoid_combat = true; Assert.False(DecisionPolicy.Accept(d,World(),true)); }
    [Fact] public void PlacementRequiresBoundedPointAndAnAvailablePiece()
    {
        var d = D("build", "campfire"); d.destination = new Position { x = 3 };
        Assert.True(DecisionPolicy.Accept(d,World(),true));
        d.destination.x = 9; Assert.False(DecisionPolicy.Accept(d,World(),true));
        d.destination.x = 3; var s = World(); s.build_options[0].available = false;
        Assert.False(DecisionPolicy.Accept(d,s,true));
    }
    [Theory]
    [InlineData("Бъерн, выбрось 5 камней", "Бъерн", "выбрось 5 камней")]
    [InlineData("подбери камни, БЬЁРН", "Бьерн", "подбери камни")]
    [InlineData("!ai стоп", "Любое имя", "стоп")]
    public void CharacterNameIsAnExplicitAddressWithoutAPrefix(string text,string name,string expected)
    { Assert.True(LeaderCommand.Addressed(text,name,out var command)); Assert.Equal(expected,command); }
    [Theory]
    [InlineData("Сегодня Бъернище пришёл", "Бъерн")]
    [InlineData("И ещё подбери камни", "Бъерн")]
    public void UnnamedMessagesAreLeftForContextRatherThanBlindlyExecuted(string text,string name)
    { Assert.False(LeaderCommand.Addressed(text,name,out _)); }
    [Fact] public void EscFallbackPrefersRealMainMenuEntriesOverSettingsDialogs()
    {
        int resume = UiSelection.MenuButtonScore("Continue","Canvas/Menu/MenuEntries/Continue",true);
        int dialog = UiSelection.MenuButtonScore("Save","Canvas/Settings/Dialog/Save",true);
        Assert.True(resume > dialog);
        Assert.Equal(int.MinValue,UiSelection.MenuButtonScore("ValheimAIHelperButton","Menu",true));
        Assert.DoesNotContain("build",Capabilities.Supported(true,false));
        Assert.DoesNotContain("hunt",Capabilities.Supported(false,true));
        Assert.Contains("drop",Capabilities.Supported(false,false));
    }
}
