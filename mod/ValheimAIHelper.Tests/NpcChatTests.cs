using System;
using System.Linq;
using System.Threading;
using Xunit;

namespace ValheimAIHelper.Tests;

public class NpcChatTests
{
    private enum TalkType { Normal }
    private enum RelationsManagerPermissionResult { Granted }
    private sealed class User
    {
        public string Name = "Чюбрик";
        public object UserId = new();
    }
    private sealed class Chat
    {
        public string Name = "", Text = "";
        public bool Timestamp;
        public void AddString(string title, string text, TalkType type, bool timestamp)
        { Name = title; Text = text; Timestamp = timestamp; }
        private sealed class PermissionFrame
        {
            public readonly object go = new(), sender = new();
            private void Complete(RelationsManagerPermissionResult result) { }
            private void Unrelated(int number) { }
        }
        private sealed class UnrelatedFrame
        {
            public readonly object sender = new();
            private void Complete(RelationsManagerPermissionResult result) { }
        }
    }

    [Fact]
    public void OutgoingUserKeepsAuthenticAccountButGetsNpcNameWithoutMutatingHuman()
    {
        var human = new User();
        var npc = (User)NpcChatContext.NamedUser(human, "Гоша");
        Assert.NotSame(human, npc);
        Assert.Same(human.UserId, npc.UserId);
        Assert.Equal("Чюбрик", human.Name);
        Assert.Equal("Гоша", npc.Name);
    }

    [Fact]
    public void LocalAndRemoteNpcCallbacksRenderNpcNameOnlyInsideTheirScope()
    {
        foreach (bool delayedRemoteCallback in new[] { false, true })
        {
            var chat = new Chat();
            // Both synchronous local delivery and a later permission callback
            // enter their own NPC scope. No pending text matching is involved.
            using (NpcChatContext.Enter("Гоша"))
            {
                Assert.True(NpcChatContext.TryNamedLine(chat, chat, "Отдаю камень", TalkType.Normal, delayedRemoteCallback));
                Assert.Equal("Гоша", chat.Name);
                Assert.Equal("Отдаю камень", chat.Text);
                Assert.Equal(delayedRemoteCallback, chat.Timestamp);
            }
            Assert.False(NpcChatContext.TryNamedLine(chat, chat, "Отдаю камень", TalkType.Normal, false));
        }
    }

    [Fact]
    public void HumanReentrancyAndConsoleDoNotInheritNpcNameEvenForIdenticalText()
    {
        var chat = new Chat();
        using (NpcChatContext.Enter("Гоша"))
        {
            Assert.False(NpcChatContext.TryNamedLine(new Chat(), chat, "Привет", TalkType.Normal, false));
            using (NpcChatContext.Enter(null))
                Assert.False(NpcChatContext.TryNamedLine(chat, chat, "Привет", TalkType.Normal, false));
            Assert.True(NpcChatContext.TryNamedLine(chat, chat, "Привет", TalkType.Normal, false));
        }
        Assert.Null(NpcChatContext.Name);
    }

    [Fact]
    public void CallbackFailureRestoresPreviousContext()
    {
        using (NpcChatContext.Enter("Предыдущий NPC"))
        {
            Assert.Throws<InvalidOperationException>((Action)(() =>
            {
                using var scope = NpcChatContext.Enter("Гоша");
                throw new InvalidOperationException();
            }));
            Assert.Equal("Предыдущий NPC", NpcChatContext.Name);
        }
        Assert.Null(NpcChatContext.Name);
    }

    [Fact]
    public void CallbackContextDoesNotLeakIntoAnotherThread()
    {
        string? observed = "unexpected";
        using (NpcChatContext.Enter("Гоша"))
        {
            var thread = new Thread(() => { observed = NpcChatContext.Name; });
            thread.Start(); thread.Join();
            Assert.Null(observed);
            Assert.Equal("Гоша", NpcChatContext.Name);
        }
    }

    [Fact]
    public void BubbleIdentityBelongsToNpcAndDoesNotMergeWithHostOrAnotherNpc()
    {
        long first = NpcChatContext.SpeakerId("1:22480", 123);
        Assert.Equal(first, NpcChatContext.SpeakerId("1:22480", 123));
        Assert.NotEqual(123, first);
        Assert.NotEqual(first, NpcChatContext.SpeakerId("1:22481", 123));
        Assert.NotEqual(first, NpcChatContext.SpeakerId("1:22480", first));
    }

    [Fact]
    public void OnlyActualChatPermissionFramesAreScoped()
    {
        var callback = Assert.Single(NpcChatContext.PermissionCallbacks(typeof(Chat)));
        Assert.Equal("Complete", callback.Name);
        Assert.Equal("PermissionFrame", callback.DeclaringType!.Name);
    }

    [Fact]
    public void NpcNameCannotInsertRichTextOrAdditionalChatLines()
    {
        string cleaned = NpcChatContext.CleanName("<color=red>Гоша</color>\nИгрок:");
        Assert.DoesNotContain('<', cleaned);
        Assert.DoesNotContain('>', cleaned);
        Assert.DoesNotContain('\n', cleaned);
        Assert.Equal("NPC", NpcChatContext.CleanName("  "));
    }
}
