using System;

namespace ValheimAIHelper;

public static class LeaderCommand
{
    public static string? Parse(string text)
    {
        text = text.Trim();
        string prefix = text.StartsWith("!ai", StringComparison.OrdinalIgnoreCase) ? "!ai" : "!ии";
        if (!text.StartsWith(prefix, StringComparison.OrdinalIgnoreCase) ||
            (text.Length > prefix.Length && !char.IsWhiteSpace(text[prefix.Length]))) return null;
        string command = text.Substring(prefix.Length).Trim();
        return command.Length > 1000 ? null : command;
    }
}
