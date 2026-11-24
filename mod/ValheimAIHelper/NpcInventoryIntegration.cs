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
                // Suspend only native input. Keep the running bridge session,
                // requested mode, task pool and any mutation receipt intact.
                // Inventory observations report simulation_paused until close.
                p.executor.Pause();
                npc.GetComponent<CompanionNpc>().Stop();
                p.SetPanel(false);
                if (Menu.instance) Menu.instance.Hide();
                Game.Unpause();
            }, out string error))
            {
                p.status = "Обмен вещами с NPC. После закрытия прежняя работа продолжится.";
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

    internal static void NpcInventoryClosed(Player npc)
    {
        var p = instance;
        if (p == null || !p.active || p.actor != npc) return;
        p.status = "Обмен закрыт; продолжаю прежнюю работу.";
        p.nextUiPoll = 0;
        p.AddLog("npc-inventory", p.status);
    }
}
