using System;
using System.Collections.Generic;
using System.Linq;

namespace ValheimAIHelper;

// Dropped ItemDrop stacks and generated E-pickups share a bounded observation
// budget. Keep one nearest representative of each type before filling it with
// duplicate stacks/plants; a field of berries must not hide a nearby tool.
public static class CollectiblePolicy
{
    public const int Limit = 80;
    public const int Radius = 128;

    public static bool IsPickupInteraction(Entity entity) => entity.kind == "interactable" &&
        entity.category == "pickable" && entity.produces != null && entity.produces.Length > 0;

    public static Entity[] Select(IEnumerable<Entity> rows, int limit)
    {
        if (limit <= 0) return Array.Empty<Entity>();
        var available = rows.Where(e => e != null && e.distance >= 0 && e.distance <= Radius &&
            ObservationValues.Finite(e.distance) && (e.kind == "item" || IsPickupInteraction(e)))
            .GroupBy(e => e.id, StringComparer.Ordinal).Select(g => g.First())
            .OrderBy(e => e.distance).ThenBy(e => e.id, StringComparer.Ordinal).ToArray();
        var types = available.GroupBy(e => e.item_prefab.Length > 0 ? e.item_prefab :
            e.produces.Length > 0 ? e.produces[0] : e.id, StringComparer.Ordinal)
            .Select(g => g.First()).OrderBy(e => e.distance).ThenBy(e => e.id, StringComparer.Ordinal)
            .Take(limit).ToArray();
        var selected = new HashSet<string>(types.Select(e => e.id), StringComparer.Ordinal);
        return types.Concat(available.Where(e => !selected.Contains(e.id)).Take(Math.Max(0, limit - types.Length)))
            .OrderBy(e => e.distance).ThenBy(e => e.id, StringComparer.Ordinal).ToArray();
    }
}
