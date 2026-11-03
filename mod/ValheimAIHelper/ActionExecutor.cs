using System.Collections.Generic;
using System.Linq;
using UnityEngine;

namespace ValheimAIHelper;

internal sealed class ActionExecutor
{
    private Decision? decision;
    private Observation? observation;
    private float expires, nextPath, nextAttack, lastProgress;
    private Vector3 lastPosition;
    private Vector3 moveTarget;
    private readonly List<Vector3> path = new();
    public string Result { get; private set; } = "Ожидание";
    public ControlIntent Input { get; private set; }

    public void Set(Decision value, Observation from, Player player)
    {
        decision = value;
        observation = from;
        expires = Time.realtimeSinceStartup + value.duration;
        nextPath = 0;
        lastProgress = Time.time;
        lastPosition = player.transform.position;
        var forward = player.transform.forward;
        forward.y = 0;
        moveTarget = lastPosition + forward.normalized * 4f;
        path.Clear();
        Result = value.say.Length > 0 ? value.say : value.action;
    }

    public void Stop(Player? player, string reason)
    {
        decision = null;
        observation = null;
        path.Clear();
        Result = reason;
        Input = default;
    }

    private void Controls(Player player, Vector3 worldDirection, bool attack)
    {
        Input = ControlIntent.Create(worldDirection.x, worldDirection.y, worldDirection.z, attack);
    }

    public void Tick(Player player, Player? leader, bool allowCombat)
    {
        Controls(player, Vector3.zero, false);
        if (decision == null || observation == null || Time.realtimeSinceStartup >= expires) return;
        if (!player.IsOwner() || player != Player.m_localPlayer ||
            player.IsDead() || player.IsTeleporting() || player.IsAttached() || player.IsSwimming() ||
            player.IsEncumbered() || player.IsSleeping() || player.InPlaceMode())
        {
            Stop(player, "Действие недоступно в текущем состоянии");
            return;
        }
        string id = decision.target_id;
        switch (decision.action)
        {
            case "wait": return;
            case "move": MoveTo(player, moveTarget, .7f); return;
            case "follow":
                if (leader == null || !leader || leader.IsDead() || Observation.Id(leader) != id) { Stop(player, "Лидер не виден"); return; }
                MoveTo(player, leader.transform.position, 3f);
                return;
            case "pickup":
                if (!observation.Drops.TryGetValue(id, out var drop) || !drop || !drop.CanPickup()) { Stop(player, "Предмет уже недоступен"); return; }
                if (Vector3.Distance(player.transform.position, drop.transform.position) <= 2f)
                {
                    // Normal pickup performs inventory/weight/network checks; never create items.
                    bool picked = player.Pickup(drop.gameObject, false);
                    Stop(player, picked ? "Предмет подобран" : "Игра отклонила подбор");
                }
                else MoveTo(player, drop.transform.position, 1.5f);
                return;
            case "eat":
            case "equip":
                if (!observation.Items.TryGetValue(id, out var item) || !player.GetInventory().GetAllItems().Contains(item))
                { Stop(player, "Предмет отсутствует в инвентаре"); return; }
                if ((decision.action == "eat" && item.m_shared.m_food > 0 && player.CanEat(item, false)) ||
                    (decision.action == "equip" && item.IsEquipable() && !item.m_equipped))
                {
                    // UseItem is the same path as a player using inventory: consumption and
                    // equipment timing are handled by the game, including food removal.
                    player.UseItem(player.GetInventory(), item, false);
                    Stop(player, "Предмет передан штатному обработчику игры");
                }
                else Stop(player, "Игра не разрешает использовать этот предмет");
                return;
            case "attack":
                if (!allowCombat || !observation.Characters.TryGetValue(id, out var enemy) ||
                    !Observation.IsEnemy(player, enemy, leader)) { Stop(player, "Цель боя недоступна"); return; }
                var weapon = player.GetCurrentWeapon();
                if (weapon != null && (weapon.m_shared.m_itemType == ItemDrop.ItemData.ItemType.Bow ||
                    weapon.m_shared.m_animationState == ItemDrop.ItemData.AnimationState.Crossbow ||
                    weapon.m_shared.m_animationState == ItemDrop.ItemData.AnimationState.Staves ||
                    weapon.m_shared.m_animationState == ItemDrop.ItemData.AnimationState.MagicItem))
                { Stop(player, "Дальний бой пока не поддерживается"); return; }
                if (player.GetHealth() < player.GetMaxHealth() * .3f || player.GetStamina() < 15)
                { Stop(player, "Недостаточно здоровья или выносливости для боя"); return; }
                var direction = enemy.transform.position - player.transform.position;
                if (direction.magnitude > 20) { Stop(player, "Враг слишком далеко"); return; }
                if (direction.magnitude <= 2.2f)
                {
                    direction.y = 0;
                    player.SetLookDir(direction.normalized);
                    bool attack = Time.time >= nextAttack && !player.InAttack();
                    Controls(player, Vector3.zero, attack);
                    player.SetLookDir(direction.normalized);
                    if (attack) nextAttack = Time.time + 1;
                }
                else MoveTo(player, enemy.transform.position, 2f);
                return;
            default: Stop(player, "Неизвестное действие"); return;
        }
    }

    private void MoveTo(Player player, Vector3 target, float stopDistance)
    {
        var origin = player.transform.position;
        float distance = Vector3.Distance(origin, target);
        if (distance <= stopDistance) return;
        if (distance > 60 || !Pathfinding.instance) { Stop(player, "Нет доступного маршрута"); return; }
        if (Time.time >= nextPath)
        {
            path.Clear();
            nextPath = Time.time + 1;
            if (!Pathfinding.instance.GetPath(origin, target, path, Pathfinding.AgentType.HumanoidNoSwim, true))
            { Stop(player, "Игра не нашла пеший маршрут; помогите вручную"); return; }
        }
        while (path.Count > 0 && Vector3.Distance(origin, path[0]) < .7f) path.RemoveAt(0);
        if (path.Count == 0) return;
        if (Mathf.Abs(path[0].y - origin.y) > 1.2f) { Stop(player, "Маршрут требует прыжка; помогите вручную"); return; }
        if (Vector3.Distance(origin, lastPosition) > .3f) { lastPosition = origin; lastProgress = Time.time; }
        if (Time.time - lastProgress > 2.5f) { Stop(player, "Персонаж застрял; помогите вручную"); return; }
        var direction = path[0] - origin;
        direction.y = 0;
        Controls(player, direction.normalized, false);
    }
}
