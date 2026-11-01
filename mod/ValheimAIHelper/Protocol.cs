using System;

namespace ValheimAIHelper;

// JsonUtility uses serializable fields. Names intentionally match the Python wire schema.
[Serializable] public sealed class Position { public float x, y, z; }
[Serializable] public sealed class InventoryEntry
{
    public string id = "", name = "";
    public int count;
    public bool equipped, food, equipable;
}
[Serializable] public sealed class Actor
{
    public string id = "", name = "";
    public Position position = new();
    public float health, max_health, stamina;
    public InventoryEntry[] inventory = Array.Empty<InventoryEntry>();
}
[Serializable] public sealed class Entity
{
    public string id = "", name = "", kind = "";
    public Position position = new();
    public float distance;
}
[Serializable] public sealed class Snapshot
{
    public int protocol_version = 1;
    public string session_id = "";
    public int sequence;
    public Actor actor = new();
    public Entity[] entities = Array.Empty<Entity>();
    public string leader_id = "", previous_result = "";
}
[Serializable] public sealed class Decision
{
    public int protocol_version, sequence, command_revision;
    public string action = "", target_id = "", say = "", source = "";
    public float duration;
}
