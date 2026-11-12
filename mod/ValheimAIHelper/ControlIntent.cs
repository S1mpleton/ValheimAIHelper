using System;

namespace ValheimAIHelper;

// A bounded horizontal input request, independent of Unity/physics/network objects.
public readonly struct ControlIntent
{
    public readonly float X, Z, Magnitude;
    public readonly bool Attack;
    public readonly bool AttackHold, Block, Jump, Run, Dodge;
    public readonly bool Secondary, Crouch;
    public readonly float AimX, AimY, AimZ;
    public readonly bool HasAim;

    private ControlIntent(float x, float z, float magnitude, bool attack)
    { X = x; Z = z; Magnitude = magnitude; Attack = attack;
      AttackHold = Block = Jump = Run = Dodge = HasAim = Secondary = Crouch = false; AimX = AimY = AimZ = 0; }

    private ControlIntent(ControlIntent move, float x, float y, float z, bool hold, bool block, bool jump, bool run, bool dodge, bool secondary, bool crouch)
    {
        X = move.X; Z = move.Z; Magnitude = move.Magnitude; Attack = move.Attack;
        AimX = x; AimY = y; AimZ = z; HasAim = x*x + y*y + z*z > .0001f;
        AttackHold = hold; Block = block; Jump = jump; Run = run; Dodge = dodge;
        Secondary = secondary; Crouch = crouch;
    }

    public ControlIntent With(float aimX = 0, float aimY = 0, float aimZ = 0, bool hold = false,
        bool block = false, bool jump = false, bool run = false, bool dodge = false, bool secondary = false, bool crouch = false)
    {
        if (!Finite(aimX) || !Finite(aimY) || !Finite(aimZ)) return default;
        double length = Math.Sqrt((double)aimX*aimX + (double)aimY*aimY + (double)aimZ*aimZ);
        float scale = length > .0001 ? (float)(1/length) : 0;
        return new ControlIntent(this, aimX*scale, aimY*scale, aimZ*scale, hold, block, jump, run, dodge, secondary, crouch);
    }

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
