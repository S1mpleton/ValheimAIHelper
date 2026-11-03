using System;

namespace ValheimAIHelper;

// A bounded horizontal input request, independent of Unity/physics/network objects.
public readonly struct ControlIntent
{
    public readonly float X, Z, Magnitude;
    public readonly bool Attack;

    private ControlIntent(float x, float z, float magnitude, bool attack)
    { X = x; Z = z; Magnitude = magnitude; Attack = attack; }

    public static ControlIntent Create(float x, float y, float z, bool attack)
    {
        if (!Finite(x) || !Finite(y) || !Finite(z)) return default;
        double length = Math.Sqrt((double)x * x + (double)z * z);
        if (length < .001) return new ControlIntent(0, 0, 0, attack);
        float magnitude = (float)Math.Min(length, 1);
        return new ControlIntent((float)(x / length), (float)(z / length), magnitude, attack);
    }

    private static bool Finite(float v) => !float.IsNaN(v) && !float.IsInfinity(v);
}
