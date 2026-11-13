using System;
using System.Collections.Generic;
using System.Linq;

namespace ValheimAIHelper;

// Reservation and measured commit are independent of Unity. A batch is committed
// at most once; neither a repeated network lease nor a failed stack retries it.
public sealed class TransferBatch<T> where T : class
{
    private readonly List<(T item, int count)> entries;
    private bool committed;
    public int Removed { get; private set; }
    public int StackCount { get; private set; }
    public TransferBatch(IEnumerable<(T item, int count)> entries) { this.entries = entries.ToList(); }
    public bool Commit(Func<T, int> available, Func<bool> inRange, Func<T, int, int> drop)
    {
        if (committed) return false;
        committed = true;
        if (entries.Count == 0 || !inRange() || entries.Any(e => e.count < 1 || available(e.item) < e.count)) return false;
        foreach (var entry in entries)
        {
            if (!inRange()) return false;
            int removed = drop(entry.item, entry.count);
            Removed += Math.Max(0, removed); StackCount++;
            if (removed != entry.count) return false;
        }
        return true;
    }
}
