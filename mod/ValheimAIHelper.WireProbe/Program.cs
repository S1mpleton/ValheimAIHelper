using System;
using ValheimAIHelper;

Console.OutputEncoding = new System.Text.UTF8Encoding(false);
Console.InputEncoding = new System.Text.UTF8Encoding(false);
if (args.Length > 0 && args[0] == "--capabilities") { Console.WriteLine(WireJson.Serialize(Capabilities.All)); return 0; }
if (args.Length > 0 && args[0] == "--ui-chat")
{
    Console.WriteLine(WireJson.Serialize(new LeaderChatRequest { actor_name = "Бъерн", speaker = "Лидер", text = "И ещё подбери камни" })); return 0;
}
if (args.Length > 0 && args[0] == "--ui-provider")
{
    Console.WriteLine(WireJson.Serialize(new ProviderOptions { provider = "openai", model = "gpt-6-luna", api_key = "mock-from-game-ui", request_interval_seconds = 20 }));
    return 0;
}
if (args.Length > 1 && args[0] == "--ui-command")
{
    Console.WriteLine(WireJson.Serialize(new BridgeCommand { mode = args[1], objective = "Следуй за лидером" }));
    return 0;
}
if (args.Length > 0 && args[0] == "--read-ui-status")
{
    var status = WireJson.Deserialize<BridgeUiStatus>(Console.In.ReadToEnd());
    if (status.provider != "openai" || status.model != "gpt-6-luna" || !status.keys_present.openai) return 1;
    Console.WriteLine("UI_STATUS_OK"); return 0;
}
if (args.Length > 0 && args[0] == "--read-ui-models")
{
    var models = WireJson.Deserialize<ModelListReply>(Console.In.ReadToEnd());
    if (Array.IndexOf(models.models, "gpt-6-luna") < 0) return 1;
    Console.WriteLine("UI_MODELS_OK"); return 0;
}
if (args.Length > 0 && args[0] == "--read-ui-check")
{
    var check = WireJson.Deserialize<ModelCheckReply>(Console.In.ReadToEnd());
    if (!check.ok || check.checks.Length == 0) return 1;
    Console.WriteLine("UI_CHECK_OK"); return 0;
}
if (args.Length > 0 && args[0] == "--read-ui-ok")
{
    if (!WireJson.Deserialize<BridgeOk>(Console.In.ReadToEnd()).ok) return 1;
    Console.WriteLine("UI_OK"); return 0;
}
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
if (args.Length > 0 && args[0].Contains("extended"))
{
    snapshot.session_id = "wire-extended";
    snapshot.background_planning = true;
    snapshot.supported_actions = Capabilities.All;
    snapshot.actor.inventory = new[] { new InventoryEntry { id = "inventory:stones", name = "Камень", count = 20 } };
}
if (args.Length > 0 && args[0] == "--check-response")
{
    var decision = WireJson.Deserialize<Decision>(Console.In.ReadToEnd());
    if (!DecisionPolicy.Accept(decision, snapshot, false)) return 1;
    Console.WriteLine("ACCEPTED:" + decision.action);
}
else if (args.Length > 0 && args[0] == "--check-plan-response")
{
    var decision = WireJson.Deserialize<Decision>(Console.In.ReadToEnd());
    if (!DecisionPolicy.Accept(decision, snapshot, false) || string.IsNullOrEmpty(decision.plan_id) ||
        decision.destination == null || !decision.run) return 1;
    Console.WriteLine("PLAN_ACCEPTED:" + decision.action);
}
else if (args.Length > 0 && args[0] == "--check-extended-response")
{
    var decision = WireJson.Deserialize<Decision>(Console.In.ReadToEnd());
    if (!DecisionPolicy.Accept(decision, snapshot, true) || decision.action != "drop" || decision.amount != 5 || decision.target_id != "inventory:stones") return 1;
    Console.WriteLine("DROP_ACCEPTED:5");
}
else Console.WriteLine(WireJson.Serialize(snapshot));
return 0;
