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
    category: str = Field(default="", max_length=40)
    damage: float = Field(default=0, ge=0)
    durability: float = Field(default=1, ge=0, le=1)
    food_health: float = Field(default=0, ge=0)
    food_stamina: float = Field(default=0, ge=0)
    usable: bool = False
    can_eat: bool = True


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
    kind: str = Field(pattern="^(player|enemy|item|interactable)$")
    position: Vec3
    distance: float = Field(ge=0, le=200)
    health: float = Field(default=0, ge=0)
    max_health: float = Field(default=0, ge=0)
    targeting_us: bool = False
    targeting_leader: bool = False
    attacking: bool = False
    category: str = Field(default="", max_length=40)


class Snapshot(StrictModel):
    protocol_version: int = Field(default=1, ge=1, le=1)
    session_id: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    sequence: int = Field(ge=1)
    actor: Actor
    entities: list[Entity] = Field(default_factory=list, max_length=64)
    leader_id: str = Field(default="", max_length=100)
    previous_result: str = Field(default="", max_length=240)
    background_planning: bool = False
    plan_id: str = Field(default="", max_length=80)
    action_status: str = Field(default="", pattern="^(|running|success|blocked)$")
    recent_events: list[str] = Field(default_factory=list, max_length=8)
    # An old mod never receives an action its executor does not understand.
    supported_actions: list[str] = Field(
        default_factory=lambda: ["wait", "follow", "attack", "pickup", "eat", "equip"],
        max_length=24,
    )

    @model_validator(mode="after")
    def unique_ids(self):
        ids = [e.id for e in self.entities] + [i.id for i in self.actor.inventory]
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


class Decision(StrictModel):
    action: Action
    target_id: str = Field(max_length=100)
    duration: float = Field(ge=0.25, le=3.0)
    say: str = Field(max_length=240)
    destination: Vec3 | None = None
    run: bool = False

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


class Plan(StrictModel):
    """Short executable plan; each step is leased to the mod for at most 3 seconds."""
    steps: list[Decision] = Field(min_length=1, max_length=6)


def wait(reason: str = "") -> Decision:
    return Decision(action=Action.WAIT, target_id="", duration=1, say=reason[:240])


def validate_decision(decision: Decision, snapshot: Snapshot) -> Decision:
    """Never accept invented targets or arbitrary code from a provider."""
    if decision.destination is not None and decision.action not in (Action.MOVE, Action.JUMP):
        raise ValueError("Only movement can specify a destination")
    if decision.action == Action.WAIT:
        return decision.model_copy(update={"target_id": ""})
    if decision.action.value not in snapshot.supported_actions:
        raise ValueError("This mod does not support the requested action")
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
    if decision.action == Action.FOLLOW:
        valid = target is not None and target.kind == "player" and target.id == snapshot.leader_id
    elif decision.action in (Action.ATTACK, Action.DEFEND, Action.FLEE, Action.DODGE):
        valid = target is not None and target.kind == "enemy" and target.distance <= 20
    elif decision.action == Action.PICKUP:
        valid = target is not None and target.kind == "item" and target.distance <= 20
    elif decision.action == Action.EAT:
        valid = decision.target_id in inventory and inventory[decision.target_id].food and inventory[decision.target_id].can_eat
    elif decision.action == Action.EQUIP:
        valid = decision.target_id in inventory and inventory[decision.target_id].equipable
    elif decision.action == Action.USE:
        valid = decision.target_id in inventory and inventory[decision.target_id].usable
    elif decision.action == Action.INTERACT:
        valid = target is not None and target.kind == "interactable" and target.distance <= 20
    else:
        valid = False
    if not valid:
        raise ValueError("Action target is not available or is not permitted")
    # Recompute range too: never trust only the sender's distance label.
    if target and decision.action != Action.FOLLOW:
        a, b = snapshot.actor.position, target.position
        if math.dist((a.x, a.y, a.z), (b.x, b.y, b.z)) > 20:
            raise ValueError("Target is out of range")
    return decision
