using ValheimAIHelper;
using Xunit;

public class ControlIntentTests
{
    [Fact] public void UphillAndDownhillWaypointsProduceHorizontalInput()
    {
        foreach (float y in new[] { -10f, 0f, 10f })
        {
            var input = ControlIntent.Create(3, y, 4, false);
            Assert.Equal(.6f, input.X, 5);
            Assert.Equal(.8f, input.Z, 5);
            Assert.Equal(1f, input.Magnitude);
        }
    }

    [Fact] public void VerticalOnlyPathCannotRequestMovement()
    {
        var input = ControlIntent.Create(0, 10, 0, false);
        Assert.Equal(0, input.Magnitude);
        Assert.False(input.Attack);
    }

    [Fact] public void SlowMovementIsPreservedAndLargeCoordinatesRemainBounded()
    {
        Assert.Equal(.2f, ControlIntent.Create(.2f, 0, 0, false).Magnitude, 5);
        var large = ControlIntent.Create(float.MaxValue, 0, float.MaxValue, false);
        Assert.Equal(1, large.Magnitude);
        Assert.Equal(1, large.X * large.X + large.Z * large.Z, 5);
    }

    [Theory]
    [InlineData(float.NaN, 0, 1)]
    [InlineData(1, float.PositiveInfinity, 0)]
    [InlineData(0, 0, float.NegativeInfinity)]
    public void InvalidInputStopsMovementAndAttack(float x, float y, float z)
    {
        var input = ControlIntent.Create(x, y, z, true);
        Assert.Equal(0, input.Magnitude);
        Assert.False(input.Attack);
    }
}
