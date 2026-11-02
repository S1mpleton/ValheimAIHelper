using System.IO;
using System.Runtime.Serialization.Json;
using System.Text;

namespace ValheimAIHelper;

// Explicit, engine-independent wire encoding. This exact code is exercised by
// the C# -> HTTP -> Python -> C# integration check, rather than hand-made JSON.
public static class WireJson
{
    public static string Serialize<T>(T value)
    {
        using (var stream = new MemoryStream())
        {
            new DataContractJsonSerializer(typeof(T)).WriteObject(stream, value);
            return Encoding.UTF8.GetString(stream.ToArray());
        }
    }

    public static T Deserialize<T>(string json)
    {
        using (var stream = new MemoryStream(Encoding.UTF8.GetBytes(json)))
            return (T)new DataContractJsonSerializer(typeof(T)).ReadObject(stream)!;
    }
}
