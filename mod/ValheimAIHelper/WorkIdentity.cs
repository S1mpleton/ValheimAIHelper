namespace ValheimAIHelper;

public static class WorkIdentity
{
    public static bool Same(Decision a, Decision b) => a.plan_id == b.plan_id && a.command_revision == b.command_revision &&
        a.action == b.action && a.target_id == b.target_id && a.source == b.source && a.mode == b.mode &&
        a.item_id == b.item_id && a.amount == b.amount && a.selection == b.selection && a.text == b.text && a.rotation == b.rotation &&
        (a.destination == null && b.destination == null || a.destination != null && b.destination != null &&
            a.destination.x == b.destination.x && a.destination.y == b.destination.y && a.destination.z == b.destination.z);
}
