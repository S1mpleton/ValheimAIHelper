using System;
using ValheimAIHelper;

Console.OutputEncoding = new System.Text.UTF8Encoding(false);
Console.InputEncoding = new System.Text.UTF8Encoding(false);
var snapshot = new Snapshot
{
    session_id = "wire-probe", sequence = args.Length > 1 ? int.Parse(args[1]) : 1,
    leader_id = "character:42", previous_result = "Ожидание \"решения\"\nиз игры",
    actor = new Actor
    {
        id = "player:-9223372036854775807", name = "Бьёрн",
        position = new Position { x = -1087.25f, y = 32.125f, z = 4799.5f },
        forward = new Position { z = 1 }, health = 25, max_health = 25,
        stamina = ObservationValues.Vital(-.01f),
        inventory = new[] { new InventoryEntry { id = "inventory:0", name = "Дубина", count = 1, equipable = true } }
    },
    entities = new[] { new Entity { id = "character:42", name = "Лидер", kind = "player", distance = 5,
        position = new Position { x = -1082.25f, y = 32.125f, z = 4799.5f } } }
};
if (args.Length > 0 && args[0] == "--check-response")
{
    var decision = WireJson.Deserialize<Decision>(Console.In.ReadToEnd());
    if (!DecisionPolicy.Accept(decision, snapshot, false)) return 1;
    Console.WriteLine("ACCEPTED:" + decision.action);
}
else Console.WriteLine(WireJson.Serialize(snapshot));
return 0;
