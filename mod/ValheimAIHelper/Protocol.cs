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
}
[Serializable] public sealed class Snapshot
{
    public int protocol_version = 1;
    public string session_id = "";
    public int sequence;
    public Actor actor = new();
    public Entity[] entities = Array.Empty<Entity>();
    public string leader_id = "", previous_result = "";
    public bool background_planning;
    public string plan_id = "", action_status = "";
    public string[] recent_events = Array.Empty<string>();
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
}
