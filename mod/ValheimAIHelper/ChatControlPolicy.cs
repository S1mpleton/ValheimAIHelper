namespace ValheimAIHelper;

internal static class ChatControlPolicy
{
    public static bool Allows(bool everyone, long? leaderId, long speakerId, long speakerOwner, long sender) =>
        speakerId != 0 && speakerOwner != 0 && speakerOwner == sender && (everyone || leaderId == speakerId);
}
