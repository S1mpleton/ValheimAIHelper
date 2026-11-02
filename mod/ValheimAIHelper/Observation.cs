using System.Collections.Generic;
using System.Linq;
using UnityEngine;

namespace ValheimAIHelper;

internal sealed class Observation
{
    public Snapshot Data = new();
    public readonly Dictionary<string, Character> Characters = new();
    public readonly Dictionary<string, ItemDrop> Drops = new();
    public readonly Dictionary<string, ItemDrop.ItemData> Items = new();

    internal static string Id(Character c) => "character:" + c.GetInstanceID();
    private static Position Pos(Vector3 p)
    {
        if (!ObservationValues.Finite(p.x) || !ObservationValues.Finite(p.y) || !ObservationValues.Finite(p.z))
            throw new System.InvalidOperationException("Position is not ready");
        return new Position { x = p.x, y = p.y, z = p.z };
    }
    private static string Name(string value)
    {
        string name = Localization.instance != null ? Localization.instance.Localize(value ?? "") : value;
        return ObservationValues.Text(name, 120);
    }

    public static bool IsEnemy(Player actor, Character target, Player? leader)
    {
        if (!target || target is Player || target.IsDead() || target.IsTamed()) return false;
        var ai = target.GetBaseAI();
        // Observe only actively hostile monsters targeting us or our selected leader.
        if (!(ai is MonsterAI) || !ai.IsAlerted() || !BaseAI.IsEnemy(actor, target)) return false;
        var victim = ai.GetTargetCreature();
        return victim == actor || (leader != null && leader && victim == leader);
    }

    public static Observation Capture(Player actor, Player? leader, string session, int sequence, string result)
    {
        var o = new Observation();
        var origin = actor.transform.position;
        var inventory = actor.GetInventory().GetAllItems().Where(i => i != null && i.m_stack > 0).Take(64).ToList();
        var entries = new List<InventoryEntry>();
        for (int i = 0; i < inventory.Count; i++)
        {
            var item = inventory[i];
            var id = "inventory:" + i;
            o.Items[id] = item;
            entries.Add(new InventoryEntry { id = id, name = Name(item.m_shared.m_name),
                count = System.Math.Min(item.m_stack, 100000), equipped = item.m_equipped,
                food = item.m_shared.m_food > 0, equipable = item.IsEquipable() });
        }
        var entities = new List<Entity>();
        if (leader != null && leader && !leader.IsDead() && Vector3.Distance(origin, leader.transform.position) <= 60)
        {
            string id = Id(leader);
            o.Characters[id] = leader;
            entities.Add(new Entity { id = id, name = Name(leader.GetPlayerName()), kind = "player",
                position = Pos(leader.transform.position), distance = Vector3.Distance(origin, leader.transform.position) });
            o.Data.leader_id = id;
        }
        foreach (var c in Character.GetAllCharacters().Where(c => c && IsEnemy(actor, c, leader))
                     .Where(c => Vector3.Distance(origin, c.transform.position) <= 20)
                     .OrderBy(c => Vector3.SqrMagnitude(origin - c.transform.position)).Take(16))
        {
            string id = Id(c);
            o.Characters[id] = c;
            entities.Add(new Entity { id = id, name = Name(c.GetHoverName()), kind = "enemy",
                position = Pos(c.transform.position), distance = Vector3.Distance(origin, c.transform.position) });
        }
        foreach (var d in Object.FindObjectsOfType<ItemDrop>()
                     .Where(d => d && d.CanPickup() && Vector3.Distance(origin, d.transform.position) <= 20)
                     .OrderBy(d => Vector3.SqrMagnitude(origin - d.transform.position)).Take(32))
        {
            string id = "drop:" + d.GetInstanceID();
            o.Drops[id] = d;
            entities.Add(new Entity { id = id, name = Name(d.m_itemData.m_shared.m_name), kind = "item",
                position = Pos(d.transform.position), distance = Vector3.Distance(origin, d.transform.position) });
        }
        o.Data.session_id = session;
        o.Data.sequence = sequence;
        o.Data.previous_result = ObservationValues.Text(result, 240);
        o.Data.entities = entities.ToArray();
        float maxHealth = actor.GetMaxHealth();
        if (!ObservationValues.Finite(maxHealth) || maxHealth <= 0)
            throw new System.InvalidOperationException("Player health is not ready");
        o.Data.actor = new Actor { id = "player:" + actor.GetPlayerID(), name = Name(actor.GetPlayerName()), position = Pos(origin),
            forward = Pos(actor.transform.forward),
            health = ObservationValues.Vital(actor.GetHealth()), max_health = maxHealth, stamina = ObservationValues.Vital(actor.GetStamina()),
            inventory = entries.ToArray() };
        return o;
    }
}
