"""Known UI duties use game skills; free-form human intent stays with the model."""

COMPANION_OBJECTIVE = "Следуй за лидером и помогай ему выжить."
IDLE_OBJECTIVE = "Поручение выполнено. Вернись к лидеру, помогай ему выживать, поддерживай еду, отдых и снаряжение. Ожидай новых поручений; не повторяй завершённый сброс, крафт или ответ."

# These are interventions during a known companion duty. Random forward moves
# and optional gathering are not implementations of continuous following.
COMPANION_ACTIONS = {
    "wait", "follow", "attack", "secondary_attack", "defend", "flee", "dodge",
    "eat", "equip", "unequip", "use", "hide_weapon", "show_weapon", "rest",
    "stand", "guardian_power", "emote", "speak",
}


def companion_duty(objective):
    return objective in (COMPANION_OBJECTIVE, IDLE_OBJECTIVE)


def planning_view(objective, snapshot, pending_chat):
    from .affordances import can_gather
    actions = snapshot.supported_actions
    if companion_duty(objective) and not pending_chat: actions = [a for a in actions if a in COMPANION_ACTIONS]
    if not any(e.kind == "resource" and can_gather(e, snapshot.actor.inventory) for e in snapshot.entities): actions = [a for a in actions if a != "gather"]
    return snapshot.model_copy(update={"supported_actions": actions})
