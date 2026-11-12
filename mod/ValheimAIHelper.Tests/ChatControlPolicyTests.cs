using ValheimAIHelper;
using Xunit;

public class ChatControlPolicyTests
{
    [Theory]
    [InlineData(false,10,10,100,100,true)]
    [InlineData(false,10,20,200,200,false)]
    [InlineData(false,10,10,100,200,false)]
    [InlineData(true,10,20,200,200,true)]
    [InlineData(true,10,20,200,100,false)]
    [InlineData(true,10,0,200,200,false)]
    [InlineData(true,10,20,0,0,false)]
    public void ControlScopeAlwaysChecksRealNetworkIdentity(bool everyone,long leader,long speaker,long owner,long sender,bool allowed)
    { Assert.Equal(allowed,ChatControlPolicy.Allows(everyone,leader,speaker,owner,sender)); }
    [Fact] public void OnlyLeaderModeRequiresASelectedLeader()
    { Assert.False(ChatControlPolicy.Allows(false,null,20,200,200)); Assert.True(ChatControlPolicy.Allows(true,null,20,200,200)); }
    [Fact] public void ClientOnlyIdentityNeverAppearsInJson()
    { var json=WireJson.Serialize(new LeaderChatRequest { speaker="Викинг", SenderPlayerId=42, SenderOwner=123 }); Assert.DoesNotContain("Sender",json); }
}
