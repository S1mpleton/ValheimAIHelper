using ValheimAIHelper;
using Xunit;

public class UiLayoutTests
{
    [Fact] public void NativeCanvasScalingChangesWindowPixels()
    {
        float normal = UiLayout.Width * UiLayout.Fit(1920,1080);
        float larger = 1.2f * UiLayout.Width * UiLayout.Fit(1920/1.2f,1080/1.2f);
        Assert.True(larger > normal);
        Assert.InRange(larger/normal,1.19f,1.21f);
    }
    [Fact] public void WindowFitsNativeSettingsAndSmallScreens()
    {
        float fit = UiLayout.Fit(1920,1080,720,600);
        Assert.True(UiLayout.Width*fit <= 720.01f);
        Assert.True(UiLayout.Height*fit <= 600.01f);
        fit = UiLayout.Fit(640,480);
        Assert.True(UiLayout.Width*fit <= 608.01f);
        Assert.True(UiLayout.Height*fit <= 448.01f);
    }
    [Fact] public void LongEscCaptionCannotGrowBeyondNativeFont()
    { Assert.Equal(32,UiLayout.FontCeiling(32,300)); }
    [Fact] public void GuiFrameworkWrapperScaleIsIncludedWithoutDoubleCountingCanvas()
    {
        Assert.Equal(.9f,UiLayout.EffectiveScale(.75f,.75f,.9f),3);
        Assert.Equal(.9f,UiLayout.EffectiveScale(.75f,1,1.2f),3);
        Assert.Equal(.75f,UiLayout.EffectiveScale(.75f,0,1.2f));
    }
}
