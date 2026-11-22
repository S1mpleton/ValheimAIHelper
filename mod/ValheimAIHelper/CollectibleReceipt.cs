namespace ValheimAIHelper;

public enum CollectibleResult { Waiting, Confirmed, TimedOut }

// A native pick RPC may complete on a later frame or after an ownership round
// trip. Renewing the same executor lease must not issue that RPC repeatedly.
public sealed class CollectibleReceipt
{
    private float issuedAt;
    public bool Issued { get; private set; }
    public bool TryIssue(float now)
    {
        if (Issued) return false;
        Issued = true; issuedAt = now; return true;
    }
    public CollectibleResult Observe(float now, bool confirmed) => confirmed ? CollectibleResult.Confirmed :
        Issued && now - issuedAt >= 6 ? CollectibleResult.TimedOut : CollectibleResult.Waiting;
    public void Reset() { Issued = false; issuedAt = 0; }
}
