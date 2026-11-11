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
    [DataMember] public int request_interval_seconds = 20;
    [DataMember] public BridgeKeys keys_present = new();
    [DataMember] public ProviderFailure? provider_error;
    [DataMember] public string instance_id = "";
    [DataMember] public bool planning;
    [DataMember] public BridgeEvent[] events = Array.Empty<BridgeEvent>();
}
[DataContract] public sealed class BridgeEvent
{
    [DataMember] public int id;
    [DataMember] public string time = "", source = "", message = "";
}
[DataContract] public sealed class ProviderOptions
{
    [DataMember] public string provider = "gemini", model = "", api_key = "";
    [DataMember] public int request_interval_seconds = 20;
}
[DataContract] public sealed class BridgeCommand
{
    [DataMember] public string mode = "stop", objective = "";
}
[DataContract] public sealed class ModelListRequest { [DataMember] public string api_key = ""; }
[DataContract] public sealed class ModelListReply { [DataMember] public string[] models = Array.Empty<string>(); }
[DataContract] public sealed class ModelCheckRequest { [DataMember] public string model = "", api_key = ""; }
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
