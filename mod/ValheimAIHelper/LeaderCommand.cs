using System;
using System.Text.RegularExpressions;

namespace ValheimAIHelper;

public static class LeaderCommand
{
    public static bool Addressed(string text, string name, out string command)
    {
        string? legacy = Parse(text);
        if (legacy != null) { command = legacy; return true; }
        string canonical = text.Normalize().ToLowerInvariant().Replace('ё', 'е');
        string actor = name.Normalize().ToLowerInvariant().Replace('ё', 'е').Trim();
        command = text.Trim();
        if (actor.Length == 0) return false;
        var match = Regex.Match(canonical, @"(?<![\p{L}\p{N}_])" + Regex.Escape(actor) + @"(?![\p{L}\p{N}_])", RegexOptions.CultureInvariant);
        if (!match.Success) return false;
        command = canonical.Remove(match.Index, match.Length).Trim(' ', ',', ':', ';', '!', '?', '.', '—', '-');
        return true;
    }
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
