using System;
using System.Collections.Generic;
using System.Linq;

namespace ValheimAIHelper;

public sealed class LeaderIdentity
{
    public long Id;
    public string Name = "";
}

public static class LeaderSelection
{
    public static long? Resolve(bool localMode, long? local, long? selected, string name, IEnumerable<LeaderIdentity> humans)
    {
        var candidates = humans.ToArray();
        if (localMode) return candidates.Any(p => p.Id == local) ? local : null;
        // A disconnected selected identity must not bind to a different player
        // who happens to have the same displayed name.
        if (selected.HasValue) return candidates.Any(p => p.Id == selected) ? selected : null;
        var matches = candidates.Where(p => string.Equals(p.Name, name, StringComparison.OrdinalIgnoreCase)).ToArray();
        return matches.Length == 1 ? matches[0].Id : (long?)null;
    }
}
