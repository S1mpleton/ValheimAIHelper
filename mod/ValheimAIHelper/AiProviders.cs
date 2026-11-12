using System;

namespace ValheimAIHelper;

public static class AiProviders
{
    public const string DefaultOllamaUrl = "http://127.0.0.1:11434";
    public static readonly string[] All = { "openai", "gemini", "ollama" };
    public static bool Known(string provider) => Array.IndexOf(All, provider) >= 0;
    public static string Next(string provider) => All[(Array.IndexOf(All, provider) + 1) % All.Length];
    public static bool RequiresKey(string provider) => provider is "openai" or "gemini";
    public static int UiTimeout(string provider) => provider == "ollama" ? 310 : 110;
    public static string DisplayName(string provider) => provider == "ollama" ? "Ollama (локально)" : provider == "gemini" ? "Gemini" : "OpenAI";
}
