using System;
using System.Linq;

namespace ValheimAIHelper;

public static class DecisionPolicy
{
    public static bool Accept(Decision? d, Snapshot s, bool allowCombat)
    {
        if (d == null || d.protocol_version != 1 || d.sequence != s.sequence ||
            float.IsNaN(d.duration) || float.IsInfinity(d.duration) ||
            d.duration < .25f || d.duration > 3f || d.say == null || d.say.Length > 240 ||
            d.target_id == null || d.target_id.Length > 100)
            return false;
        var entity = s.entities.FirstOrDefault(e => e.id == d.target_id);
        var item = s.actor.inventory.FirstOrDefault(i => i.id == d.target_id);
        if (d.action != "wait" && !s.supported_actions.Contains(d.action)) return false;
        switch (d.action)
        {
            case "wait": return d.target_id == "";
            case "move": return d.target_id == "";
            case "follow": return entity?.kind == "player" && entity.id == s.leader_id;
            case "attack": return allowCombat && entity?.kind == "enemy" && InRange(s.actor.position, entity);
            case "pickup": return entity?.kind == "item" && InRange(s.actor.position, entity);
            case "eat": return item?.food == true;
            case "equip": return item?.equipable == true;
            default: return false;
        }
    }

    private static bool InRange(Position from, Entity target)
    {
        double x = from.x - target.position.x, y = from.y - target.position.y, z = from.z - target.position.z;
        double square = x * x + y * y + z * z;
        return !double.IsNaN(square) && square <= 400 && target.distance >= 0 && target.distance <= 20;
    }
}
