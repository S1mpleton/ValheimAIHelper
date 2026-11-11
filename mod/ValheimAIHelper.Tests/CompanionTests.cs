using System;
using ValheimAIHelper;
using Xunit;

public class CompanionTests
{
    [Theory]
    [InlineData("!ai стоп", "стоп")]
    [InlineData("  !AI следуй за мной ", "следуй за мной")]
    [InlineData("!ии защищай меня", "защищай меня")]
    [InlineData("!ai", "")]
    [InlineData("!air стоп", null)]
    [InlineData("кто-то сказал !ai стоп", null)]
    [InlineData("обычное сообщение", null)]
    public void OnlyExplicitLeaderCommandsAreParsed(string text, string? expected) => Assert.Equal(expected, LeaderCommand.Parse(text));

    [Fact] public void JournalRedactsRegisteredAndRecognizableSecretsBeforeTruncation()
    {
        var log = new LogJournal();
        log.RegisterSecret("short-private-value");
        log.RegisterSecret("a-local-pairing-code");
        log.Add("api", "key=short-private-value Bearer a-local-pairing-code sk-proj-" + new string('x', 80));
        Assert.DoesNotContain("short-private-value", log.Text);
        Assert.DoesNotContain("a-local-pairing-code", log.Text);
        Assert.DoesNotContain(new string('x', 20), log.Text);
        Assert.Contains("REDACTED", log.Text);
        for (int i = 0; i < 500; i++) log.Add("test", i.ToString());
        Assert.Equal(300, log.Text.Split('\n').Length);
        log.Clear(); Assert.Equal("", log.Text);
    }

    [Fact] public void AimAndMovementAreIndependentAndInputStaysBounded()
    {
        var input = ControlIntent.Create(0, 0, -1, false).With(0, 1, 2, hold: true, block: true, run: true);
        Assert.Equal(-1, input.Z);
        Assert.True(input.AimZ > 0 && input.AimY > 0 && input.AttackHold && input.Block && input.Run);
        Assert.InRange(input.AimX*input.AimX + input.AimY*input.AimY + input.AimZ*input.AimZ, .999f, 1.001f);
        Assert.False(input.With(float.NaN).HasAim);
    }

    [Fact] public void NewDecisionFieldsRoundTripAndOutOfRangeDestinationFailsLocally()
    {
        var s = new Snapshot { sequence = 1, supported_actions = new[] { "move" } };
        var d = new Decision { protocol_version = 1, sequence = 1, action = "move", target_id = "", say = "Иду", duration = 3,
            plan_id = "plan-step", destination = new Position { x = 5 }, run = true };
        var copy = WireJson.Deserialize<Decision>(WireJson.Serialize(d));
        Assert.True(DecisionPolicy.Accept(copy, s, false));
        Assert.Equal("plan-step", copy.plan_id); Assert.True(copy.run);
        copy.destination!.x = 31; Assert.False(DecisionPolicy.Accept(copy, s, false));
        copy.destination.x = float.NaN; Assert.False(DecisionPolicy.Accept(copy, s, false));
    }
}
