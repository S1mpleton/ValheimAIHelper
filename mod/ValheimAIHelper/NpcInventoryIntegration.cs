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
        // Keep Player.Interact's native use-key/animation callback free of UI
        // changes. The request is processed by the plugin on a later frame.
        NpcInventoryWindow.RequestOpen(npc);
    }

    internal static void ProcessNpcInventoryRequest(Player npc)
    {
        var p = instance;
        if (p == null) { NpcInventoryLog("Запрос отклонён: плагин ещё не готов."); return; }
        try
        {
            p.AddLog("npc-inventory", "Обрабатываю запрос обмена с NPC.");
            if (NpcInventoryWindow.TryOpen(npc, () =>
            {
                p.PauseTasksFromPanel();
                p.SetPanel(false);
                if (Menu.instance) Menu.instance.Hide();
                Game.Unpause();
            }, out string error))
            {
                p.status = "Открываю обмен вещами с NPC. Задачи остаются на паузе после закрытия.";
                p.uiMessage = p.status;
                p.AddLog("npc-inventory", "Запрос обмена принят для " + npc.GetPlayerName() + "; управление NPC приостановлено.");
            }
            else
            {
                p.uiError = error;
                p.AddLog("npc-inventory", error);
                NpcInventoryWindow.Notify(error);
            }
        }
        catch (Exception ex)
        {
            NpcInventoryWindow.Close();
            p.uiError = "Не удалось открыть инвентарь NPC. Подробности в журнале.";
            p.AddLog("npc-inventory", ex.ToString());
            NpcInventoryWindow.Notify(p.uiError);
        }
    }
}
