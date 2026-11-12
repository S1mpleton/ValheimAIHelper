using System;

namespace ValheimAIHelper;

public static class UiSelection
{
    public static int MenuButtonScore(string name, string path, bool active)
    {
        name = name.ToLowerInvariant(); path = path.ToLowerInvariant();
        if (name == "valheimaihelperbutton" || path.Contains("valheimaihelperbutton")) return int.MinValue;
        int score = active ? 10 : 0;
        if (path.Contains("menuentries")) score += 100;
        if (name.Contains("continue") || name.Contains("resume")) score += 80;
        else if (name.Contains("settings")) score += 60;
        else if (name.Contains("player") || name.Contains("save")) score += 40;
        if (path.Contains("settings/") || path.Contains("dialog/quit") || path.Contains("logout")) score -= 150;
        return score;
    }
}
