using System;
using System.Runtime.Serialization;

namespace ValheimAIHelper;

// UI endpoints return different subsets of fields; optional DataMembers allow
// a single reply type to read command/configuration responses safely.
[DataContract] public sealed class BridgeKeys
{
    [DataMember] public bool openai, gemini;
}
[DataContract] public sealed class ProviderFailure
{
    [DataMember] public string provider = "", model = "", message = "", detail = "";
}
[DataContract] public sealed class BridgeUiStatus
{
    [DataMember] public string provider = "offline", model = "", mode = "stop", objective = "";
    [DataMember] public bool connected;
    [DataMember] public string ollama_url = AiProviders.DefaultOllamaUrl;
    [DataMember] public bool provider_ready;
    [DataMember] public int request_interval_seconds = 20;
    [DataMember] public BridgeKeys keys_present = new();
    [DataMember] public ProviderFailure? provider_error;
    [DataMember] public string instance_id = "";
    [DataMember] public bool planning;
    [DataMember] public AiPerformance? performance;
    [DataMember] public BridgeEvent[] events = Array.Empty<BridgeEvent>();
}
[DataContract] public sealed class AiPerformance
{
    [DataMember] public AiSample? current, last;
}
[DataContract] public sealed class AiSample
{
    [DataMember] public string provider = "", model = "", purpose = "", status = "";
    [DataMember] public double? request_seconds, elapsed_seconds, load_seconds, prompt_seconds, generation_seconds, tokens_per_second;
    [DataMember] public int? prompt_tokens, output_tokens;
}
[DataContract] public sealed class BridgeEvent
{
    [DataMember] public int id;
    [DataMember] public string time = "", source = "", message = "";
}
[DataContract] public sealed class ProviderOptions
{
    [DataMember] public string provider = "gemini", model = "", api_key = "";
    [DataMember] public string ollama_url = AiProviders.DefaultOllamaUrl;
    [DataMember] public int request_interval_seconds = 20;
}
[DataContract] public sealed class BridgeCommand
{
    [DataMember] public string mode = "stop", objective = "";
}
[DataContract] public sealed class ModelListRequest { [DataMember] public string api_key = "", ollama_url = AiProviders.DefaultOllamaUrl; }
[DataContract] public sealed class ModelListReply { [DataMember] public string[] models = Array.Empty<string>(); }
[DataContract] public sealed class ModelCheckRequest { [DataMember] public string model = "", api_key = "", ollama_url = AiProviders.DefaultOllamaUrl; }
[DataContract] public sealed class ModelCheckReply
{
    [DataMember] public bool ok;
    [DataMember] public string model = "";
    [DataMember] public string[] checks = Array.Empty<string>();
}
[DataContract] public sealed class BridgeOk
{
    [DataMember] public bool ok, key_present;
    [DataMember] public int revision;
}
[DataContract] public sealed class LeaderChatRequest
{
    [DataMember] public string actor_name = "", speaker = "", text = "";
    [DataMember] public bool explicit_address;
    // Client-only authorization context, never provider-supplied or serialized.
    internal long SenderPlayerId = 0, SenderOwner = 0;
}
[DataContract] public sealed class LeaderChatReply
{
    [DataMember] public bool handled, queued;
    [DataMember] public string mode = "ignore", objective = "", reply = "";
}
