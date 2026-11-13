using System;
using System.Collections.Generic;
using System.Linq;
using UnityEngine;

namespace ValheimAIHelper;

internal sealed partial class ActionExecutor
{
    private List<(ItemDrop.ItemData item, int amount)>? transfer;
    private string transferRecipient = "";
    private void TransferItems(Player player, Player? receiver)
    {
        var d = decision!; var bag = player.GetInventory();
        if (receiver && (receiver == player || receiver!.IsDead())) { Finish("Получатель недоступен", false); return; }
        if (transfer == null)
        {
            string id = d.action == "give" ? d.item_id : d.target_id;
            var candidates = TransferPolicy.Candidates(observation!.Data.actor.inventory, id, d.selection);
            if (!TransferPolicy.Available(observation.Data.actor.inventory, id, d.selection, d.amount))
            { Finish("Не хватает доступных предметов; квестовые предметы защищены", false); return; }
            transfer = new List<(ItemDrop.ItemData, int)>();
            int remaining = d.selection == "all_inventory" || d.selection == "all_matching" ? int.MaxValue : d.amount;
            foreach (var candidate in candidates)
            {
                if (remaining <= 0) break;
                if (!observation.Items.TryGetValue(candidate.id, out var item) || !bag.ContainsItem(item)) { Finish("Инвентарь изменился до передачи", false); return; }
                int take = Math.Min(remaining, item.m_stack);
                if (take > 0) { transfer.Add((item, take)); remaining -= take; }
            }
            transferRecipient = receiver ? Observation.Name(receiver!.GetPlayerName()) : "";
        }
        if (receiver)
        {
            if (Vector3.Distance(player.transform.position, receiver!.transform.position) > 2.5f)
            { actionStage = 0; MoveTo(player, receiver.transform.position, 2); if (ActionStatus == "success") ActionStatus = "running"; return; }
            if (actionStage == 0) { actionStage = 1; nextUse = Time.realtimeSinceStartup + .35f; }
            Controls(Vector3.zero, receiver.transform.position - player.transform.position);
            if (Time.realtimeSinceStartup < nextUse) return;
        }
        var batch = new TransferBatch<ItemDrop.ItemData>(transfer);
        var contents = new List<string>();
        bool delivered = batch.Commit(
            item => bag.ContainsItem(item) && !item.m_shared.m_questItem ? item.m_stack : 0,
            () => d.action != "give" || (receiver && !receiver!.IsDead() && Vector3.Distance(player.transform.position, receiver.transform.position) <= 2.5f),
            (item, count) =>
            {
                int before = item.m_stack;
                player.DropItem(bag, item, count);
                int removed = before - (bag.ContainsItem(item) ? item.m_stack : 0);
                if (removed > 0) contents.Add(Observation.Name(item.m_shared.m_name) + " ×" + removed);
                return removed; // Actual inventory delta is authoritative.
            });
        CompletedAmount = batch.Removed;
        if (!delivered)
        { Finish("Передача прервана: предметы изменились, получатель отошёл или игра отклонила сброс. Подтверждено " + batch.Removed + "; остаток в инвентаре NPC", false); return; }
        int removedTotal = batch.Removed, stacks = batch.StackCount;
        player.GetComponent<CompanionNpc>()?.Save();
        string summary = string.Join(", ", contents.Take(4));
        if (contents.Count > 4) summary += "; всего стопок " + stacks;
        Finish((d.action == "give" ? "Рядом с " + transferRecipient + " оставлено: " : "Сброшено: ") + summary + ". Всего " + removedTotal + "." + (d.selection == "all_inventory" && bag.GetAllItems().Any(i => i.m_shared.m_questItem) ? " Квестовые предметы сохранены." : ""));
    }
}
