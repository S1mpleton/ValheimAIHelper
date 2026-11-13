namespace ValheimAIHelper;

public static class NpcAuthorityPolicy
{
    public static bool MayControl(bool companion, bool worldHost, bool owned, bool localHuman, bool alive) =>
        companion && worldHost && owned && !localHuman && alive;
}
