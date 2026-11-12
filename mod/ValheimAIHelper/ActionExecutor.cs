using System;
using System.Collections.Generic;
using System.Linq;
using UnityEngine;

namespace ValheimAIHelper;

// Native input/UseItem/Interact only. No position, vitals, ownership or inventory writes.
internal sealed partial class ActionExecutor
{
    private Decision? decision;
    private Observation? observation;
    private float expires, nextPath, nextAttack, nextJump, nextSurvival, lastProgress, bowStarted;
    private Vector3 lastPosition, moveTarget;
    private readonly List<Vector3> path = new();
    private ItemDrop.ItemData? usedItem;
    private int oldStack;
    private float usedAt;
    public string Result { get; private set; } = "Ожидание";
    public string PlanId { get; private set; } = "";
    public string ActionStatus { get; private set; } = "";
    public ControlIntent Input { get; private set; }

    public void Set(Decision value, Observation from, Player player)
    {
        bool same = decision != null && (value.plan_id ?? "") == PlanId && decision.action == value.action && decision.target_id == value.target_id && decision.source == value.source && decision.mode == value.mode;
        observation = from;
        expires = Time.realtimeSinceStartup + value.duration;
        if (same) { decision = value; return; } // Renew policy/flags while preserving execution state.
        ResetExtended(value);
        decision = value;
        PlanId = value.plan_id ?? "";
        ActionStatus = "running";
        nextPath = 0; bowStarted = 0; usedItem = null;
        lastProgress = Time.time;
        lastPosition = player.transform.position;
        var forward = player.transform.forward; forward.y = 0;
        moveTarget = value.destination != null ? new Vector3(value.destination.x, value.destination.y, value.destination.z) : lastPosition + forward.normalized * 4;
        path.Clear();
        Result = value.say.Length > 0 ? value.say : value.action;
    }

    public void Stop(Player? player, string reason)
    {
        decision = null; observation = null; PlanId = ""; ActionStatus = "";
        inventorySession.Close(); placementPulse = false;
        path.Clear(); bowStarted = 0; usedItem = null;
        Result = reason; Input = default;
    }

    private void Finish(string result, bool success = true)
    { Result = result; ActionStatus = success ? "success" : "blocked"; Input = default; }
    public void Pause() { Input = default; lastProgress = Time.time; nextPath = 0; }

    public ControlIntent ConsumeInput()
    {
        var value = Input;
        Input = ControlIntent.Create(value.X * value.Magnitude, 0, value.Z * value.Magnitude, false)
            .With(value.AimX, value.AimY, value.AimZ, hold: value.AttackHold, block: value.Block, run: value.Run);
        return value;
    }

    private void Controls(Vector3 movement, Vector3 aim = default, bool attack = false, bool hold = false,
        bool block = false, bool jump = false, bool run = false, bool dodge = false, bool secondary = false, bool crouch = false)
    { Input = ControlIntent.Create(movement.x, movement.y, movement.z, attack).With(aim.x, aim.y, aim.z, hold, block, jump, run, dodge, secondary, crouch); }

    public void Tick(Player player, Player? leader, bool allowCombat, bool survival)
    {
        Input = default;
        if (decision == null || observation == null) return;
        if (Time.realtimeSinceStartup >= expires) { Finish("Нет свежих данных от службы; управление приостановлено", false); return; }
        if (!player.IsOwner() || player != Player.m_localPlayer || player.IsDead() || player.IsTeleporting() ||
            (player.IsAttached() && decision.action != "stand" && decision.action != "sleep") ||
            (player.IsSleeping() && decision.action != "sleep"))
        { Finish("Действие недоступно в текущем состоянии", false); return; }
        if (decision.mode == "stop" || (string.IsNullOrEmpty(decision.mode) && decision.action == "wait" && decision.source == "local")) return;
        if (decision.avoid_combat)
        {
            allowCombat = false;
            var danger = Character.GetAllCharacters().Where(c => Observation.IsEnemy(player, c, leader) && c.GetBaseAI()?.GetTargetCreature() == player &&
                Vector3.Distance(c.transform.position, player.transform.position) < 5).FirstOrDefault();
            if (danger) { inventorySession.Close(); Retreat(player, danger, leader); return; }
        }
        if (survival && !new[] { "eat", "use", "drop", "deposit", "use_on", "craft", "upgrade" }.Contains(decision.action)) PrepareSurvival(player);
        if (allowCombat && new[] { "follow", "wait", "move", "pickup", "interact", "gather", "rest", "open", "take", "deposit", "craft", "repair", "build", "repair_building" }.Contains(decision.action))
        {
            var threat = Character.GetAllCharacters().Where(c => Observation.IsEnemy(player, c, leader))
                .Where(c => { var victim = c.GetBaseAI()?.GetTargetCreature(); return victim == player || (leader && victim == leader); })
                .Where(c => Vector3.Distance(player.transform.position, c.transform.position) <= 8)
                .OrderBy(c => Vector3.SqrMagnitude(player.transform.position - c.transform.position)).FirstOrDefault();
            if (threat)
            {
                if (OwnsInventory) { inventorySession.Close(); Finish("Работа прервана: защищаюсь от врага", false); }
                if (player.InPlaceMode())
                {
                    Finish("Строительство прервано опасностью", false);
                    var combatWeapon = player.GetInventory().GetAllItems().Where(i => i.m_stack > 0 && i.m_shared.m_buildPieces == null &&
                        i.GetDamage().GetTotalDamage() > 0 && i.IsEquipable() && (!i.m_shared.m_useDurability || i.m_durability > 0))
                        .OrderByDescending(i => i.GetDamage().GetTotalDamage()).FirstOrDefault();
                    if (Time.time >= nextSurvival)
                    { if (combatWeapon != null) player.UseItem(player.GetInventory(), combatWeapon, true); else player.HideHandItems(); nextSurvival = Time.time + 2; }
                    return;
                }
                Fight(player, threat, leader, true); return;
            }
        }
        if (ActionStatus != "running") return;
        if (ExtendedTick(player, leader, allowCombat)) return;
        string id = decision.target_id;
        switch (decision.action)
        {
            case "wait": return;
            case "move": case "jump":
                MoveTo(player, moveTarget, .8f, decision.run);
                if (decision.action == "jump" && ActionStatus == "running" && Time.time >= nextJump && player.IsOnGround() && player.GetStamina() > 20)
                { Input = Input.With(jump: true, run: Input.Run); nextJump = Time.time + 1.5f; }
                return;
            case "follow":
                if (leader == null || !leader || leader.IsDead() || Observation.Id(leader) != id) { Finish("Лидер не виден", false); return; }
                MoveTo(player, leader.transform.position, 3, Vector3.Distance(player.transform.position, leader.transform.position) > 8);
                if (ActionStatus == "success") ActionStatus = "running";
                return;
            case "pickup":
                PickupItem(player);
                return;
            case "interact":
                if (!observation.Interactables.TryGetValue(id, out var target) || !target) { Finish("Объект недоступен", false); return; }
                if (Vector3.Distance(player.transform.position, target.transform.position) > 2) MoveTo(player, target.transform.position, 1.5f);
                else
                {
                    bool ok = target is Interactable interactable && interactable.Interact(player, false, false);
                    Finish(ok ? "Объект использован" : "Игра отклонила взаимодействие", ok);
                }
                return;
            case "eat": case "equip": case "use": UseInventory(player, id); return;
            case "attack": case "defend": case "flee": case "dodge":
                if (!observation.Characters.TryGetValue(id, out var enemy) || !Observation.IsEnemy(player, enemy, leader))
                { Finish("Враг побеждён или больше не виден"); return; }
                if (decision.action == "attack" && !allowCombat) { Finish("Бой выключен в настройках помощника", false); return; }
                if (decision.action == "flee") Retreat(player, enemy, leader);
                else if (decision.action == "defend" || decision.action == "dodge")
                {
                    var away = player.transform.position - enemy.transform.position; away.y = 0;
                    bool evade = decision.action == "dodge" && Time.time >= nextJump && player.GetStamina() > 20;
                    Controls(evade ? away.normalized : Vector3.zero, -away, block: true, jump: evade, dodge: evade);
                    if (evade) { nextJump = Time.time + 2; Finish("Уклонение передано игре"); Controls(away.normalized, -away, block: true, jump: true, dodge: true); }
                }
                else Fight(player, enemy, leader, false);
                return;
            default: Finish("Неизвестное действие", false); return;
        }
    }

    private void UseInventory(Player player, string id)
    {
        if (usedItem != null)
        {
            bool done = decision!.action == "equip" ? usedItem.m_equipped : usedItem.m_stack < oldStack || !player.GetInventory().ContainsItem(usedItem);
            if (done) Finish("Игра подтвердила использование предмета");
            else if (Time.time - usedAt > 8) Finish("Игра не подтвердила использование предмета", false);
            return;
        }
        if (!observation!.Items.TryGetValue(id, out var item) || !player.GetInventory().ContainsItem(item)) { Finish("Предмет отсутствует", false); return; }
        if (decision!.action == "equip" && item.m_equipped) { Finish("Предмет уже экипирован"); return; }
        bool valid = decision.action == "equip" ? item.IsEquipable() : decision.action == "eat" ? item.m_shared.m_food > 0 && player.CanEat(item, false) : item.m_shared.m_itemType == ItemDrop.ItemData.ItemType.Consumable;
        if (!valid) { Finish("Игра не разрешает использовать этот предмет", false); return; }
        usedItem = item; oldStack = item.m_stack; usedAt = Time.time;
        player.UseItem(player.GetInventory(), item, true);
    }

    private void PrepareSurvival(Player player)
    {
        if (Time.time < nextSurvival || player.InAttack() || player.IsSwimming()) return;
        nextSurvival = Time.time + 3;
        var food = player.GetInventory().GetAllItems().Where(i => i.m_stack > 0 && i.m_shared.m_food > 0 && player.CanEat(i, false))
            .OrderByDescending(i => i.m_shared.m_food + i.m_shared.m_foodStamina).FirstOrDefault();
        if (food != null) { player.UseItem(player.GetInventory(), food, true); Result = "Выживание: использую доступную еду"; }
    }

    private void Fight(Player player, Character enemy, Player? leader, bool reflex)
    {
        var direction = enemy.transform.position - player.transform.position;
        float distance = direction.magnitude;
        if (distance > 20) { if (!reflex) Finish("Враг слишком далеко", false); return; }
        if (player.GetHealth() < player.GetMaxHealth() * .3f) { Retreat(player, enemy, leader); return; }
        if (player.GetStamina() < 12)
        { Controls(distance < 3 ? -new Vector3(direction.x, 0, direction.z).normalized * .4f : Vector3.zero, direction, block: true); Result = "Восстанавливаю выносливость, держу защиту"; return; }
        var weapon = player.GetCurrentWeapon();
        if (weapon != null && weapon.m_shared.m_useDurability && weapon.m_durability <= 0)
        { Result = "Оружие сломано; отступаю"; Retreat(player, enemy, leader); return; }
        bool bow = weapon?.m_shared.m_attack.m_bowDraw == true;
        bool ranged = bow || weapon?.m_shared.m_animationState == ItemDrop.ItemData.AnimationState.Crossbow ||
                      weapon?.m_shared.m_animationState == ItemDrop.ItemData.AnimationState.Staves || weapon?.m_shared.m_animationState == ItemDrop.ItemData.AnimationState.MagicItem;
        var aim = enemy.GetCenterPoint() - player.GetEyePoint();
        float range = ranged ? 15 : Mathf.Clamp((weapon?.m_shared.m_attack.m_attackRange ?? 1.5f) + enemy.GetRadius(), 1.7f, 4);
        if (distance > range) { MoveTo(player, enemy.transform.position, range - .2f); return; }
        if (ranged && Physics.Raycast(player.GetEyePoint(), aim.normalized, out var hit, aim.magnitude, Physics.DefaultRaycastLayers, QueryTriggerInteraction.Ignore) &&
            hit.collider.GetComponentInParent<Character>() != enemy)
        { bowStarted = 0; MoveTo(player, enemy.transform.position, 2); Result = "Линия огня закрыта; меняю позицию"; return; }
        if (enemy.InAttack() && distance < 3 && !bow)
        { Controls(Vector3.zero, aim, block: true); Result = "Защищаюсь от удара"; return; }
        bool attack = Time.time >= nextAttack && !player.InAttack();
        bool hold = false;
        if (bow)
        {
            if (bowStarted <= 0) bowStarted = Time.time;
            hold = Time.time - bowStarted < 1.8f; attack = false;
            if (!hold && Time.time - bowStarted > 2.2f) bowStarted = 0;
        }
        Controls(Vector3.zero, aim, attack, hold);
        if (attack) nextAttack = Time.time + .65f;
        Result = reflex ? "Защищаю себя и лидера: " + enemy.GetHoverName() : "Атакую: " + enemy.GetHoverName();
    }

    private void Retreat(Player player, Character enemy, Player? leader)
    {
        var away = player.transform.position - enemy.transform.position; away.y = 0;
        var target = player.transform.position + away.normalized * 6;
        if (leader != null && leader && Vector3.Distance(leader.transform.position, enemy.transform.position) > 5) target = leader.transform.position;
        MoveTo(player, target, 1, true);
        var i = Input;
        Controls(new Vector3(i.X, 0, i.Z) * i.Magnitude, -away, block: !i.Run, run: i.Run);
        Result = "Отступаю: мало здоровья";
    }

    private void MoveTo(Player player, Vector3 target, float stopDistance, bool run = false, float maxDistance = 60)
    {
        var origin = player.transform.position;
        float distance = Vector3.Distance(origin, target);
        if (distance <= stopDistance) { lastProgress = Time.time; lastPosition = origin; Finish("Точка достигнута"); return; }
        if (player.IsEncumbered()) { Finish("Перегрузка инвентаря", false); return; }
        if (distance > maxDistance || !Pathfinding.instance) { Finish("Нет доступного маршрута", false); return; }
        if (Time.time >= nextPath)
        {
            path.Clear(); nextPath = Time.time + .75f;
            if (!Pathfinding.instance.GetPath(origin, target, path, Pathfinding.AgentType.HumanoidNoSwim, true))
            { Finish("Игра не нашла пеший маршрут", false); return; }
        }
        while (path.Count > 0 && Vector3.Distance(origin, path[0]) < .6f) path.RemoveAt(0);
        if (path.Count == 0) return;
        if (Mathf.Abs(path[0].y - origin.y) > 1.8f) { Finish("Маршрут требует обхода перепада высоты", false); return; }
        if (Vector3.Distance(origin, lastPosition) > .25f) { lastPosition = origin; lastProgress = Time.time; }
        bool jump = Time.time - lastProgress > 1.2f && Time.time >= nextJump && player.IsOnGround() && player.GetStamina() > 25;
        if (jump) nextJump = Time.time + 2;
        if (Time.time - lastProgress > 4) { Finish("Застрял: маршрут заблокирован", false); return; }
        var direction = path[0] - origin; direction.y = 0;
        Controls(direction.normalized, jump: jump, run: run && player.GetStamina() > 30 && !player.IsSwimming());
    }
}
