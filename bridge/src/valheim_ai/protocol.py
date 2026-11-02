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


class Actor(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    name: str = Field(max_length=120)
    position: Vec3
    forward: Vec3 = Field(default_factory=lambda: Vec3(x=0, y=0, z=1))
    health: float = Field(ge=0)
    max_health: float = Field(gt=0)
    stamina: float = Field(ge=0)
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
    kind: str = Field(pattern="^(player|enemy|item)$")
    position: Vec3
    distance: float = Field(ge=0, le=200)


class Snapshot(StrictModel):
    protocol_version: int = Field(default=1, ge=1, le=1)
    session_id: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    sequence: int = Field(ge=1)
    actor: Actor
    entities: list[Entity] = Field(default_factory=list, max_length=64)
    leader_id: str = Field(default="", max_length=100)
    previous_result: str = Field(default="", max_length=240)
    # An old mod never receives an action its executor does not understand.
    supported_actions: list[str] = Field(
        default_factory=lambda: ["wait", "follow", "attack", "pickup", "eat", "equip"],
        max_length=16,
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


class Decision(StrictModel):
    action: Action
    target_id: str = Field(max_length=100)
    duration: float = Field(ge=0.25, le=3.0)
    say: str = Field(max_length=240)

    @field_validator("say")
    @classmethod
    def plain_text(cls, value: str) -> str:
        return "".join(c for c in value if c.isprintable()).strip()


class DecisionReply(Decision):
    protocol_version: int = 1
    sequence: int
    source: str
    command_revision: int


def wait(reason: str = "") -> Decision:
    return Decision(action=Action.WAIT, target_id="", duration=1, say=reason[:240])


def validate_decision(decision: Decision, snapshot: Snapshot) -> Decision:
    """Never accept invented targets or arbitrary code from a provider."""
    if decision.action == Action.WAIT:
        return decision.model_copy(update={"target_id": ""})
    if decision.action.value not in snapshot.supported_actions:
        raise ValueError("This mod does not support the requested action")
    if decision.action == Action.MOVE:
        if decision.target_id != "":
            raise ValueError("Forward movement has no arbitrary target")
        forward = snapshot.actor.forward
        if math.hypot(forward.x, forward.z) < 0.01:
            raise ValueError("Forward direction is unavailable")
        return decision
    entities = {e.id: e for e in snapshot.entities}
    inventory = {i.id: i for i in snapshot.actor.inventory}
    target = entities.get(decision.target_id)
    if decision.action == Action.FOLLOW:
        valid = target is not None and target.kind == "player" and target.id == snapshot.leader_id
    elif decision.action == Action.ATTACK:
        valid = target is not None and target.kind == "enemy" and target.distance <= 20
    elif decision.action == Action.PICKUP:
        valid = target is not None and target.kind == "item" and target.distance <= 20
    elif decision.action == Action.EAT:
        valid = decision.target_id in inventory and inventory[decision.target_id].food
    elif decision.action == Action.EQUIP:
        valid = decision.target_id in inventory and inventory[decision.target_id].equipable
    else:
        valid = False
    if not valid:
        raise ValueError("Action target is not available or is not permitted")
    # Recompute range too: never trust only the sender's distance label.
    if target and decision.action in (Action.ATTACK, Action.PICKUP):
        a, b = snapshot.actor.position, target.position
        if math.dist((a.x, a.y, a.z), (b.x, b.y, b.z)) > 20:
            raise ValueError("Target is out of range")
    return decision
