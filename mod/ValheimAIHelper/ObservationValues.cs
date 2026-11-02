using System;

namespace ValheimAIHelper;

public static class ObservationValues
{
    public static float Vital(float value) => float.IsNaN(value) || float.IsInfinity(value) ? 0 : Math.Max(0, value);
    public static string Text(string? value, int length)
    {
        value = value ?? "";
        return value.Length <= length ? value : value.Substring(0, length);
    }

    public static bool Finite(float value) => !float.IsNaN(value) && !float.IsInfinity(value);
}
