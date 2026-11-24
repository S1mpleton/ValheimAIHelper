using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;

namespace ValheimAIHelper;

// A permission callback can run later than OnNewChatMessage. Scope the actual
// callback, never a queue keyed by message text: a human may say the same words.
internal static class NpcChatContext
{
    [ThreadStatic] private static string? name;
    internal static string? Name => name;
    internal static Scope Enter(string? speaker) => new(speaker);
    internal static bool TryNamedLine(object terminal, object chat, string text, object type, bool timestamp)
    {
        if (name == null || !ReferenceEquals(terminal, chat)) return false;
        NativeMethods.Call(terminal, "AddString", name, text, type, timestamp);
        return true;
    }

    internal readonly struct Scope : IDisposable
    {
        private readonly string? previous;
        internal Scope(string? speaker) { previous = name; name = speaker; }
        public void Dispose() { name = previous; }
    }

    internal static object NamedUser(object user, string speaker)
    {
        // UserInfo contains the authentic host platform identity used by native
        // permissions. Clone the object and change only the character name.
        var clone = typeof(object).GetMethod("MemberwiseClone", BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(user, null)!;
        var field = user.GetType().GetField("Name", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
        if (field == null || field.FieldType != typeof(string)) throw new MissingFieldException(user.GetType().FullName, "Name");
        field.SetValue(clone, CleanName(speaker));
        return clone;
    }

    internal static string CleanName(string speaker)
    {
        string value = new string((speaker ?? "").Select(c => c == '<' || c == '>' || char.IsControl(c) ? ' ' : c).ToArray()).Trim();
        if (value.Length == 0) value = "NPC";
        return value.Length > 120 ? value.Substring(0, 120) : value;
    }

    internal static long SpeakerId(string objectId, long transportSender)
    {
        // The native world-bubble key is a peer ID. NPCs share the host peer,
        // so use their own stable ZDO identity for this DISPLAY key only.
        ulong hash = 14695981039346656037UL;
        foreach (char c in objectId) hash = unchecked((hash ^ c) * 1099511628211UL);
        long value = unchecked((long)(hash | 0x8000000000000000UL));
        return value == transportSender ? value ^ 1 : value;
    }

    internal static IEnumerable<MethodBase> PermissionCallbacks(Type chat)
    {
        foreach (var nested in chat.GetNestedTypes(BindingFlags.Public | BindingFlags.NonPublic))
        {
            var fields = nested.GetFields(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            if (!fields.Any(field => field.Name == "go") || !fields.Any(field => field.Name == "sender")) continue;
            foreach (var method in nested.GetMethods(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.DeclaredOnly))
            {
                var parameters = method.GetParameters();
                if (method.ReturnType == typeof(void) && parameters.Length == 1 &&
                    parameters[0].ParameterType.Name.Contains("PermissionResult")) yield return method;
            }
        }
    }
}
