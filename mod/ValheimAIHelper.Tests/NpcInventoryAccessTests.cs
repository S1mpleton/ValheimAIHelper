using Xunit;

namespace ValheimAIHelper.Tests;

public class NpcInventoryAccessTests
{
    [Theory]
    [InlineData(0, false, NpcInventoryAccessResult.Allowed)]
    [InlineData(3.5, false, NpcInventoryAccessResult.Allowed)]
    [InlineData(3.501, false, NpcInventoryAccessResult.TooFar)]
    [InlineData(3.9, true, NpcInventoryAccessResult.Allowed)]
    [InlineData(4, true, NpcInventoryAccessResult.Allowed)]
    [InlineData(4.001, true, NpcInventoryAccessResult.TooFar)]
    [InlineData(double.NaN, true, NpcInventoryAccessResult.TooFar)]
    [InlineData(double.PositiveInfinity, false, NpcInventoryAccessResult.TooFar)]
    [InlineData(-1, false, NpcInventoryAccessResult.TooFar)]
    public void DistanceHasOpenCloseHysteresisAndRejectsInvalidCoordinates(double distance, bool existing, NpcInventoryAccessResult expected)
        => Assert.Equal(expected, NpcInventoryAccess.Check(true, true, true, true, true, true, true, true, distance, existing));

    [Theory]
    [InlineData(false, true, true, true, true, true, true, true, NpcInventoryAccessResult.Unavailable)]
    [InlineData(true, false, true, true, true, true, true, true, NpcInventoryAccessResult.InvalidNpc)]
    [InlineData(true, true, false, true, true, true, true, true, NpcInventoryAccessResult.LoadingNpc)]
    [InlineData(true, true, true, false, true, true, true, true, NpcInventoryAccessResult.HostOnly)]
    [InlineData(true, true, true, true, false, true, true, true, NpcInventoryAccessResult.HostOnly)]
    [InlineData(true, true, true, true, true, false, true, true, NpcInventoryAccessResult.InvalidHuman)]
    [InlineData(true, true, true, true, true, true, false, true, NpcInventoryAccessResult.InvalidHuman)]
    [InlineData(true, true, true, true, true, true, true, false, NpcInventoryAccessResult.UiUnavailable)]
    public void InvalidSessionCannotExposeNpcBag(bool initialized, bool npc, bool ready, bool host, bool npcOwned, bool human, bool humanOwned, bool gui, NpcInventoryAccessResult expected)
    {
        Assert.Equal(expected, NpcInventoryAccess.Check(initialized, npc, ready, host, npcOwned, human, humanOwned, gui, 1, false));
        Assert.Equal(expected, NpcInventoryAccess.Check(initialized, npc, ready, host, npcOwned, human, humanOwned, gui, 1, true));
    }
}
