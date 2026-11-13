from enum import Enum
import math

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Vec3(StrictModel):
    x: float
    y: float
    z: float


class InventoryItem(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    name: str = Field(max_length=120)
    count: int = Field(ge=1, le=100000)
    equipped: bool
    food: bool
    equipable: bool
    item_key: str = Field(default="", max_length=160)
    quest: bool = False
    category: str = Field(default="", max_length=40)
    damage: float = Field(default=0, ge=0)
    durability: float = Field(default=1, ge=0, le=1)
    food_health: float = Field(default=0, ge=0)
    food_stamina: float = Field(default=0, ge=0)
    usable: bool = False
    can_eat: bool = True
    weight: float = Field(default=0, ge=0)
    tool_tier: int = Field(default=0, ge=0, le=20)
    effect: str = Field(default="", max_length=120)
    quality: int = Field(default=1, ge=1, le=100)
    max_quality: int = Field(default=1, ge=1, le=100)
    armor: float = Field(default=0, ge=0)
    block_power: float = Field(default=0, ge=0)
    ammo_type: str = Field(default="", max_length=80)
    skill: str = Field(default="", max_length=80)
    damage_types: str = Field(default="", max_length=240)


class Actor(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    name: str = Field(max_length=120)
    position: Vec3
    forward: Vec3 = Field(default_factory=lambda: Vec3(x=0, y=0, z=1))
    health: float = Field(ge=0)
    max_health: float = Field(gt=0)
    stamina: float = Field(ge=0)
    max_stamina: float = Field(default=100, gt=0)
    food_slots: int = Field(default=0, ge=0, le=3)
    weapon: str = Field(default="", max_length=120)
    blocking: bool = False
    swimming: bool = False
    encumbered: bool = False
    biome: str = Field(default="", max_length=80)
    carry_weight: float = Field(default=0, ge=0)
    max_carry_weight: float = Field(default=300, gt=0)
    free_slots: int = Field(default=0, ge=0, le=256)
    comfort: int = Field(default=0, ge=0, le=100)
    sheltered: bool = False
    rested: bool = False
    wet: bool = False
    cold: bool = False
    attached: bool = False
    sitting: bool = False
    eitr: float = Field(default=0, ge=0)
    station_id: str = Field(default="", max_length=100)
    status_effects: list[str] = Field(default_factory=list, max_length=20)
    guardian_power: str = Field(default="", max_length=120)
    guardian_cooldown: float = Field(default=0, ge=0)
    inventory: list[InventoryItem] = Field(default_factory=list, max_length=64)

    @field_validator("health", "stamina", mode="before")
    @classmethod
    def game_vitals(cls, value):
        # Native spending/damage may overshoot zero. Normalise the observation,
        # never the actual game statistic. NaN/Infinity still fail validation.
        if type(value) in (int, float) and math.isfinite(value) and value < 0:
            return 0
        return value

    @field_validator("inventory", mode="before")
    @classmethod
    def empty_stacks(cls, value):
        if isinstance(value, list):
            return [i for i in value if not (
                isinstance(i, dict) and type(i.get("count")) in (int, float) and i["count"] == 0
            )]
        return value


class Entity(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    name: str = Field(max_length=120)
    kind: str = Field(pattern="^(player|enemy|animal|resource|item|interactable)$")
    position: Vec3
    distance: float = Field(ge=0, le=200)
    health: float = Field(default=0, ge=0)
    max_health: float = Field(default=0, ge=0)
    targeting_us: bool = False
    targeting_leader: bool = False
    attacking: bool = False
    category: str = Field(default="", max_length=40)
    state: str = Field(default="", max_length=240)
    tool_tier: int = Field(default=0, ge=0, le=20)
    count: int = Field(default=1, ge=1, le=100000)
    weight: float = Field(default=0, ge=0)
    pickup_allowed: bool = True


class RecipeOption(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    name: str = Field(max_length=120)
    station: str = Field(default="", max_length=120)
    requirements: str = Field(default="", max_length=500)
    craftable: bool = False
    amount: int = Field(default=1, ge=1, le=10000)


class BuildOption(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    name: str = Field(max_length=120)
    requirements: str = Field(default="", max_length=500)
    available: bool = False
    category: str = Field(default="", max_length=80)


class Snapshot(StrictModel):
    protocol_version: int = Field(default=1, ge=1, le=1)
    session_id: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    sequence: int = Field(ge=1)
    actor: Actor
    entities: list[Entity] = Field(default_factory=list, max_length=128)
    ground_scan_radius: float = Field(default=20, ge=0, le=200)
    ground_items_total: int = Field(default=0, ge=0)
    ground_items_truncated: bool = False
    leader_id: str = Field(default="", max_length=100)
    previous_result: str = Field(default="", max_length=240)
    background_planning: bool = False
    listen_chat: bool = True
    plan_id: str = Field(default="", max_length=80)
    action_status: str = Field(default="", pattern="^(|running|success|blocked)$")
    action_completed_amount: int = Field(default=0, ge=0, le=6400000)
    recent_events: list[str] = Field(default_factory=list, max_length=8)
    recipes: list[RecipeOption] = Field(default_factory=list, max_length=24)
    build_options: list[BuildOption] = Field(default_factory=list, max_length=32)
    container_id: str = Field(default="", max_length=100)
    container_inventory: list[InventoryItem] = Field(default_factory=list, max_length=64)
    daylight: bool = True
    weather: str = Field(default="", max_length=80)
    # An old mod never receives an action its executor does not understand.
    supported_actions: list[str] = Field(
        default_factory=lambda: ["wait", "follow", "attack", "pickup", "eat", "equip"],
        max_length=64,
    )

    @model_validator(mode="after")
    def unique_ids(self):
        ids = [e.id for e in self.entities] + [i.id for i in self.actor.inventory] + [i.id for i in self.container_inventory]
        ids += [r.id for r in self.recipes] + [b.id for b in self.build_options]
        if len(ids) != len(set(ids)):
            raise ValueError("Object IDs must be unique within a snapshot")
        if self.leader_id and not any(
            e.id == self.leader_id and e.kind == "player" for e in self.entities
        ):
            raise ValueError("Leader must be an observed player")
        return self


class Action(str, Enum):
    WAIT = "wait"
    FOLLOW = "follow"
    MOVE = "move"
    ATTACK = "attack"
    PICKUP = "pickup"
    EAT = "eat"
    EQUIP = "equip"
    DEFEND = "defend"
    FLEE = "flee"
    DODGE = "dodge"
    JUMP = "jump"
    INTERACT = "interact"
    USE = "use"
    DROP = "drop"
    GIVE = "give"
    UNEQUIP = "unequip"
    HIDE_WEAPON = "hide_weapon"
    SHOW_WEAPON = "show_weapon"
    SNEAK = "sneak"
    UNSNEAK = "unsneak"
    REST = "rest"
    STAND = "stand"
    GUARDIAN_POWER = "guardian_power"
    EMOTE = "emote"
    SPEAK = "speak"
    LOOK = "look"
    SECONDARY_ATTACK = "secondary_attack"
    HUNT = "hunt"
    GATHER = "gather"
    OPEN = "open"
    CLOSE = "close"
    TAKE = "take"
    DEPOSIT = "deposit"
    STACK = "stack"
    CRAFT = "craft"
    UPGRADE = "upgrade"
    REPAIR = "repair"
    BUILD = "build"
    REPAIR_BUILDING = "repair_building"
    USE_ON = "use_on"
    SLEEP = "sleep"


class Decision(StrictModel):
    action: Action
    target_id: str = Field(max_length=100)
    duration: float = Field(ge=0.25, le=3.0)
    say: str = Field(max_length=240)
    destination: Vec3 | None = None
    run: bool = False
    item_id: str = Field(default="", max_length=100)
    selection: str = Field(default="exact", pattern="^(exact|all_matching|all_inventory)$")
    amount: int = Field(default=1, ge=1, le=100000)
    text: str = Field(default="", max_length=220)
    rotation: int = Field(default=0, ge=0, le=15)

    @field_validator("text")
    @classmethod
    def safe_text(cls, value):
        return "".join(c for c in value if c.isprintable()).strip()

    @field_validator("say")
    @classmethod
    def plain_text(cls, value: str) -> str:
        return "".join(c for c in value if c.isprintable()).strip()


class DecisionReply(Decision):
    protocol_version: int = 1
    sequence: int
    source: str
    command_revision: int
    plan_id: str = ""
    mode: str = "stop"
    avoid_combat: bool = False


class ChatIntent(StrictModel):
    message_id: int = Field(ge=0)
    addressed: bool
    mode: str = Field(pattern="^(ignore|task|follow|stop|reply)$")
    objective: str = Field(max_length=1000)
    reply: str = Field(max_length=220)


class Plan(StrictModel):
    """Short executable plan; each step is leased to the mod for at most 3 seconds."""
    steps: list[Decision] = Field(min_length=1, max_length=6)
    chat_intent: ChatIntent | None = None
    combat_policy: str = Field(default="defend", pattern="^(defend|avoid)$")
    objective_complete: bool = False


def wait(reason: str = "") -> Decision:
    return Decision(action=Action.WAIT, target_id="", duration=1, say=reason[:240])


def inventory_status(decision: Decision, snapshot: Snapshot) -> Decision:
    """A planned transfer is not an already completed inventory operation."""
    items = {i.id: i.name for i in [*snapshot.actor.inventory, *snapshot.container_inventory]}
    entities = {e.id: e for e in snapshot.entities}
    action = decision.action
    text = ""
    if action == Action.PICKUP and decision.target_id in entities:
        item = entities[decision.target_id]
        text = f"Подхожу к выпавшей стопке: {item.name} ×{item.count}"
    elif action == Action.GIVE and decision.target_id in entities:
        text = f"Подхожу к {entities[decision.target_id].name}, чтобы передать " + ("все доступные вещи" if decision.selection == "all_inventory" else items.get(decision.item_id, "") + (" — всё количество" if decision.selection == "all_matching" else f" ×{decision.amount}"))
    elif action == Action.DROP:
        text = "Сбрасываю " + ("все доступные вещи" if decision.selection == "all_inventory" else items.get(decision.target_id, "") + (" — всё количество" if decision.selection == "all_matching" else f" ×{decision.amount}"))
    elif action in (Action.TAKE, Action.DEPOSIT):
        text = ("Беру из контейнера " if action == Action.TAKE else "Кладу в контейнер ") + f"{items.get(decision.item_id, '')} ×{decision.amount}"
    return decision.model_copy(update={"say": text[:240]}) if text else decision


def transfer_candidates(decision: Decision, snapshot: Snapshot):
    bag = snapshot.actor.inventory
    if decision.selection == "all_inventory": return [i for i in bag if not i.quest]
    item_id = decision.item_id if decision.action == Action.GIVE else decision.target_id
    chosen = next((i for i in bag if i.id == item_id and not i.quest), None)
    if chosen is None: return []
    return [i for i in bag if not i.quest and (i.id == item_id or chosen.item_key and i.item_key == chosen.item_key)]


def validate_transfer(decision: Decision, snapshot: Snapshot):
    candidates = transfer_candidates(decision, snapshot)
    if not candidates: raise ValueError("No transferable observed inventory items")
    if decision.selection == "exact" and sum(i.count for i in candidates) < decision.amount:
        raise ValueError("Not enough items across matching inventory stacks")


def validate_decision(decision: Decision, snapshot: Snapshot) -> Decision:
    """Never accept invented targets or arbitrary code from a provider."""
    if decision.selection != "exact" and decision.action not in (Action.GIVE, Action.DROP):
        raise ValueError("Bulk selection is only available for give/drop")
    if decision.destination is not None and decision.action not in (Action.MOVE, Action.JUMP, Action.BUILD, Action.LOOK):
        raise ValueError("Only movement can specify a destination")
    if decision.action == Action.WAIT:
        return decision.model_copy(update={"target_id": ""})
    if decision.action.value not in snapshot.supported_actions:
        raise ValueError("This mod does not support the requested action")
    no_target = {Action.HIDE_WEAPON, Action.SHOW_WEAPON, Action.SNEAK, Action.UNSNEAK, Action.REST,
                 Action.STAND, Action.GUARDIAN_POWER, Action.EMOTE, Action.SPEAK, Action.CLOSE}
    if decision.action in no_target:
        if decision.target_id: raise ValueError("This action has no target")
        if decision.action == Action.EMOTE and decision.text not in ("wave", "cheer", "point", "sit", "bow", "challenge", "cry", "nonono", "thumbsup"):
            raise ValueError("Unknown game emote")
        if decision.action == Action.SPEAK and not decision.text: raise ValueError("Empty chat message")
        return decision
    if decision.action == Action.LOOK:
        if decision.destination is None: raise ValueError("Look needs a point")
        a, b = snapshot.actor.position, decision.destination
        if math.dist((a.x,a.y,a.z),(b.x,b.y,b.z)) > 60: raise ValueError("Look point too far")
        return decision
    if decision.action in (Action.MOVE, Action.JUMP):
        if decision.target_id != "":
            raise ValueError("Forward movement has no arbitrary target")
        if decision.destination is not None:
            a, b = snapshot.actor.position, decision.destination
            if math.dist((a.x, a.y, a.z), (b.x, b.y, b.z)) > 30 or abs(a.y - b.y) > 8:
                raise ValueError("Destination must be within the observed neighbourhood")
            return decision
        forward = snapshot.actor.forward
        if math.hypot(forward.x, forward.z) < 0.01:
            raise ValueError("Forward direction is unavailable")
        return decision
    entities = {e.id: e for e in snapshot.entities}
    inventory = {i.id: i for i in snapshot.actor.inventory}
    target = entities.get(decision.target_id)
    if decision.action in (Action.CRAFT, Action.UPGRADE):
        recipe = next((r for r in snapshot.recipes if r.id == decision.target_id), None)
        if recipe is None: raise ValueError("Unknown recipe")
        if decision.amount > 10 or (decision.action == Action.UPGRADE and decision.amount != 1): raise ValueError("Craft batches are 1..10 operations; upgrade is one operation")
        if decision.action == Action.CRAFT and not recipe.craftable: raise ValueError("Native crafting requirements not met")
        if decision.action == Action.UPGRADE and decision.item_id not in inventory: raise ValueError("Missing upgrade item")
        return decision
    if decision.action == Action.BUILD:
        option = next((b for b in snapshot.build_options if b.id == decision.target_id), None)
        if option is None or not option.available or decision.destination is None: raise ValueError("Unavailable building piece or position")
        a, b = snapshot.actor.position, decision.destination
        if math.dist((a.x,a.y,a.z),(b.x,b.y,b.z)) > 8: raise ValueError("Build point too far")
        return decision
    if decision.action == Action.FOLLOW:
        valid = target is not None and target.kind == "player" and target.id == snapshot.leader_id
    elif decision.action in (Action.ATTACK, Action.SECONDARY_ATTACK, Action.DEFEND, Action.FLEE, Action.DODGE):
        valid = target is not None and target.kind == "enemy" and target.distance <= 20
    elif decision.action == Action.PICKUP:
        if target is None or target.kind != "item": raise ValueError("Pickup target must be a dropped item")
        valid = target.distance <= 128
    elif decision.action == Action.GIVE:
        if target is None or target.kind != "player": raise ValueError("Give recipient must be an observed player")
        validate_transfer(decision, snapshot)
        valid = target.distance <= 60
    elif decision.action == Action.EAT:
        valid = decision.target_id in inventory and inventory[decision.target_id].food and inventory[decision.target_id].can_eat
    elif decision.action == Action.EQUIP:
        valid = decision.target_id in inventory and inventory[decision.target_id].equipable
    elif decision.action == Action.USE:
        valid = decision.target_id in inventory and inventory[decision.target_id].usable
    elif decision.action == Action.INTERACT:
        valid = target is not None and target.kind == "interactable" and target.distance <= 20
    elif decision.action in (Action.DROP, Action.UNEQUIP):
        if decision.action == Action.DROP: validate_transfer(decision, snapshot)
        elif decision.target_id not in inventory: raise ValueError("Inventory item not observed")
        valid = decision.action != Action.UNEQUIP or inventory[decision.target_id].equipable
    elif decision.action == Action.HUNT:
        valid = target is not None and target.kind == "animal"
    elif decision.action == Action.GATHER:
        valid = target is not None and target.kind == "resource"
    elif decision.action in (Action.OPEN, Action.TAKE, Action.DEPOSIT, Action.STACK):
        valid = target is not None and target.category == "container"
        if decision.action in (Action.TAKE, Action.DEPOSIT):
            items = inventory if decision.action == Action.DEPOSIT else {i.id:i for i in snapshot.container_inventory}
            valid = valid and snapshot.container_id == decision.target_id and decision.item_id in items and decision.amount <= items[decision.item_id].count
    elif decision.action == Action.USE_ON:
        valid = target is not None and target.kind == "interactable" and decision.item_id in inventory and decision.amount <= inventory[decision.item_id].count
    elif decision.action == Action.REPAIR:
        valid = target is not None and target.category == "station"
    elif decision.action == Action.REPAIR_BUILDING:
        valid = target is not None and target.category == "structure"
    elif decision.action == Action.SLEEP:
        valid = target is not None and target.category == "bed"
    else:
        valid = False
    if not valid:
        raise ValueError("Action target is not available or is not permitted")
    # Recompute range too: never trust only the sender's distance label.
    if target and decision.action != Action.FOLLOW:
        a, b = snapshot.actor.position, target.position
        limit = 128 if decision.action == Action.PICKUP else 60 if decision.action == Action.GIVE else 20
        if math.dist((a.x, a.y, a.z), (b.x, b.y, b.z)) > limit:
            raise ValueError("Target is out of range")
    return decision
