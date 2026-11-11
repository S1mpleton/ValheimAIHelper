using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.RegularExpressions;

namespace ValheimAIHelper;

public sealed class LogJournal
{
    private readonly object gate = new();
    private readonly Queue<string> lines = new();
    private readonly HashSet<string> secrets = new();
    private string last = "";
    public void RegisterSecret(string value) { if (!string.IsNullOrWhiteSpace(value)) lock (gate) secrets.Add(value.Trim()); }
    public string Redact(string value)
    {
        lock (gate)
        {
            foreach (string secret in secrets.OrderByDescending(s => s.Length)) value = value.Replace(secret, "[REDACTED]");
            value = Regex.Replace(value, @"(?:sk-|AQ\.|AIza)[A-Za-z0-9_-]{16,}", "[REDACTED]");
            value = Regex.Replace(value, @"(?i)Bearer\s+\S+", "Bearer [REDACTED]");
            value = Regex.Replace(value, @"(?i)(api[_-]?key|access_token|token)\s*[=:]\s*[^\s,;]+", "$1=[REDACTED]");
            return value;
        }
    }
    public void Add(string source, string message)
    {
        lock (gate)
        {
            string text = Redact(source + ": " + message).Replace("\r", " ");
            if (text == last) return;
            last = text;
            lines.Enqueue(DateTime.Now.ToString("HH:mm:ss") + " " + text.Substring(0, Math.Min(2200, text.Length)));
            while (lines.Count > 300) lines.Dequeue();
        }
    }
    public string Text { get { lock (gate) return string.Join("\n", lines); } }
    public void Clear() { lock (gate) { lines.Clear(); last = ""; } }
}
