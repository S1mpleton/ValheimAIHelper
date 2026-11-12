using System;
using System.Runtime.Serialization;

namespace ValheimAIHelper;

// WireJson serializes these fields. Names intentionally match the Python wire schema.
[Serializable] public sealed class Position { public float x, y, z; }
[Serializable] public sealed class InventoryEntry
{
    public string id = "", name = "";
    public int count;
    public bool equipped, food, equipable;
    public string category = "";
    public float damage, durability = 1, food_health, food_stamina;
    public bool usable;
    public bool can_eat = true;
    public float weight;
    public int tool_tier;
    public string effect = "";
    public int quality = 1, max_quality = 1;
    public float armor, block_power;
    public string ammo_type = "", skill = "", damage_types = "";
}
[Serializable] public sealed class Actor
{
    public string id = "", name = "";
    public Position position = new();
    public Position forward = new() { z = 1 };
    public float health, max_health, stamina;
    public float max_stamina = 100;
    public int food_slots;
    public string weapon = "", biome = "";
    public bool blocking, swimming, encumbered;
    public float carry_weight, max_carry_weight = 300, eitr;
    public int free_slots, comfort;
    public bool sheltered, rested, wet, cold, attached, sitting;
    public string station_id = "";
    public string[] status_effects = Array.Empty<string>();
    public string guardian_power = "";
    public float guardian_cooldown;
    public InventoryEntry[] inventory = Array.Empty<InventoryEntry>();
}
[Serializable] public sealed class Entity
{
    public string id = "", name = "", kind = "";
    public Position position = new();
    public float distance;
    public float health, max_health;
    public bool targeting_us, targeting_leader, attacking;
    public string category = "";
    public string state = "";
    public int tool_tier;
    public int count = 1;
    public float weight;
    public bool pickup_allowed = true;
}
[Serializable] public sealed class RecipeOption
{
    public string id = "", name = "", station = "", requirements = "";
    public bool craftable;
    public int amount = 1;
}
[Serializable] public sealed class BuildOption
{
    public string id = "", name = "", requirements = "", category = "";
    public bool available;
}
[Serializable] public sealed class Snapshot
{
    public float ground_scan_radius = 20;
    public int ground_items_total;
    public bool ground_items_truncated;
    public int protocol_version = 1;
    public string session_id = "";
    public int sequence;
    public Actor actor = new();
    public Entity[] entities = Array.Empty<Entity>();
    public string leader_id = "", previous_result = "";
    public bool background_planning;
    public bool listen_chat = true;
    public string plan_id = "", action_status = "";
    public string[] recent_events = Array.Empty<string>();
    public RecipeOption[] recipes = Array.Empty<RecipeOption>();
    public BuildOption[] build_options = Array.Empty<BuildOption>();
    public string container_id = "", weather = "";
    public InventoryEntry[] container_inventory = Array.Empty<InventoryEntry>();
    public bool daylight = true;
    public string[] supported_actions = { "wait", "follow", "move", "attack", "pickup", "eat", "equip" };
}
[Serializable] public sealed class BridgeError { public string detail = ""; }
[DataContract] public sealed class Decision
{
    [DataMember] public int protocol_version, sequence, command_revision;
    [DataMember] public string action = "", target_id = "", say = "", source = "", plan_id = "", mode = "";
    [DataMember] public float duration;
    [DataMember] public Position? destination;
    [DataMember] public bool run;
    [DataMember] public bool avoid_combat;
    [DataMember] public string item_id = "", text = "";
    [DataMember] public int amount, rotation;
}
