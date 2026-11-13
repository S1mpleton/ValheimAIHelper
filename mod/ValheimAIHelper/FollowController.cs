using System;

namespace ValheimAIHelper;

// A continuous native skill. The model chooses follow once; it does not supply
// short-lived leader coordinates or a fresh "move forward" every cycle.
public sealed class FollowController
{
    private bool following;
    public string Phase { get; private set; } = "idle";
    public bool Approach(float horizontalDistance, float verticalDistance, bool leaderAvailable)
    {
        if (!leaderAvailable) { following = false; Phase = "leader_missing"; return false; }
        if (float.IsNaN(horizontalDistance) || float.IsInfinity(horizontalDistance) || horizontalDistance < 0 || float.IsNaN(verticalDistance) || float.IsInfinity(verticalDistance))
        { following = false; Phase = "invalid_target"; return false; }
        bool sameFloor = Math.Abs(verticalDistance) <= 1.2f;
        if (sameFloor && horizontalDistance <= 2.5f) following = false;
        else if (!sameFloor || horizontalDistance >= 4f) following = true;
        Phase = following ? "approach_leader" : "hold_distance";
        return following;
    }
    public void Reset() { following = false; Phase = "idle"; }
}

public sealed class NavigationGate
{
    private float next, lastAttempt = float.NegativeInfinity;
    public void Reset() { next = 0; lastAttempt = float.NegativeInfinity; }
    public bool Due(float now, bool targetMoved) => now >= next || targetMoved && now - lastAttempt >= .4f;
    public void Attempted(float now) { lastAttempt = now; next = now + .9f; }
    // An empty/pending path cannot bypass the next scheduled query.
    public void PathConsumed(float now) { next = Math.Min(next, Math.Max(lastAttempt + .4f, now + .15f)); }
}
