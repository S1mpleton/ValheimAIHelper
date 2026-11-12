using ValheimAIHelper;
using Xunit;

public class Inventory060Tests
{
    private static Snapshot World() => new()
    {
        sequence = 3, supported_actions = Capabilities.All,
        actor = new Actor { position = new Position(), inventory = new[] { new InventoryEntry { id = "stones", count = 12 } } },
        entities = new[] {
            new Entity { id = "human", kind = "player", distance = 40, position = new Position { x = 40 } },
            new Entity { id = "drop", kind = "item", count = 4, pickup_allowed = false, distance = 90, position = new Position { x = 90 } },
        }
    };
    private static Decision Give(string target = "human", string item = "stones", int count = 5) => new()
    { protocol_version = 1, sequence = 3, action = "give", target_id = target, item_id = item, amount = count, duration = 3, say = "Передам камни" };

    [Fact] public void DeliveryCanApproachObservedPlayerWithExactAvailableAmount() => Assert.True(DecisionPolicy.Accept(Give(), World(), false));
    [Theory]
    [InlineData("drop", "stones", 5)]
    [InlineData("human", "drop", 5)]
    [InlineData("human", "stones", 13)]
    [InlineData("human", "stones", 0)]
    [InlineData("missing", "stones", 1)]
    public void WrongRecipientOrInventedStockCannotBeDelivered(string target, string item, int count) => Assert.False(DecisionPolicy.Accept(Give(target,item,count),World(),false));
    [Fact] public void DeliveryRevalidatesMovingRecipientAndChangingInventory()
    {
        var world=World(); world.entities[0].position.x=61;
        Assert.False(DecisionPolicy.Accept(Give(),world,false));
        world.entities[0].position.x=40;world.actor.inventory[0].count=4;
        Assert.False(DecisionPolicy.Accept(Give(),world,false));
    }
    [Fact] public void DistantLoadedDropCanBeApproachedAndOwnershipRequestedByNativeMotor()
    {
        var world=World();var action=Give("drop");action.action="pickup";
        Assert.True(DecisionPolicy.Accept(action,world,false));
        world.entities[1].position.x=129;
        Assert.False(DecisionPolicy.Accept(action,world,false));
    }
    [Fact] public void TimingWireSeparatesRunningRequestAndLastValidatedResponse()
    {
        var status=WireJson.Deserialize<BridgeUiStatus>("{\"performance\":{\"current\":{\"model\":\"qwen3:1.7b\",\"elapsed_seconds\":17.5},\"last\":{\"provider\":\"ollama\",\"model\":\"qwen3:1.7b\",\"status\":\"error\",\"request_seconds\":12.3,\"tokens_per_second\":8.5,\"load_seconds\":1.2,\"prompt_seconds\":2,\"generation_seconds\":9,\"prompt_tokens\":1000,\"output_tokens\":80}}}");
        Assert.Contains("17.5 с",AiPerformanceText.Summary(status.performance));
        Assert.Contains("Загрузка 1.2 с",AiPerformanceText.Details(status.performance));
        status.performance!.current=null;
        Assert.Contains("8.5 ток/с",AiPerformanceText.Summary(status.performance));
        Assert.Contains("ошибка",AiPerformanceText.Summary(status.performance));
        Assert.DoesNotContain("план проверен",AiPerformanceText.Summary(status.performance));
    }
}
