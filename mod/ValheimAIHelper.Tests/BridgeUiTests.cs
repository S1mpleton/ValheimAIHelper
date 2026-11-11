using ValheimAIHelper;
using Xunit;

public class BridgeUiTests
{
    [Theory]
    [InlineData("http://127.0.0.1:8765")]
    [InlineData("http://localhost:8765/")]
    [InlineData("http://[::1]:8765")]
    public void UiCredentialsCanBeSentToLoopbackOnly(string address) => Assert.True(BridgeAddress.IsValid(address));

    [Theory]
    [InlineData("https://api.example.com")]
    [InlineData("http://127.0.0.1@api.example.com")]
    [InlineData("http://127.0.0.1:8765/other")]
    [InlineData("http://localhost:8765/?token=value")]
    [InlineData("http://localhost:8765/#other")]
    [InlineData("http://user:pass@localhost:8765")]
    public void RemoteOrAmbiguousUiDestinationsAreRejected(string address) => Assert.False(BridgeAddress.IsValid(address));

    [Fact] public void InGameStatusReadsKeyPresenceAndDetailedErrorWithoutKeyValues()
    {
        const string json = "{\"provider\":\"openai\",\"model\":\"gpt-6-luna\",\"mode\":\"stop\",\"keys_present\":{\"openai\":true,\"gemini\":false},\"provider_error\":{\"message\":\"API overloaded\",\"detail\":\"key=[REDACTED]\"}}";
        var state = WireJson.Deserialize<BridgeUiStatus>(json);
        Assert.True(state.keys_present.openai);
        Assert.False(state.keys_present.gemini);
        Assert.Equal("key=[REDACTED]", state.provider_error!.detail);
    }
}
