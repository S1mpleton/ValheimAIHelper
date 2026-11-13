using System;
using System.Collections.Concurrent;
using System.Reflection;

namespace ValheimAIHelper;

// UI assemblies are resolved dynamically, but never scanned again per label,
// pointer movement or refresh. Missing types may become available later.
public static class UiReflection
{
    private static readonly ConcurrentDictionary<string, Type> types = new();
    private static readonly ConcurrentDictionary<(Type, string), Lazy<PropertyInfo?>> properties = new();
    private static readonly ConcurrentDictionary<(Type, string), Lazy<FieldInfo?>> fields = new();
    public static Type? Resolve(string name, Func<string, Type?> resolve)
    {
        if (types.TryGetValue(name, out var type)) return type;
        type = resolve(name); if (type != null) types.TryAdd(name, type);
        return type;
    }
    public static bool Write(object target, string name, object? value)
    {
        var property = Property(target.GetType(), name);
        if (property == null || !property.CanWrite || property.CanRead && Equals(property.GetValue(target, null), value)) return false;
        property.SetValue(target, value, null); return true;
    }
    public static PropertyInfo? Property(Type type, string name) => properties.GetOrAdd((type, name), key => new Lazy<PropertyInfo?>(() =>
    {
        for (var t = key.Item1; t != null; t = t.BaseType)
        {
            var property = t.GetProperty(key.Item2, BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static | BindingFlags.DeclaredOnly);
            if (property != null) return property;
        }
        return null;
    })).Value;
    public static FieldInfo? Field(Type type, string name) => fields.GetOrAdd((type, name), key => new Lazy<FieldInfo?>(() =>
    {
        for (var t = key.Item1; t != null; t = t.BaseType)
        {
            var field = t.GetField(key.Item2, BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static | BindingFlags.DeclaredOnly);
            if (field != null) return field;
        }
        return null;
    })).Value;
}
