using System;
using ValheimAIHelper;
using Xunit;

public class NativeNotificationTests
{
    private enum MessageType { Center }
    private class OldPlayer
    {
        public string Received = "";
        protected void Message(MessageType type, string text, int amount = 0, object? icon = null)
        { Assert.Equal(0, amount); Assert.Null(icon); Received = text; }
    }
    private sealed class DerivedPlayer : OldPlayer { }
    private sealed class NewPlayer
    {
        public string Received = "";
        private void Message(MessageType type, string text, int amount = 0, object? icon = null, bool showImmediately = true)
        { Assert.Equal(0, amount); Assert.Null(icon); Assert.True(showImmediately); Received = text; }
    }
    private sealed class Hud
    {
        public string Received = "";
        public void ShowMessage(MessageType type, string text) { Received = text; }
    }
    private sealed class RequiredExtraArgument
    {
        public void Message(MessageType type, string text, int differentMeaning) => throw new Exception("must not be called");
    }
    private sealed class BrokenPlayer
    {
        public void Message(MessageType type, string text) => throw new MissingMethodException("runtime game API changed");
    }

    [Fact] public void OldInheritedSignatureReceivesItsDefaultArguments()
    {
        var player = new DerivedPlayer();
        Assert.True(NativeNotification.TryShow(player, "Message", MessageType.Center, "come closer", out var failure));
        Assert.Equal("", failure); Assert.Equal("come closer", player.Received);
    }

    [Fact] public void NewSignatureReceivesAdditionalDeclaredDefaults()
    {
        var player = new NewPlayer();
        Assert.True(NativeNotification.TryShow(player, "Message", MessageType.Center, "come closer", out _));
        Assert.Equal("come closer", player.Received);
    }

    [Fact] public void HudCanDisplayTheMessageAfterPlayerMethodFails()
    {
        Assert.False(NativeNotification.TryShow(new BrokenPlayer(), "Message", MessageType.Center, "come closer", out var failure));
        Assert.Equal("MissingMethodException", failure);
        var hud = new Hud();
        Assert.True(NativeNotification.TryShow(hud, "ShowMessage", MessageType.Center, "come closer", out _));
        Assert.Equal("come closer", hud.Received);
    }

    [Fact] public void UnrelatedRequiredParametersAreNotGuessed()
    {
        Assert.False(NativeNotification.TryShow(new RequiredExtraArgument(), "Message", MessageType.Center, "come closer", out var failure));
        Assert.Equal("MissingMethodException", failure);
    }
}
