using System;
using System.Linq;
using HarmonyLib;
using UnityEngine;

namespace ValheimAIHelper;

internal sealed partial class ActionExecutor
{
    private readonly NativeInventorySession inventorySession = new();
    private int actionStage, baseCount, baseQuality, uses, lastUseStack, craftsCompleted;
    private float actionTime, nextUse;
    private float repairBefore = -1, repairAttemptTime;
    private Vector2i craftPosition;
    private bool placementPulse, placementDone;
    private string placementFailure = "";
    public bool OwnsInventory => inventorySession.Owned;
    private void ResetExtended(Decision value)
    {
        if (!new[] { "open", "close", "take", "deposit", "stack", "craft", "upgrade", "repair", "wait" }.Contains(value.action)) inventorySession.Close();
        actionStage = uses = craftsCompleted = 0; actionTime = Time.realtimeSinceStartup; nextUse = 0;
        placementPulse = placementDone = false; placementFailure = "";
        repairBefore = -1;
    }
    private bool ExtendedTick(Player player, Player? leader, bool combat)
    {
        var d = decision!; var o = observation!;
        switch (d.action)
        {
            case "drop":
                if (!o.Items.TryGetValue(d.target_id, out var dropped) || !player.GetInventory().ContainsItem(dropped) || d.amount < 1 || d.amount > dropped.m_stack)
                { Finish("Неверное количество или предмет уже отсутствует", false); return true; }
                bool droppedOk = player.DropItem(player.GetInventory(), dropped, d.amount);
                Finish(droppedOk ? "Выброшено: " + d.amount : "Игра запретила сброс предмета", droppedOk); return true;
            case "unequip":
                if (!o.Items.TryGetValue(d.target_id, out var unequip) || !player.GetInventory().ContainsItem(unequip)) { Finish("Предмет отсутствует", false); return true; }
                if (!unequip.m_equipped) { Finish("Предмет снят"); return true; }
                if (actionStage++ == 0) player.UseItem(player.GetInventory(), unequip, true);
                if (Time.realtimeSinceStartup - actionTime > 8) Finish("Игра не подтвердила снятие предмета", false);
                return true;
            case "hide_weapon": player.HideHandItems(); Finish("Оружие убрано"); return true;
            case "show_weapon": player.ShowHandItems(); Finish("Оружие достали"); return true;
            case "sneak": case "unsneak":
                bool crouch = d.action == "sneak";
                if (player.IsCrouching() == crouch) Finish(crouch ? "Крадусь" : "Красться закончил");
                else Controls(Vector3.zero, crouch: true);
                return true;
            case "stand": player.StopEmote(); if (player.IsAttached()) NativeAccess.Call(player, "AttachStop"); Finish("Встал"); return true;
            case "rest":
                if (!player.IsSitting() && !player.StartEmote("sit", false)) { Finish("Нельзя сесть сейчас", false); return true; }
                if (o.Data.actor.rested) Finish("Отдых восстановлен");
                else if (Time.realtimeSinceStartup - actionTime > 21) Finish("Отдых не получен: нужны безопасные условия у огня", false);
                return true;
            case "guardian_power": bool power = player.StartGuardianPower(); Finish(power ? "Сила Хранителя активируется" : "Сила недоступна или восстанавливается", power); return true;
            case "emote": bool emote = player.StartEmote(d.text, d.text != "sit"); Finish(emote ? "Жест: " + d.text : "Игра отклонила жест", emote); return true;
            case "speak": bool spoke = Plugin.SayFromAction(d.text); Finish(spoke ? "Сообщение передано игровому чату" : "Игровой чат недоступен", spoke); return true;
            case "look":
                if (d.destination == null) { Finish("Не задана точка взгляда", false); return true; }
                var aimPoint = new Vector3(d.destination.x, d.destination.y, d.destination.z) - player.GetEyePoint();
                Controls(Vector3.zero, aimPoint); if (Time.realtimeSinceStartup - actionTime > .4f) Finish("Осмотрел точку"); return true;
            case "hunt":
                if (!combat || !o.Characters.TryGetValue(d.target_id, out var animal) || !Observation.IsWildAnimal(animal)) { Finish("Дичь недоступна", false); return true; }
                Fight(player, animal, leader, false); return true;
            case "secondary_attack":
                if (!combat || !o.Characters.TryGetValue(d.target_id, out var enemy) || !Observation.IsEnemy(player, enemy, leader)) { Finish("Враг недоступен", false); return true; }
                if (Vector3.Distance(player.transform.position, enemy.transform.position) > 2.5f) MoveTo(player, enemy.transform.position, 2);
                else if (Time.time >= nextAttack && player.GetStamina() > 20)
                { Controls(Vector3.zero, enemy.GetCenterPoint() - player.GetEyePoint(), secondary: true); nextAttack = Time.time + 1.2f; }
                return true;
            case "gather": Gather(player); return true;
            case "use_on": UseOn(player); return true;
            case "open": case "take": case "deposit": case "stack": ContainerAction(player); return true;
            case "close": inventorySession.Close(); Finish("Контейнер закрыт"); return true;
            case "craft": case "upgrade": CraftAction(player); return true;
            case "repair": RepairEquipment(player); return true;
            case "build": case "repair_building": Placement(player); return true;
            case "sleep":
                if (player.IsSleeping()) { Finish("Персонаж спит"); return true; }
                if (!o.Interactables.TryGetValue(d.target_id, out var bedObject) || !(bedObject is Bed bed) || !bed.IsMine()) { Finish("Это не кровать данного персонажа", false); return true; }
                if (Vector3.Distance(player.transform.position, bed.transform.position) > 2) MoveTo(player, bed.transform.position, 1.5f);
                else if (Time.time >= nextUse) { bed.Interact(player, false, false); nextUse = Time.time + 2; }
                if (Time.realtimeSinceStartup - actionTime > 6) Finish("Игра не разрешила сон: проверьте время, огонь, укрытие и врагов", false);
                return true;
            default: return false;
        }
    }
    private void Gather(Player player)
    {
        var d = decision!;
        if (!observation!.Resources.TryGetValue(d.target_id, out var resource) || !resource) { Finish("Ресурс добыт или исчез"); return; }
        var weapon = player.GetCurrentWeapon();
        bool mining = resource.GetType().Name.StartsWith("MineRock");
        bool tool = weapon != null && (mining ? weapon.m_shared.m_skillType == Skills.SkillType.Pickaxes : weapon.m_shared.m_skillType == Skills.SkillType.Axes);
        int tier = NativeAccess.Read(resource, "m_minToolTier") is int required ? required : 0;
        if (!tool || weapon!.m_shared.m_toolTier < tier || weapon.m_durability <= 0) { Finish("Нужен исправный подходящий топор/кирка требуемого уровня", false); return; }
        var collider = resource.GetComponentsInChildren<Collider>().OrderBy(c => Vector3.SqrMagnitude(c.ClosestPoint(player.GetCenterPoint()) - player.GetCenterPoint())).FirstOrDefault();
        var point = collider ? collider!.ClosestPoint(player.GetCenterPoint()) : resource.transform.position;
        if (Vector3.Distance(player.transform.position, point) > 2.5f) { MoveTo(player, point, 1.7f); return; }
        if (player.GetStamina() < 15) { Result = "Восстанавливаю выносливость перед добычей"; return; }
        bool attack = Time.time >= nextAttack && !player.InAttack();
        Controls(Vector3.zero, point - player.GetEyePoint(), attack: attack);
        if (attack) nextAttack = Time.time + .9f;
        Result = mining ? "Добываю руду/камень штатным ударом кирки" : "Рублю дерево штатным ударом топора";
    }
    private void UseOn(Player player)
    {
        var d = decision!; var o = observation!;
        if (!o.Interactables.TryGetValue(d.target_id, out var target) || !target || !(target is Interactable interactable)) { Finish("Объект недоступен", false); return; }
        if (!o.Items.TryGetValue(d.item_id, out var item) || !player.GetInventory().ContainsItem(item))
        { Finish(actionStage > 0 ? "Предмет передан обработчику игры" : "Предмет недоступен", actionStage > 0); return; }
        if (Vector3.Distance(player.transform.position, target.transform.position) > 2.5f) { MoveTo(player, target.transform.position, 1.8f); return; }
        if (actionStage == 0) { baseCount = item.m_stack; lastUseStack = item.m_stack; actionStage = 1; }
        uses = baseCount - item.m_stack;
        if (uses >= d.amount || !player.GetInventory().ContainsItem(item)) { Finish("Предмет использован: " + uses); return; }
        if (actionStage == 2 && item.m_stack == lastUseStack)
        { if (Time.time >= nextUse + 3) Finish("Игра не подтвердила использование предмета", false); return; }
        if (Time.time < nextUse) return;
        nextUse = Time.time + .8f;
        lastUseStack = item.m_stack;
        bool accepted; bool oldScope = NativeInputScope.Active, oldPlace = NativeInputScope.Place;
        try { NativeInputScope.Active = true; NativeInputScope.Place = false; accepted = interactable.UseItem(player, item); }
        finally { NativeInputScope.Active = oldScope; NativeInputScope.Place = oldPlace; }
        if (!accepted) { Finish("Игра не принимает этот предмет на выбранном объекте", false); return; }
        actionStage = 2; uses = baseCount - item.m_stack;
        if (!player.GetInventory().ContainsItem(item) || uses >= d.amount) Finish("Предмет передан штатному обработчику: " + uses);
        else if (Time.realtimeSinceStartup - actionTime > 8) Finish("Обработчик ответил; изменение инвентаря не подтверждено", false);
    }
    private void ContainerAction(Player player)
    {
        var d = decision!; var o = observation!;
        if (!o.Interactables.TryGetValue(d.target_id, out var target) || !(target is Container container) || !container) { Finish("Контейнер недоступен", false); return; }
        if (Vector3.Distance(player.transform.position, container.transform.position) > 2.5f) { MoveTo(player, container.transform.position, 1.8f); return; }
        if (actionStage++ == 0) inventorySession.Open(player, container);
        if (!NativeInventorySession.HasAccess(player, container))
        { if (Time.realtimeSinceStartup - actionTime > 5) { inventorySession.Close(); Finish("Игра не предоставила доступ/владение контейнером", false); } return; }
        if (d.action == "open") { Finish("Контейнер открыт; содержимое доступно в следующем наблюдении"); return; }
        if (d.action == "stack") { NativeAccess.Call(InventoryGui.instance, "OnStackAll"); Finish("Сложены совпадающие стопки через интерфейс игры"); return; }
        var items = d.action == "take" ? o.ContainerItems : o.Items;
        if (!items.TryGetValue(d.item_id, out var item)) { Finish("Выбранный предмет отсутствует", false); return; }
        int moved = NativeInventorySession.Transfer(player, container, item, d.amount, d.action == "deposit");
        Finish("Перемещено " + moved + " из " + d.amount, moved == d.amount);
    }
    private void CraftAction(Player player)
    {
        var d = decision!; var o = observation!;
        if (!o.Recipes.TryGetValue(d.target_id, out var recipe)) { Finish("Рецепт больше недоступен", false); return; }
        ItemDrop.ItemData? upgrade = null;
        if (d.action == "upgrade")
        {
            o.Items.TryGetValue(d.item_id, out upgrade);
            if (upgrade == null && actionStage > 0) upgrade = usedItem;
            if (upgrade == null || (actionStage == 0 && !player.GetInventory().ContainsItem(upgrade))) { Finish("Предмет улучшения отсутствует", false); return; }
        }
        string name = recipe.m_item.m_itemData.m_shared.m_name;
        int quality = upgrade != null ? upgrade.m_quality + 1 : 1;
        if (actionStage == 0)
        {
            if (!player.HaveRequirements(recipe, false, quality)) { Finish("Игра не разрешает крафт: нужны материалы/уровень станции", false); return; }
            baseCount = player.GetInventory().CountItems(name); baseQuality = upgrade?.m_quality ?? 0;
            usedItem = upgrade; craftPosition = upgrade?.m_gridPos ?? new Vector2i(-1, -1);
            if (!inventorySession.Craft(player, recipe, upgrade)) { Finish("Кнопка крафта недоступна: проверьте станцию, ресурсы и место", false); return; }
            actionStage = 1;
        }
        else if (CraftComplete(player, name, upgrade != null)) CompleteCraftOperation(upgrade != null);
        else if (Time.realtimeSinceStartup - actionTime > Math.Min(55, d.amount * 5 + 5)) { inventorySession.Close(); Finish("Игра не подтвердила результат крафта", false); }
    }
    private bool CraftComplete(Player player, string name, bool upgrade) => NativeAccess.Read(InventoryGui.instance, "m_craftTimer") is float timer && timer < 0 && (upgrade
        ? player.GetInventory().GetAllItems().Any(i => i.m_shared.m_name == name && i.m_quality > baseQuality && i.m_gridPos == craftPosition)
        : player.GetInventory().CountItems(name) > baseCount);
    private void CompleteCraftOperation(bool upgrade)
    {
        craftsCompleted++;
        if (upgrade || craftsCompleted >= decision!.amount) Finish(upgrade ? "Предмет улучшен игрой" : "Крафт подтверждён: операций " + craftsCompleted);
        else { actionStage = 0; Result = "Крафт: выполнено " + craftsCompleted + " из " + decision.amount; }
    }
    public void RefreshOutcomes(Player player)
    {
        if (decision == null || ActionStatus != "running") return;
        if ((decision.action == "build" || decision.action == "repair_building") && placementDone)
            Finish(decision.action == "build" ? "Игра подтвердила размещение с расходом ресурсов" : "Игра подтвердила ремонт строения");
        if ((decision.action == "craft" || decision.action == "upgrade") && actionStage > 0 && observation!.Recipes.TryGetValue(decision.target_id, out var recipe) &&
            CraftComplete(player, recipe.m_item.m_itemData.m_shared.m_name, decision.action == "upgrade"))
            CompleteCraftOperation(decision.action == "upgrade");
        if (usedItem != null && new[] { "equip", "eat", "use" }.Contains(decision.action))
        {
            bool done = decision.action == "equip" ? usedItem.m_equipped : usedItem.m_stack < oldStack || !player.GetInventory().ContainsItem(usedItem);
            if (done) Finish("Игра подтвердила использование предмета");
        }
    }
    private void RepairEquipment(Player player)
    {
        if (!observation!.Interactables.TryGetValue(decision!.target_id, out var target) || !(target is CraftingStation station)) { Finish("Станция недоступна", false); return; }
        if (Vector3.Distance(player.transform.position, station.transform.position) > 2.5f) { MoveTo(player, station.transform.position, 1.8f); return; }
        if (actionStage++ == 0) { inventorySession.AdoptStation(); station.Interact(player, false, false); }
        if (!InventoryGui.IsVisible() || player.GetCurrentCraftingStation() != station || !station.CheckUsable(player, false))
        { if (Time.realtimeSinceStartup - actionTime > 5) { inventorySession.Close(); Finish("Станция не готова: укрытие/огонь/доступ", false); } return; }
        if (Time.time < nextUse) return; nextUse = Time.time + .3f;
        if (NativeAccess.Call(InventoryGui.instance, "HaveRepairableItems") is not true) { inventorySession.Close(); Finish("Все доступные этой станции предметы отремонтированы"); return; }
        NativeAccess.Call(InventoryGui.instance, "OnRepairPressed");
    }
    private void Placement(Player player)
    {
        var d = decision!;
        if (d.action == "repair_building" && repairBefore >= 0)
        {
            var target = observation!.Interactables.TryGetValue(d.target_id, out var value) ? value : null;
            var wear = target ? target!.GetComponent<WearNTear>() : null;
            float after = wear ? (float)(NativeAccess.Call(wear!, "GetHealthPercentage") ?? -1f) : -1;
            if (after > repairBefore || after >= .999f) placementDone = true;
            else if (Time.realtimeSinceStartup - repairAttemptTime > 3) placementFailure = "ремонт не подтверждён после сетевого ответа";
            else return;
        }
        if (placementDone) { Finish(d.action == "repair_building" ? "Игра подтвердила ремонт строения" : "Игра подтвердила размещение с расходом ресурсов"); return; }
        if (placementFailure.Length > 0) { Finish("Размещение отклонено игрой: " + placementFailure, false); return; }
        Piece? selected; Vector3 point;
        if (d.action == "build")
        {
            if (!observation!.Pieces.TryGetValue(d.target_id, out selected) || d.destination == null) { Finish("Неизвестная деталь/точка строительства", false); return; }
            point = new Vector3(d.destination.x, d.destination.y, d.destination.z);
        }
        else
        {
            if (!observation!.Interactables.TryGetValue(d.target_id, out var target) || !(target is Piece piece)) { Finish("Строение недоступно", false); return; }
            point = piece.transform.position;
            selected = player.GetRightItem()?.m_shared.m_buildPieces?.m_pieces.Select(g => g.GetComponent<Piece>()).FirstOrDefault(p => p && p.m_repairPiece);
        }
        if (!selected || !player.InPlaceMode()) { Finish("Экипируйте молот/культиватор; нужный режим инструмента недоступен", false); return; }
        if (Vector3.Distance(player.transform.position, point) > 5) { MoveTo(player, point, 3.5f); return; }
        if (actionStage++ == 0 && !player.SetSelectedPiece(selected!)) { Finish("Игра не разрешила выбрать эту деталь", false); return; }
        AccessTools.Field(typeof(Player), "m_placeRotation")?.SetValue(player, d.rotation);
        var camera = Utils.GetMainCamera();
        // Native preview uses a camera ray. Aim the actual view, then verify its
        // resulting ghost/hover before issuing one native placement input.
        var aim = point - (camera ? camera!.transform.position : player.GetEyePoint());
        Controls(Vector3.zero, aim);
        if (Time.realtimeSinceStartup - actionTime < .6f) return;
        bool correct;
        if (d.action == "build")
        {
            var ghost = NativeAccess.Read(player, "m_placementGhost") as GameObject;
            correct = ghost && Vector3.Distance(ghost!.transform.position, point) < 1.2f;
        }
        else correct = NativeAccess.Call(player, "GetHoveringPiece") as Piece == observation!.Interactables[d.target_id];
        if (correct && Time.time >= nextUse)
        { placementPulse = true; nextUse = Time.time + 1; }
        if (Time.realtimeSinceStartup - actionTime > 8) Finish("Игра не подтвердила размещение: проверьте предпросмотр, ресурсы и выносливость", false);
    }
    public bool PlacementInput => placementPulse;
    public void EndPlacementInput() { placementPulse = false; }
    public void RepairAttempt(float health) { if (placementPulse) { repairBefore = health; repairAttemptTime = Time.realtimeSinceStartup; } }
    public void PlacementResult(bool success, Player player)
    {
        if (!placementPulse) return;
        placementDone = success;
        if (!success) placementFailure = NativeAccess.Read(player, "m_placementStatus")?.ToString() ?? "проверка размещения";
    }
}
