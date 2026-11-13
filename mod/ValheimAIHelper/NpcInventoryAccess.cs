namespace ValheimAIHelper;

public enum NpcInventoryAccessResult { Allowed, Unavailable, InvalidNpc, LoadingNpc, HostOnly, InvalidHuman, UiUnavailable, TooFar }

// Shared by initial opening and session validation. A small hysteresis allows
// normal movement at the chest boundary without opening/closing on every tick.
public static class NpcInventoryAccess
{
    public const double OpenDistance = 3.5, CloseDistance = 4;
    public static NpcInventoryAccessResult Check(bool initialized, bool validNpc, bool ready,
        bool worldHost, bool npcOwned, bool validHuman, bool humanOwned, bool guiReady,
        double distance, bool existingSession)
    {
        if (!initialized) return NpcInventoryAccessResult.Unavailable;
        if (!validNpc) return NpcInventoryAccessResult.InvalidNpc;
        if (!worldHost || !npcOwned) return NpcInventoryAccessResult.HostOnly;
        if (!ready) return NpcInventoryAccessResult.LoadingNpc;
        if (!validHuman || !humanOwned) return NpcInventoryAccessResult.InvalidHuman;
        if (!guiReady) return NpcInventoryAccessResult.UiUnavailable;
        if (double.IsNaN(distance) || double.IsInfinity(distance) || distance < 0 ||
            distance > (existingSession ? CloseDistance : OpenDistance)) return NpcInventoryAccessResult.TooFar;
        return NpcInventoryAccessResult.Allowed;
    }
}
