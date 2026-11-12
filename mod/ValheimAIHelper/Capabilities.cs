using System.Linq;

namespace ValheimAIHelper;

public static class Capabilities
{
    public static readonly string[] All = {
        "wait", "follow", "move", "jump", "look", "attack", "secondary_attack", "defend", "flee", "dodge", "hunt", "gather",
        "pickup", "interact", "eat", "equip", "unequip", "use", "drop", "use_on", "hide_weapon", "show_weapon", "sneak", "unsneak",
        "rest", "stand", "guardian_power", "emote", "speak", "sleep", "open", "close", "take", "deposit", "stack", "craft", "upgrade", "repair", "build", "repair_building"
    };
    public static string[] Supported(bool combat, bool placement) => All.Where(a => (combat || (a != "attack" && a != "secondary_attack" && a != "hunt")) &&
        (placement || (a != "build" && a != "repair_building"))).ToArray();
}
