using System;

namespace ValheimAIHelper;

public static class NavigationPolicy
{
    public static bool Reached(float horizontal, float vertical, float tolerance) =>
        horizontal >= 0 && horizontal <= tolerance && Math.Abs(vertical) <= 1.2f;
    public static bool UnsafeSegment(float horizontal, float vertical) =>
        Math.Abs(vertical) > 1.2f && (horizontal < .5f || Math.Abs(vertical) / horizontal > 1.2f);
    public static bool Retry(float elapsed, int attempts) => elapsed < 8 && attempts < 8;
}
