using ValheimAIHelper;
using Xunit;

public class AiProvidersTests
{
    [Fact] public void ProviderCycleIncludesInstalledOllamaOption()
    { Assert.Equal("ollama",AiProviders.Next("gemini")); Assert.Equal("openai",AiProviders.Next("ollama")); Assert.True(AiProviders.Known("ollama")); }
    [Fact] public void LocalInferenceDoesNotRequireCloudCredentials()
    { Assert.False(AiProviders.RequiresKey("ollama")); Assert.True(AiProviders.RequiresKey("gemini")); Assert.True(AiProviders.RequiresKey("openai")); }
    [Fact] public void SlowLocalCheckHasTransportHeadroom()
    { Assert.True(AiProviders.UiTimeout("ollama")>300); Assert.True(AiProviders.UiTimeout("openai")>100); }
    [Fact] public void NativeOllamaSettingsPreserveTagAndLocalAddress()
    {
        var options=new ProviderOptions {provider="ollama",model="example/agent:Q4_K_M",ollama_url="http://127.0.0.1:11445"};
        var decoded=WireJson.Deserialize<ProviderOptions>(WireJson.Serialize(options));
        Assert.Equal(options.model,decoded.model); Assert.Equal(options.ollama_url,decoded.ollama_url); Assert.Equal("",decoded.api_key);
    }
}
