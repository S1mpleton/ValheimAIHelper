using System.Collections.Generic;
using System.Linq;
using System.Runtime.CompilerServices;
using UnityEngine;

namespace ValheimAIHelper;

internal sealed class Observation
{
    public Snapshot Data = new();
    public readonly Dictionary<string, Character> Characters = new();
    public readonly Dictionary<string, ItemDrop> Drops = new();
    public readonly Dictionary<string, ItemDrop.ItemData> Items = new();
    public readonly Dictionary<string, Component> Interactables = new();
    private static readonly Collider[] hits = new Collider[512];

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
        return ai is MonsterAI && BaseAI.IsEnemy(actor, target);
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
            // Stable across snapshots, including after another item is consumed.
            var id = "inventory:" + RuntimeHelpers.GetHashCode(item);
            o.Items[id] = item;
            entries.Add(new InventoryEntry { id = id, name = Name(item.m_shared.m_name),
                count = System.Math.Min(item.m_stack, 100000), equipped = item.m_equipped,
                food = item.m_shared.m_food > 0, equipable = item.IsEquipable(),
                category = item.m_shared.m_itemType.ToString(), damage = ObservationValues.Vital(item.GetDamage().GetTotalDamage()),
                durability = item.m_shared.m_useDurability ? Mathf.Clamp01(item.m_durability / Mathf.Max(1, item.GetMaxDurability())) : 1,
                food_health = ObservationValues.Vital(item.m_shared.m_food), food_stamina = ObservationValues.Vital(item.m_shared.m_foodStamina),
                usable = item.m_shared.m_itemType == ItemDrop.ItemData.ItemType.Consumable,
                can_eat = item.m_shared.m_food > 0 && actor.CanEat(item, false) });
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
            var victim = c.GetBaseAI()?.GetTargetCreature();
            o.Characters[id] = c;
            entities.Add(new Entity { id = id, name = Name(c.GetHoverName()), kind = "enemy",
                position = Pos(c.transform.position), distance = Vector3.Distance(origin, c.transform.position),
                health = ObservationValues.Vital(c.GetHealth()), max_health = ObservationValues.Vital(c.GetMaxHealth()),
                targeting_us = victim == actor, targeting_leader = leader && victim == leader, attacking = c.InAttack(),
                category = c.GetFaction().ToString() });
        }
        int hitCount = Physics.OverlapSphereNonAlloc(origin, 20, hits);
        var nearby = hits.Take(hitCount).Where(c => c).ToArray();
        foreach (var d in nearby.Select(c => c.GetComponentInParent<ItemDrop>()).Where(d => d && d.CanPickup()).Distinct()
                     .OrderBy(d => Vector3.SqrMagnitude(origin - d.transform.position)).Take(32))
        {
            string id = "drop:" + d.GetInstanceID();
            o.Drops[id] = d;
            entities.Add(new Entity { id = id, name = Name(d.m_itemData.m_shared.m_name), kind = "item",
                position = Pos(d.transform.position), distance = Vector3.Distance(origin, d.transform.position) });
        }
        foreach (var c in nearby.Select(h => (Component?)h.GetComponentInParent<Pickable>() ?? h.GetComponentInParent<Door>())
                     .Where(c => c && Vector3.Distance(origin, c!.transform.position) <= 20).Distinct()
                     .OrderBy(c => Vector3.SqrMagnitude(origin - c!.transform.position)).Take(12))
        {
            string id = "interactable:" + c!.GetInstanceID();
            o.Interactables[id] = c;
            var hover = c as Hoverable;
            entities.Add(new Entity { id = id, name = Name(hover?.GetHoverName() ?? c.name), kind = "interactable",
                category = c is Door ? "door" : "pickable", position = Pos(c.transform.position),
                distance = Vector3.Distance(origin, c.transform.position) });
        }
        System.Array.Clear(hits, 0, hitCount);
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
            max_stamina = Mathf.Max(1, ObservationValues.Vital(actor.GetMaxStamina())), food_slots = Mathf.Min(3, actor.GetFoods().Count),
            weapon = Name(actor.GetCurrentWeapon()?.m_shared.m_name ?? ""), blocking = actor.IsBlocking(),
            swimming = actor.IsSwimming(), encumbered = actor.IsEncumbered(), biome = actor.GetCurrentBiome().ToString(),
            inventory = entries.ToArray() };
        return o;
    }
}
