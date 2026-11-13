namespace ValheimAIHelper;

public static class MotorPolicy
{
    // Reflexes own their current native input even if a previous task failed.
    // Stop/expired lease always wins. A task outcome must not cancel defence.
    public static bool Cancel(bool validControl, bool reflex, string taskStatus) =>
        !validControl || !reflex && taskStatus != "running";
}
