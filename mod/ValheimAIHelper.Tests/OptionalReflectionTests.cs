using System;
using System.Reflection;
using ValheimAIHelper;
using Xunit;

public class OptionalReflectionTests
{
    private class OldNativeObject
    {
        private readonly bool m_picked = false;
        public bool Picked() => m_picked;
    }
    private sealed class NewNativeObject : OldNativeObject
    {
        public string? MissingValue => null;
        public string Caption => "NPC";
        public bool Broken => throw new InvalidOperationException("real native failure");
    }

    [Fact] public void RepeatedMissingVersionSpecificMembersFallBackToInheritedFieldsAndProperties()
    {
        var target = new NewNativeObject();
        for (int i = 0; i < 10000; i++)
        {
            Assert.Null(UiReflection.Read(target, "menuEntriesParent", "m_settingsInstance", "saveButton", "m_minToolTier"));
            Assert.Equal(false, UiReflection.Read(target, "m_picked"));
            Assert.Equal("NPC", UiReflection.Read(target, "MissingValue", "Caption"));
        }
        Assert.Null(UiReflection.Read(null, "m_picked"));
    }

    [Fact] public void MissingOptionalMembersDoNotHideRealGetterErrors()
    {
        var error = Assert.Throws<TargetInvocationException>(() => UiReflection.Read(new NewNativeObject(), "Broken"));
        Assert.IsType<InvalidOperationException>(error.InnerException);
        Assert.Equal("real native failure", error.InnerException!.Message);
    }
}
