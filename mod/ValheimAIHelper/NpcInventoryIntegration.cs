using System;

namespace ValheimAIHelper;

public sealed partial class Plugin
{
    internal static void NpcInventoryLog(string message) => instance?.AddLog("npc-inventory", message);
    private void OpenCurrentNpcInventory()
    {
        var npc = CurrentNpc;
        if (!npc) { uiError = "Сначала создайте NPC и подойдите к нему."; return; }
        OpenNpcInventory(npc!);
    }

    internal static void OpenNpcInventory(Player npc)
    {
        NpcInventoryWindow.MarkUseAttempt();
        var p = instance;
        if (p == null) return;
        try
        {
            if (NpcInventoryWindow.TryOpen(npc, () =>
            {
                p.StopFromPanel();
                p.SetPanel(false);
                if (Menu.instance) Menu.instance.Hide();
                Game.Unpause();
            }, out string error))
            {
                p.status = "Обмен вещами с NPC. Следование / AI можно включить после закрытия.";
                p.uiMessage = p.status;
                p.AddLog("npc-inventory", "Открыт инвентарь " + npc.GetPlayerName() + "; управление NPC остановлено.");
            }
            else
            {
                p.uiError = error;
                p.AddLog("npc-inventory", error);
                if (Player.m_localPlayer) Player.m_localPlayer.Message(MessageHud.MessageType.Center, error);
            }
        }
        catch (Exception ex)
        {
            NpcInventoryWindow.Close();
            p.uiError = "Не удалось открыть инвентарь NPC. Подробности в журнале.";
            p.AddLog("npc-inventory", ex.ToString());
            if (Player.m_localPlayer) Player.m_localPlayer.Message(MessageHud.MessageType.Center, p.uiError);
        }
    }
}
