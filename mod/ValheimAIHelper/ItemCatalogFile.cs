using System;
using System.IO;
using System.Runtime.Serialization.Json;
using System.Text;

namespace ValheimAIHelper;

[Serializable] public sealed class ItemCatalogExport
{
    public string game_version = "", language = "", version = "";
    public string meaning = "Item type definitions from ObjectDB; not items currently available to the NPC.";
    public int total;
    public ItemCatalogEntry[] items = Array.Empty<ItemCatalogEntry>();
}

public static class ItemCatalogFile
{
    public static void Write(string path, string gameVersion, string language, string version, ItemCatalogEntry[] entries)
    {
        string temporary = path + ".tmp";
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(path)!);
            using (var stream = new FileStream(temporary, FileMode.Create, FileAccess.Write, FileShare.None))
            using (var writer = JsonReaderWriterFactory.CreateJsonWriter(stream, new UTF8Encoding(false), false, true, "  "))
            {
                new DataContractJsonSerializer(typeof(ItemCatalogExport)).WriteObject(writer, new ItemCatalogExport
                { game_version = gameVersion, language = language, version = version, total = entries.Length, items = entries });
                writer.Flush(); stream.Flush();
            }
            // The old complete catalog stays intact if serialization/write fails.
            if (File.Exists(path)) File.Replace(temporary, path, null);
            else File.Move(temporary, path);
        }
        finally
        {
            if (File.Exists(temporary)) File.Delete(temporary);
        }
    }
}
