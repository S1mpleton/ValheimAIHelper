using System;

namespace ValheimAIHelper;

internal static class UiLayout
{
    public const float Width = 860, Height = 660;
    public static float EffectiveScale(float canvasScale, float canvasTransformScale, float menuTransformScale)
    {
        float scale = canvasScale > 0 && !float.IsInfinity(canvasScale) && !float.IsNaN(canvasScale) ? canvasScale : 1;
        if (canvasTransformScale > 0 && menuTransformScale > 0)
        {
            float adjusted = scale * menuTransformScale / canvasTransformScale;
            if (!float.IsNaN(adjusted) && !float.IsInfinity(adjusted) && adjusted > 0) scale = adjusted;
        }
        return scale;
    }
    public static float Fit(float viewportWidth, float viewportHeight, float settingsWidth = 0, float settingsHeight = 0)
    {
        float fit = Math.Min(1, Math.Min((viewportWidth - 32) / Width, (viewportHeight - 32) / Height));
        if (settingsWidth >= 400 && settingsHeight >= 300)
            fit = Math.Min(fit, Math.Min(settingsWidth / Width, settingsHeight / Height));
        return Math.Max(.1f, fit);
    }
    public static float FontCeiling(float renderedSize, float configuredSize) =>
        renderedSize > 0 ? renderedSize : configuredSize > 0 ? configuredSize : 32;
}
