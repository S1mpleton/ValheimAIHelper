using System;
using System.Collections.Generic;
using System.Linq;
using UnityEngine;

namespace ValheimAIHelper;

internal sealed partial class ActionExecutor
{
    private readonly CollectibleReceipt collectibleReceipt = new();
    private Component? collectibleTarget;
    private Vector3 collectiblePosition;
    private string[] collectiblePrefabs = Array.Empty<string>();
    private HashSet<int> collectibleBefore = new();
    private int collectibleBagBefore;

    private void ResetCollectible()
    {
        collectibleReceipt.Reset(); collectibleTarget = null;
        collectibleBefore.Clear(); collectiblePrefabs = Array.Empty<string>();
    }
    private int CollectibleBagCount(Player player) => player.GetInventory().GetAllItems()
        .Where(i => collectiblePrefabs.Contains(ItemCatalog.Prefab(i))).Sum(i => i.m_stack);

    private void Collect(Player player, Component target)
    {
        if (collectibleReceipt.TryIssue(Time.time))
        {
            collectibleTarget = target; collectiblePosition = target.transform.position;
            collectiblePrefabs = ItemCatalog.Produces(target);
            collectibleBefore = new HashSet<int>(Observation.LoadedDrops().Where(d => d).Select(d => d.GetInstanceID()));
            collectibleBagBefore = CollectibleBagCount(player);
            // Interact's return value is an animation flag, not a receipt.
            ((Interactable)target).Interact(player, false, false);
        }
        CheckCollectible(player);
    }

    private void CheckCollectible(Player player)
    {
        bool bagChanged = CollectibleBagCount(player) > collectibleBagBefore;
        bool picked = !collectibleTarget || NativeAccess.Read(collectibleTarget,"m_picked") is true;
        bool spawned = Observation.LoadedDrops().Any(d => d && !collectibleBefore.Contains(d.GetInstanceID()) &&
            Vector3.Distance(d.transform.position,collectiblePosition) <= 3 && d.m_itemData != null &&
            collectiblePrefabs.Contains(ItemCatalog.Prefab(d.m_itemData)));
        switch (collectibleReceipt.Observe(Time.time,bagChanged || picked || spawned))
        {
            case CollectibleResult.Confirmed:
                Finish(bagChanged ? "Сбор подтверждён: инвентарь пополнился" : "Сбор подтверждён; появившиеся вещи подбираются действием pickup");
                return;
            case CollectibleResult.TimedOut:
                Finish("Игра не подтвердила сбор предмета; повторный запрос не отправлялся",false);
                return;
            default: Result = "Ожидаю подтверждение сбора предмета от игры"; return;
        }
    }
}
