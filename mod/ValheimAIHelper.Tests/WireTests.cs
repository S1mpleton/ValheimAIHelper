using System;
using ValheimAIHelper;
using Xunit;

public sealed class WireTests
{
    [Fact] public void RoundTripAllFieldsAndUnicode()
    {
        var original = new Snapshot { session_id = "session", sequence = 5, previous_result = "Строка\n\"ответа\"",
            actor = new Actor { name = "Бьёрн", health = 25, max_health = 25, stamina = 0,
                position = new Position { x = -100.25f, y = 32.5f, z = 4200.75f } } };
        string json = WireJson.Serialize(original);
        var restored = WireJson.Deserialize<Snapshot>(json);
        Assert.Equal(original.actor.name, restored.actor.name);
        Assert.Equal(original.previous_result, restored.previous_result);
        Assert.Equal(-100.25f, restored.actor.position.x);
        Assert.Equal(1, restored.protocol_version);
        Assert.Contains("move", restored.supported_actions);
        Assert.Empty(restored.actor.inventory);
    }

    [Theory]
    [InlineData(-.01f, 0)] [InlineData(-50, 0)] [InlineData(25, 25)]
    [InlineData(float.NaN, 0)] [InlineData(float.PositiveInfinity, 0)]
    public void VitalsAreFiniteNonnegative(float input, float expected) => Assert.Equal(expected, ObservationValues.Vital(input));

    [Fact] public void ErrorsUseSafeStructuredDetail()
    {
        var error = BridgeFailure.Describe(422, "{\"detail\":\"actor.stamina (greater_than_equal)\",\"errors\":[]}");
        Assert.Contains("actor.stamina", error);
        Assert.DoesNotContain("Мост недоступен", error);
        Assert.DoesNotContain("password", BridgeFailure.Describe(500, "<html>password</html>"));
        Assert.Contains("сопряжения", BridgeFailure.Describe(401, ""));
    }

    [Fact] public void ForwardMovementHasNoArbitraryTarget()
    {
        var s = new Snapshot { sequence = 1 };
        var d = new Decision { protocol_version = 1, sequence = 1, action = "move", target_id = "", duration = 3, say = "Иду" };
        Assert.True(DecisionPolicy.Accept(d, s, false));
        d.target_id = "teleport-coordinates";
        Assert.False(DecisionPolicy.Accept(d, s, false));
    }
}
