using ValheimAIHelper;
using Xunit;

public sealed class DecisionPolicyTests
{
    private static Snapshot World() => new()
    {
        sequence = 4, leader_id = "leader",
        actor = new Actor { position = new Position(), inventory = new[] {
            new InventoryEntry { id = "food", food = true },
            new InventoryEntry { id = "weapon", equipable = true }
        } },
        entities = new[] {
            new Entity { id = "leader", kind = "player", position = new Position { x = 4 }, distance = 4 },
            new Entity { id = "enemy", kind = "enemy", position = new Position { x = 3 }, distance = 3 },
            new Entity { id = "drop", kind = "item", position = new Position { x = 2 }, distance = 2 }
        }
    };
    private static Decision Action(string action, string id = "") => new()
    { protocol_version = 1, sequence = 4, action = action, target_id = id, duration = 2, say = "" };

    [Theory]
    [InlineData("follow", "leader")]
    [InlineData("pickup", "drop")]
    [InlineData("eat", "food")]
    [InlineData("equip", "weapon")]
    [InlineData("wait", "")]
    public void KnownActionsPass(string action, string target) => Assert.True(DecisionPolicy.Accept(Action(action, target), World(), false));

    [Theory]
    [InlineData("attack", "leader")]
    [InlineData("pickup", "weapon")]
    [InlineData("equip", "food")]
    [InlineData("teleport", "leader")]
    [InlineData("follow", "missing")]
    public void InvalidTargetsFail(string action, string target) => Assert.False(DecisionPolicy.Accept(Action(action, target), World(), true));

    [Fact] public void CombatRequiresOptIn()
    {
        Assert.False(DecisionPolicy.Accept(Action("attack", "enemy"), World(), false));
        Assert.True(DecisionPolicy.Accept(Action("attack", "enemy"), World(), true));
    }

    [Fact] public void StaleSequenceFails()
    {
        var d = Action("wait"); d.sequence = 3;
        Assert.False(DecisionPolicy.Accept(d, World(), true));
    }

    [Theory]
    [InlineData(float.NaN)] [InlineData(float.PositiveInfinity)] [InlineData(-1)] [InlineData(4)]
    public void UnboundedDurationFails(float duration)
    {
        var d = Action("wait"); d.duration = duration;
        Assert.False(DecisionPolicy.Accept(d, World(), true));
    }

    [Fact] public void RecomputeTargetDistance()
    {
        var world = World(); world.entities[1].position.x = 100;
        Assert.False(DecisionPolicy.Accept(Action("attack", "enemy"), world, true));
    }
}
