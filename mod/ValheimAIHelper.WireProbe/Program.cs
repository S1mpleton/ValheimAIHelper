using System;
using System.IO;
using System.Reflection.Metadata;
using System.Reflection.PortableExecutable;
using ValheimAIHelper;

Console.OutputEncoding = new System.Text.UTF8Encoding(false);
Console.InputEncoding = new System.Text.UTF8Encoding(false);
if (args.Length > 1 && args[0] == "--check-game-access")
{
    using var file = File.OpenRead(args[1]);
    using var pe = new PEReader(file);
    var metadata = pe.GetMetadataReader();
    foreach (var handle in metadata.MemberReferences)
    {
        var member = metadata.GetMemberReference(handle);
        if (metadata.GetString(member.Name) == "GetRightItem")
        { Console.Error.WriteLine("Inaccessible direct game call: Humanoid.GetRightItem (protected in the runtime)"); return 1; }
    }
    Console.WriteLine("GAME_ACCESS_OK: no direct reference to protected GetRightItem"); return 0;
}
if (args.Length > 0 && args[0] == "--capabilities") { Console.WriteLine(WireJson.Serialize(Capabilities.All)); return 0; }
if (args.Length > 0 && args[0] == "--ui-ollama")
{
    Console.WriteLine(WireJson.Serialize(new ProviderOptions { provider="ollama",model="wire-agent:Q4",ollama_url="http://127.0.0.1:11434" })); return 0;
}
if (args.Length > 0 && args[0] == "--read-ollama-status")
{
    var state = WireJson.Deserialize<BridgeUiStatus>(Console.In.ReadToEnd());
    if (state.provider!="ollama" || !state.provider_ready || state.model!="wire-agent:Q4") return 1;
    Console.WriteLine("OLLAMA_STATUS_OK"); return 0;
}
if (args.Length > 0 && args[0] == "--read-dialogue-index")
{
    var index=WireJson.Deserialize<DialogueIndex>(Console.In.ReadToEnd());
    if(index.entries.Length==0||index.entries[0].provider!="ollama"||index.entries[0].status!="ok")return 1;
    Console.WriteLine("DIALOGUE_INDEX_OK");return 0;
}
if (args.Length > 0 && args[0] == "--read-dialogue-page")
{
    var page=WireJson.Deserialize<DialoguePage>(Console.In.ReadToEnd());
    if(page.part!="response"||!page.text.Contains("pickup")||page.pages<1)return 1;
    Console.WriteLine("DIALOGUE_PAGE_OK");return 0;
}
if (args.Length > 0 && args[0] == "--read-dialogue-export")
{
    var chunk=WireJson.Deserialize<DialogueExport>(Console.In.ReadToEnd());
    if(chunk.offset!=0||chunk.next_offset<1||!chunk.text.Contains("Valheim AI Helper"))return 1;
    Console.WriteLine("DIALOGUE_EXPORT_OK");return 0;
}
if (args.Length > 0 && args[0] == "--ollama-ui-timeout")
{ Console.WriteLine(AiProviders.UiTimeout("ollama")); return 0; }
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
    snapshot.ground_scan_radius=128;snapshot.ground_items_total=1;
    snapshot.entities = new[] {snapshot.entities[0], new Entity {id="drop:far",name="Камень",kind="item",count=7,weight=14,distance=45,
        position=new Position {x=snapshot.actor.position.x+45,y=snapshot.actor.position.y,z=snapshot.actor.position.z}}};
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
else if (args.Length > 0 && args[0] == "--check-give-extended-response")
{
    var decision=WireJson.Deserialize<Decision>(Console.In.ReadToEnd());
    if(!DecisionPolicy.Accept(decision,snapshot,false)||decision.action!="give"||decision.target_id!="character:42"||decision.item_id!="inventory:stones"||decision.amount!=5)return 1;
    Console.WriteLine("GIVE_ACCEPTED:5");
}
else if (args.Length > 0 && args[0] == "--check-pickup-extended-response")
{
    var decision=WireJson.Deserialize<Decision>(Console.In.ReadToEnd());
    if(!DecisionPolicy.Accept(decision,snapshot,false)||decision.action!="pickup"||decision.target_id!="drop:far")return 1;
    Console.WriteLine("PICKUP_ACCEPTED:45m");
}
else if (args.Length > 0 && args[0] == "--check-extended-response")
{
    var decision = WireJson.Deserialize<Decision>(Console.In.ReadToEnd());
    if (!DecisionPolicy.Accept(decision, snapshot, true) || decision.action != "drop" || decision.amount != 5 || decision.target_id != "inventory:stones") return 1;
    Console.WriteLine("DROP_ACCEPTED:5");
}
else Console.WriteLine(WireJson.Serialize(snapshot));
return 0;
