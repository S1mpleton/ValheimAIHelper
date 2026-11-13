using System;
using System.Linq;

namespace ValheimAIHelper;

public static class DecisionPolicy
{
    public static bool Accept(Decision? d, Snapshot s, bool allowCombat, int minimumCommandRevision = 0)
    {
        if (d == null || d.protocol_version != 1 || d.sequence != s.sequence || d.command_revision < minimumCommandRevision ||
            float.IsNaN(d.duration) || float.IsInfinity(d.duration) ||
            d.duration < .25f || d.duration > 3f || d.say == null || d.say.Length > 240 ||
            d.target_id == null || d.target_id.Length > 100 || (d.plan_id != null && d.plan_id.Length > 80))
            return false;
        if (d.destination != null)
        {
            var p = d.destination;
            double x = p.x - s.actor.position.x, y = p.y - s.actor.position.y, z = p.z - s.actor.position.z;
            if ((d.action != "move" && d.action != "jump" && d.action != "build" && d.action != "look") || !ObservationValues.Finite(p.x) ||
                !ObservationValues.Finite(p.y) || !ObservationValues.Finite(p.z) || x*x + y*y + z*z > (d.action == "build" ? 64 : d.action == "look" ? 3600 : 900) || (d.action != "look" && Math.Abs(y) > 8))
                return false;
        }
        if ((d.action == "drop" || d.action == "give" || d.action == "take" || d.action == "deposit" || d.action == "use_on") && (d.amount < 1 || d.amount > 100000)) return false;
        if (d.text != null && d.text.Length > 220 || d.item_id != null && d.item_id.Length > 100 || d.rotation < 0 || d.rotation > 15) return false;
        if (!TransferPolicy.ValidSelection(d.selection) || (d.selection != null && d.selection != "" && d.selection != "exact" && d.action != "give" && d.action != "drop")) return false;
        var entity = s.entities.FirstOrDefault(e => e.id == d.target_id);
        allowCombat = allowCombat && !d.avoid_combat;
        var item = s.actor.inventory.FirstOrDefault(i => i.id == d.target_id);
        if (d.action != "wait" && !s.supported_actions.Contains(d.action)) return false;
        switch (d.action)
        {
            case "wait": return d.target_id == "";
            case "move": case "jump": return d.target_id == "";
            case "look": return d.target_id == "" && d.destination != null;
            case "hide_weapon": case "show_weapon": case "sneak": case "unsneak": case "rest": case "stand":
            case "guardian_power": case "close": return d.target_id == "";
            case "emote": return d.target_id == "" && new[] { "wave", "cheer", "point", "sit", "bow", "challenge", "cry", "nonono", "thumbsup" }.Contains(d.text);
            case "speak": return d.target_id == "" && !string.IsNullOrWhiteSpace(d.text);
            case "follow": return entity?.kind == "player" && entity.id == s.leader_id;
            case "attack": return allowCombat && entity?.kind == "enemy" && InRange(s.actor.position, entity);
            case "secondary_attack": return allowCombat && entity?.kind == "enemy" && InRange(s.actor.position, entity);
            case "hunt": return allowCombat && entity?.kind == "animal" && InRange(s.actor.position, entity);
            case "gather": return entity?.kind == "resource" && InRange(s.actor.position, entity) && ResourcePolicy.CanGather(entity, s.actor.inventory);
            case "defend": case "flee": case "dodge": return entity?.kind == "enemy" && InRange(s.actor.position, entity);
            case "pickup": return entity?.kind == "item" && InRange(s.actor.position, entity, 128);
            case "give": return entity?.kind == "player" && InRange(s.actor.position, entity, 60) && TransferPolicy.Available(s.actor.inventory, d.item_id, d.selection, d.amount);
            case "eat": return item?.food == true && item.can_eat;
            case "equip": return item?.equipable == true;
            case "unequip": return item?.equipable == true;
            case "drop": return TransferPolicy.Available(s.actor.inventory, d.target_id, d.selection, d.amount);
            case "use": return item?.usable == true;
            case "interact": return entity?.kind == "interactable" && InRange(s.actor.position, entity);
            case "use_on": return entity?.kind == "interactable" && InRange(s.actor.position, entity) && s.actor.inventory.Any(i => i.id == d.item_id && i.count >= d.amount);
            case "open": case "stack": return entity?.category == "container" && InRange(s.actor.position, entity);
            case "take": case "deposit":
                return entity?.category == "container" && InRange(s.actor.position, entity) && s.container_id == d.target_id &&
                    (d.action == "take" ? s.container_inventory : s.actor.inventory).Any(i => i.id == d.item_id && i.count >= d.amount);
            case "repair": return entity?.category == "station" && InRange(s.actor.position, entity);
            case "sleep": return entity?.category == "bed" && InRange(s.actor.position, entity);
            case "repair_building": return entity?.category == "structure" && InRange(s.actor.position, entity);
            case "craft": return d.amount >= 1 && d.amount <= 10 && s.recipes.Any(r => r.id == d.target_id && r.craftable);
            case "upgrade": return d.amount == 1 && s.recipes.Any(r => r.id == d.target_id) && s.actor.inventory.Any(i => i.id == d.item_id);
            case "build": return d.destination != null && s.build_options.Any(b => b.id == d.target_id && b.available);
            default: return false;
        }
    }

    private static bool InRange(Position from, Entity target, double range = 20)
    {
        double x = from.x - target.position.x, y = from.y - target.position.y, z = from.z - target.position.z;
        double square = x * x + y * y + z * z;
        return !double.IsNaN(square) && square <= range * range && target.distance >= 0 && target.distance <= range;
    }
}
