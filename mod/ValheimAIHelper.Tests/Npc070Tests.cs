using System.Linq;
using ValheimAIHelper;
using Xunit;

public class Npc070Tests
{
    [Theory]
    [InlineData("следуй за мной", true)]
    [InlineData("  Следуй за мной! ", true)]
    [InlineData("пошли", false)]
    [InlineData("пойдём", false)]
    [InlineData("за мной", false)]
    [InlineData("следуй", false)]
    [InlineData("follow", false)]
    [InlineData("не следуй за мной", false)]
    [InlineData("следуй за мной и отдай вещи", false)]
    public void OnlyTheCanonicalFollowCommandBypassesInference(string text, bool direct) => Assert.Equal(direct, LeaderCommand.IsDirectFollow(text));

    [Fact] public void HumanNameIsNotTheNpcNameAndAnAddressPreservesIntent()
    {
        Assert.True(LeaderCommand.Addressed("Гоша, пошли", "Гоша", out var task));
        Assert.Equal("пошли", task); Assert.False(LeaderCommand.IsDirectFollow(task));
        Assert.False(LeaderCommand.Addressed("Бъёрн, пошли", "Гоша", out _));
    }
    [Fact] public void MotorRequiresNpcHostOwnershipAndNeverControlsTheHuman()
    {
        Assert.True(NpcAuthorityPolicy.MayControl(true, true, true, false, true));
        Assert.False(NpcAuthorityPolicy.MayControl(false, true, true, false, true));
        Assert.False(NpcAuthorityPolicy.MayControl(true, false, true, false, true));
        Assert.False(NpcAuthorityPolicy.MayControl(true, true, false, false, true));
        Assert.False(NpcAuthorityPolicy.MayControl(true, true, true, true, true));
        Assert.False(NpcAuthorityPolicy.MayControl(true, true, true, false, false));
    }
    private static InventoryEntry[] Bag() => new[] {
        new InventoryEntry { id="stone1", item_key="Stone:1:0", name="Камень", count=5 },
        new InventoryEntry { id="stone2", item_key="Stone:1:0", name="Камень", count=9 },
        new InventoryEntry { id="weapon", item_key="Club:2:0", count=1, equipped=true },
        new InventoryEntry { id="quest", item_key="Quest:1:0", count=1, quest=true }
    };
    [Fact] public void ExactDeliverySpansStacksButNeverSubstitutesOtherItems()
    {
        Assert.True(TransferPolicy.Available(Bag(), "stone1", "exact", 14));
        Assert.False(TransferPolicy.Available(Bag(), "stone1", "exact", 15));
        Assert.Equal(new[] { "stone1", "stone2" }, TransferPolicy.Candidates(Bag(), "stone1", "all_matching").Select(i=>i.id));
        Assert.False(TransferPolicy.Available(Bag(), "quest", "exact", 1));
        Assert.False(TransferPolicy.Available(Bag(), "unknown", "all_matching", 1));
        Assert.False(TransferPolicy.Available(Bag(), "stone1", "invented", 1));
    }
    [Fact] public void AllInventoryIncludesEquippedItemsOnlyOnExplicitBulkInstructionAndProtectsQuestItems()
    {
        Assert.True(TransferPolicy.Available(Bag(), "", "all_inventory", 1));
        Assert.Equal(3, TransferPolicy.Candidates(Bag(), "", "all_inventory").Length);
        Assert.Contains(TransferPolicy.Candidates(Bag(), "", "all_inventory"), i => i.id == "weapon");
        Assert.False(TransferPolicy.Available(new[] { Bag().Last() }, "", "all_inventory", 1));
        Assert.False(TransferPolicy.Available(Bag(), "", "exact", 1));
    }
    [Fact] public void SnapshotValidationAcceptsBulkButStillRequiresARealNearbyHuman()
    {
        var s = new Snapshot { sequence=1, actor=new Actor { inventory=Bag() }, supported_actions=Capabilities.NpcSupported(true),
            entities=new[] { new Entity { id="leader", kind="player", distance=3, position=new Position { x=3 } } } };
        var d = new Decision { protocol_version=1, sequence=1, action="give", target_id="leader", selection="all_inventory", amount=1, duration=3 };
        Assert.True(DecisionPolicy.Accept(d,s,true));
        d.target_id="missing"; Assert.False(DecisionPolicy.Accept(d,s,true));
        d.action="equip"; d.target_id="weapon"; Assert.False(DecisionPolicy.Accept(d,s,true));
    }
    [Fact] public void NavigationDoesNotConfuseDifferentFloorsOrGentleSlopesWithArrivalAndCliffs()
    {
        Assert.False(NavigationPolicy.Reached(.2f, 3, 2));
        Assert.True(NavigationPolicy.Reached(1.8f, .4f, 2));
        Assert.False(NavigationPolicy.UnsafeSegment(8, 3));
        Assert.True(NavigationPolicy.UnsafeSegment(.2f, 2));
        Assert.True(NavigationPolicy.Retry(2,2)); Assert.False(NavigationPolicy.Retry(9,2)); Assert.False(NavigationPolicy.Retry(2,8));
    }
    [Fact] public void NpcDoesNotAdvertiseActionsThatWouldUseTheHumanCameraOrBed()
    {
        var actions=Capabilities.NpcSupported(false);
        Assert.Contains("give",actions); Assert.Contains("craft",actions); Assert.Contains("take",actions);
        Assert.DoesNotContain("build",actions); Assert.DoesNotContain("repair_building",actions); Assert.DoesNotContain("sleep",actions); Assert.DoesNotContain("attack",actions);
    }
}

public class TransferCommitTests
{
    private sealed class Stack { public int Count; public Stack(int count) { Count=count; } }
    [Fact] public void ChangedInventoryCancelsWholeReservationBeforeAnyDrop()
    {
        var a=new Stack(3);var b=new Stack(1);int calls=0;
        var batch=new TransferBatch<Stack>(new[] { (a,3),(b,2) });
        Assert.False(batch.Commit(s=>s.Count,()=>true,(s,n)=>{ calls++;return n; }));
        Assert.Equal(0,calls); Assert.Equal(0,batch.Removed);
    }
    [Fact] public void ActualDeltaAcrossStacksConfirmsTotalAndCannotCommitTwice()
    {
        var a=new Stack(3);var b=new Stack(5);
        var batch=new TransferBatch<Stack>(new[] { (a,3),(b,4) });
        Assert.True(batch.Commit(s=>s.Count,()=>true,(s,n)=>{ s.Count-=n; return n; }));
        Assert.Equal(7,batch.Removed); Assert.Equal(0,a.Count); Assert.Equal(1,b.Count);
        Assert.False(batch.Commit(s=>s.Count,()=>true,(s,n)=>throw new System.Exception("Must not retry")));
    }
    [Fact] public void MovingRecipientStopsFurtherDropsAndReportsPartialAmount()
    {
        var a=new Stack(3);var b=new Stack(5);bool near=true;
        var batch=new TransferBatch<Stack>(new[] { (a,3),(b,4) });
        Assert.False(batch.Commit(s=>s.Count,()=>near,(s,n)=>{ s.Count-=n; near=false; return n; }));
        Assert.Equal(3,batch.Removed); Assert.Equal(5,b.Count);
    }
    [Fact] public void NativePartialRejectionDoesNotRetryOrOverstateSuccess()
    {
        var a=new Stack(8);
        var batch=new TransferBatch<Stack>(new[] { (a,5) });
        Assert.False(batch.Commit(s=>s.Count,()=>true,(s,n)=>{ s.Count-=2; return 2; }));
        Assert.Equal(2,batch.Removed); Assert.Equal(6,a.Count);
        Assert.False(batch.Commit(s=>s.Count,()=>true,(s,n)=>throw new System.Exception("No retry")));
    }
}
