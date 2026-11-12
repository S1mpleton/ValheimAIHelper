using System;
using ValheimAIHelper;
using Xunit;

public class NativeMethodsTests
{
    private class RuntimeHumanoid
    {
        protected string GetRightItem() => "hammer";
        private string Hidden(bool value) => value ? "yes" : "no";
        protected void Fill(out float number) { number = 42; }
        protected void Fail() => throw new InvalidOperationException("native failure");
    }
    private sealed class RuntimePlayer : RuntimeHumanoid { }
    [Fact] public void RealProtectedHeldItemCanBeReadOnDerivedPlayer()
    { Assert.Equal("hammer", NativeMethods.Call(new RuntimePlayer(), "GetRightItem")); }
    [Fact] public void PrivateBaseMembersAndArgumentsAreResolved()
    { Assert.Equal("yes", NativeMethods.Call(new RuntimePlayer(), "Hidden", true)); }
    [Fact] public void OutParametersArePreserved()
    { object?[] args = { 0f }; NativeMethods.Call(new RuntimePlayer(), "Fill", args); Assert.Equal(42f,args[0]); }
    [Fact] public void ReportContainsActualNativeFailure()
    { Assert.Equal("native failure", Assert.Throws<InvalidOperationException>(() => NativeMethods.Call(new RuntimePlayer(), "Fail")).Message); }
    [Fact] public void MissingMemberHasItsActualTypeAndMethod()
    { Assert.Contains("Missing", Assert.Throws<MissingMethodException>(() => NativeMethods.Call(new RuntimePlayer(), "Missing")).Message); }
}
