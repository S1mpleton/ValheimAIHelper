using System;
using System.Linq;
using ValheimAIHelper;
using Xunit;

public class Follow071Tests
{
    private static readonly LeaderIdentity[] Humans = {
        new() { Id=10, Name="Гоша" }, new() { Id=20, Name="Бъёрн" }
    };
    [Fact] public void SingleClientDefaultIgnoresLegacySecondClientLeaderName()
    {
        Assert.Equal(10L,LeaderSelection.Resolve(true,10,null,"Отсутствующий Бъёрн",Humans.Take(1)));
        Assert.Null(LeaderSelection.Resolve(false,10,20,"Бъёрн",Humans.Take(1)));
    }
    [Fact] public void ExplicitLeaderIsNotReboundToAnImpersonatingDisplayName()
    {
        Assert.Equal(20L,LeaderSelection.Resolve(false,10,20,"Бъёрн",Humans));
        Assert.Null(LeaderSelection.Resolve(false,10,30,"Бъёрн",Humans));
        Assert.Null(LeaderSelection.Resolve(false,10,null,"Гоша",new[] { new LeaderIdentity { Id=1,Name="Гоша" },new LeaderIdentity {Id=2,Name="Гоша"} }));
    }
    [Fact] public void ContinuousFollowUsesDistanceHysteresisAndResumesForMovingHuman()
    {
        var follow=new FollowController();
        Assert.False(follow.Approach(3.8f,0,true));
        Assert.True(follow.Approach(4.1f,0,true));
        Assert.True(follow.Approach(3.1f,0,true));
        Assert.False(follow.Approach(2.4f,0,true));
        for(int i=0;i<200;i++) Assert.False(follow.Approach(2.5f+(i%2)*.4f,0,true));
        Assert.Equal("hold_distance",follow.Phase);
        Assert.True(follow.Approach(8,0,true));
    }
    [Fact] public void OtherFloorsAndMissingOrInvalidLeadersDoNotBecomeSuccessfulArrival()
    {
        var follow=new FollowController();
        Assert.True(follow.Approach(.1f,3,true));
        Assert.False(follow.Approach(0,0,false)); Assert.Equal("leader_missing",follow.Phase);
        Assert.False(follow.Approach(float.NaN,0,true));
        Assert.False(follow.Approach(4,float.PositiveInfinity,true));
    }
    [Fact] public void PendingEmptyNavigationCannotTurnIntoAQueryEveryFixedTick()
    {
        var gate=new NavigationGate();int queries=0;
        for(int frame=0;frame<250;frame++)
        {
            float now=frame*.02f;
            if(gate.Due(now,false)) { queries++;gate.Attempted(now); }
        }
        Assert.InRange(queries,5,6); // five seconds, rather than 125/250 queries
    }
    [Fact] public void MovingTargetAndRepeatedInvalidWaypointsCannotBypassOrPostponeQueryDeadlineForever()
    {
        var gate=new NavigationGate();gate.Attempted(0);
        for(int i=1;i<20;i++) { Assert.False(gate.Due(i*.02f,true));gate.PathConsumed(i*.02f); }
        Assert.True(gate.Due(.41f,true));
        gate.Attempted(.41f);
        Assert.False(gate.Due(.6f,true)); Assert.True(gate.Due(.82f,true));
    }
    [Fact] public void FailedTaskDoesNotDiscardCurrentDefenceOrRetreatButStopAndLostLeaseWin()
    {
        Assert.True(MotorPolicy.Cancel(true,false,"blocked"));
        Assert.False(MotorPolicy.Cancel(true,true,"blocked"));
        Assert.False(MotorPolicy.Cancel(true,true,"success"));
        Assert.False(MotorPolicy.Cancel(true,false,"running"));
        Assert.True(MotorPolicy.Cancel(false,true,"blocked"));
        Assert.True(MotorPolicy.Cancel(false,true,"running"));
    }
    private sealed class NativeTickTarget
    {
        public float Total;
        private void UpdateStats(float dt) { Total+=dt; }
        private void UpdateStats() { throw new InvalidOperationException("Local player's profile method must not run"); }
    }
    [Fact] public void NativeTickBindingSelectsPrivateFloatOverloadOnceAndPreservesItsInstance()
    {
        var a=new NativeTickTarget();var b=new NativeTickTarget();
        var update=NativeMethods.BindFloat(a,"UpdateStats");
        for(int i=0;i<1000;i++) update(.02f);
        Assert.InRange(a.Total,19.99f,20.01f); Assert.Equal(0,b.Total);
    }
}

public class UiHotPath071Tests
{
    private class BaseUi { private readonly int hidden=42; public int Hidden()=>hidden; }
    private sealed class FakeUi:BaseUi
    {
        private string text="";
        public int Rebuilds;
        public string Text { get=>text; set { text=value;Rebuilds++; } }
    }
    [Fact] public void ThousandsOfHoverRefreshesResolveUiTypeOnceAndDoNotRebuildUnchangedCaption()
    {
        string name="FakeUi-"+Guid.NewGuid(); int scans=0;
        Type? Resolve(string _) { scans++;return typeof(FakeUi); }
        var ui=new FakeUi();
        for(int i=0;i<10000;i++)
        {
            Assert.Equal(typeof(FakeUi),UiReflection.Resolve(name,Resolve));
            UiReflection.Write(ui,"Text","Управление персонажем");
        }
        Assert.Equal(1,scans); Assert.Equal(1,ui.Rebuilds);
        Assert.True(UiReflection.Write(ui,"Text","Другой текст")); Assert.Equal(2,ui.Rebuilds);
        Assert.Equal(42,UiReflection.Field(typeof(FakeUi),"hidden")!.GetValue(ui));
    }
    [Fact] public void EarlyMissingAssemblyIsRetriedWhenUiLoads()
    {
        string name="LateUi-"+Guid.NewGuid();
        Assert.Null(UiReflection.Resolve(name,_=>null));
        Assert.Equal(typeof(FakeUi),UiReflection.Resolve(name,_=>typeof(FakeUi)));
    }
}

public class Resource071Tests
{
    [Theory]
    [InlineData("Destructible",true,false,true,false)]
    [InlineData("Destructible",false,false,false,false)]
    [InlineData("Destructible",false,false,true,true)]
    [InlineData("TreeBase",false,false,false,true)]
    [InlineData("MineRock5",false,false,false,true)]
    [InlineData("TreeBase",false,true,false,false)]
    public void PlantsAndDroppedObjectsCannotMasqueradeAsMiningTargets(string component,bool plant,bool drop,bool tree,bool resource)
    { Assert.Equal(resource,ResourcePolicy.IsHarvestResource(component,plant,drop,tree)); }
    [Fact] public void GatheringRequiresTheObservedAdequateToolWhilePickupDoesNot()
    {
        var ore=new Entity { required_tool="pickaxe",tool_tier=1 };
        var tool=new InventoryEntry { id="tool",count=1,equipable=true,skill="Pickaxes",durability=1,tool_tier=1 };
        Assert.False(ResourcePolicy.CanGather(ore,Array.Empty<InventoryEntry>()));
        Assert.True(ResourcePolicy.CanGather(ore,new[] {tool}));
        tool.durability=0; Assert.False(ResourcePolicy.CanGather(ore,new[] {tool}));
        var world=new Snapshot { sequence=1, supported_actions=new[] {"pickup"}, entities=new[] {new Entity {id="stone",kind="item",distance=1,position=new Position {x=1},interaction="pickup"}} };
        var pickup=new Decision {protocol_version=1,sequence=1,action="pickup",target_id="stone",duration=3};
        Assert.True(DecisionPolicy.Accept(pickup,world,false));
    }
}

public class WorkIdentity071Tests
{
    [Fact] public void LeaseAndStatusRenewalPreserveWorkButNewOperatorRevisionRestartsIt()
    {
        var a=new Decision { action="follow",target_id="leader",plan_id="",command_revision=3,duration=1,say="Иду" };
        var b=new Decision { action="follow",target_id="leader",plan_id="",command_revision=3,duration=3,say="Скоро буду" };
        Assert.True(WorkIdentity.Same(a,b));
        b.command_revision=4;Assert.False(WorkIdentity.Same(a,b));
    }
    [Fact] public void DifferentAmountsAndDestinationsCannotSilentlyReuseAnOldReservationOrPath()
    {
        var a=new Decision {action="give",target_id="leader",item_id="stones",amount=3};
        var b=new Decision {action="give",target_id="leader",item_id="stones",amount=5};
        Assert.False(WorkIdentity.Same(a,b));
        a.action=b.action="move";a.amount=b.amount=0;
        a.destination=new Position {x=1};b.destination=new Position {x=5};
        Assert.False(WorkIdentity.Same(a,b));
    }
}
