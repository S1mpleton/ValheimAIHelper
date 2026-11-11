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
            d.target_id == null || d.target_id.Length > 100 || (d.plan_id != null && d.plan_id.Length > 80))
            return false;
        if (d.destination != null)
        {
            var p = d.destination;
            double x = p.x - s.actor.position.x, y = p.y - s.actor.position.y, z = p.z - s.actor.position.z;
            if ((d.action != "move" && d.action != "jump") || !ObservationValues.Finite(p.x) ||
                !ObservationValues.Finite(p.y) || !ObservationValues.Finite(p.z) || x*x + y*y + z*z > 900 || Math.Abs(y) > 8)
                return false;
        }
        var entity = s.entities.FirstOrDefault(e => e.id == d.target_id);
        var item = s.actor.inventory.FirstOrDefault(i => i.id == d.target_id);
        if (d.action != "wait" && !s.supported_actions.Contains(d.action)) return false;
        switch (d.action)
        {
            case "wait": return d.target_id == "";
            case "move": case "jump": return d.target_id == "";
            case "follow": return entity?.kind == "player" && entity.id == s.leader_id;
            case "attack": return allowCombat && entity?.kind == "enemy" && InRange(s.actor.position, entity);
            case "defend": case "flee": case "dodge": return entity?.kind == "enemy" && InRange(s.actor.position, entity);
            case "pickup": return entity?.kind == "item" && InRange(s.actor.position, entity);
            case "eat": return item?.food == true && item.can_eat;
            case "equip": return item?.equipable == true;
            case "use": return item?.usable == true;
            case "interact": return entity?.kind == "interactable" && InRange(s.actor.position, entity);
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
