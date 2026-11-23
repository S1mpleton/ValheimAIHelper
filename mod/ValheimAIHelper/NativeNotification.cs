using System;
using System.Collections.Concurrent;
using System.Linq;
using System.Reflection;

namespace ValheimAIHelper;

// Notification signatures may gain optional arguments between game versions.
// Keep the call late-bound, including those arguments, just like other native
// methods whose publicized reference signatures cannot be trusted at runtime.
internal static class NativeNotification
{
    private static readonly ConcurrentDictionary<(Type, string), MethodInfo[]> methods = new();

    internal static bool TryShow(object target, string name, object messageType, string text, out string failure)
    {
        failure = "MissingMethodException";
        var candidates = methods.GetOrAdd((target.GetType(), name), key =>
        {
            var found = new System.Collections.Generic.List<MethodInfo>();
            for (var type = key.Item1; type != null; type = type.BaseType)
                found.AddRange(type.GetMethods(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.DeclaredOnly)
                    .Where(method => method.Name == key.Item2 && !method.ContainsGenericParameters));
            return found.OrderBy(method => method.GetParameters().Length).ToArray();
        });
        foreach (var method in candidates)
        {
            var parameters = method.GetParameters();
            if (parameters.Length < 2 || !parameters[0].ParameterType.IsInstanceOfType(messageType) ||
                parameters[1].ParameterType != typeof(string) || parameters.Skip(2).Any(parameter => !parameter.IsOptional)) continue;
            var args = new object?[parameters.Length];
            args[0] = messageType; args[1] = text;
            for (int index = 2; index < parameters.Length; index++)
            {
                var parameter = parameters[index];
                var value = parameter.DefaultValue;
                args[index] = value == DBNull.Value || value == Missing.Value
                    ? (parameter.ParameterType.IsValueType ? Activator.CreateInstance(parameter.ParameterType) : null)
                    : value;
            }
            try
            {
                NativeMethods.Call(target, name, args);
                failure = "";
                return true;
            }
            catch (Exception ex) { failure = ex.GetType().Name; }
        }
        return false;
    }
}
