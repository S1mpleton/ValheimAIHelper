using ValheimAIHelper;
using Xunit;

public class DialogueTests
{
    [Fact] public void DialogueMetadataKeepsPurposeAndCancelledRequestsSeparate()
    {
        var index=WireJson.Deserialize<DialogueIndex>("{\"instance_id\":\"service-2\",\"revision\":15,\"entries\":[{\"id\":9,\"time\":\"12:34:56\",\"provider\":\"ollama\",\"model\":\"qwen3:1.7b\",\"purpose\":\"chat\",\"status\":\"cancelled\",\"seconds\":12.5,\"truncated\":true}]}");
        Assert.Equal("service-2",index.instance_id);Assert.Equal(9,index.entries[0].id);
        Assert.Equal("chat",index.entries[0].purpose);Assert.Equal("cancelled",index.entries[0].status);
        Assert.Equal(12.5,index.entries[0].seconds);Assert.True(index.entries[0].truncated);
    }
    [Fact] public void RequestPagePreservesQuotedJsonCyrillicAndLineBreaks()
    {
        var page=WireJson.Deserialize<DialoguePage>("{\"id\":9,\"part\":\"request\",\"page\":2,\"pages\":40,\"text\":\"{\\\"name\\\":\\\"Камень\\\"}\\nНовая строка\",\"status\":\"ok\"}");
        Assert.Equal(2,page.page);Assert.Equal(40,page.pages);
        Assert.Equal("{\"name\":\"Камень\"}\nНовая строка",page.text);
    }
    [Fact] public void ExportUsesServerOffsetsAndSupportsEmptyClearRequest()
    {
        var chunk=WireJson.Deserialize<DialogueExport>("{\"id\":9,\"offset\":16000,\"next_offset\":16002,\"text\":\"🙂\\n\",\"complete\":true}");
        Assert.Equal(16002,chunk.next_offset);Assert.Equal("🙂\n",chunk.text);Assert.True(chunk.complete);
        Assert.Equal("{}",WireJson.Serialize(new BridgeEmpty()));
    }
}
