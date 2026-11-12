using System;
using System.Collections.Concurrent;
using System.Linq;
using System.Reflection;
using System.Runtime.ExceptionServices;

namespace ValheimAIHelper;

// Compile-time game packages publicize protected members. Invoke those members
// against the real runtime instead of emitting an inaccessible direct call.
internal static class NativeMethods
{
    private static readonly ConcurrentDictionary<string, MethodInfo> methods = new();
    public static object? Call(object target, string name, params object?[] args)
    {
        var type = target.GetType();
        string key = type.AssemblyQualifiedName + ":" + name + ":" + string.Join(";", args.Select(a => a?.GetType().AssemblyQualifiedName ?? "null"));
        if (!methods.TryGetValue(key, out var method))
        {
            for (var current = type; current != null && method == null; current = current.BaseType)
                method = current.GetMethods(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.DeclaredOnly)
                    .FirstOrDefault(m => m.Name == name && m.GetParameters().Length == args.Length &&
                        m.GetParameters().Select((p, i) => args[i] == null || (p.ParameterType.IsByRef ? p.ParameterType.GetElementType()! : p.ParameterType).IsInstanceOfType(args[i])).All(v => v));
            if (method == null) throw new MissingMethodException(type.FullName, name);
            methods.TryAdd(key, method);
        }
        try { return method.Invoke(target, args); }
        catch (TargetInvocationException ex) when (ex.InnerException != null)
        { ExceptionDispatchInfo.Capture(ex.InnerException).Throw(); throw; }
    }
}
