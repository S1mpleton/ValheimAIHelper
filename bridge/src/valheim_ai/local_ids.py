"""Readable, per-request handles; native IDs remain authoritative in the game."""

def readable_view(snapshot):
    view = snapshot.model_copy(deep=True)
    originals = {snapshot.actor.id, *(e.id for e in snapshot.entities),
                 *(i.id for i in snapshot.actor.inventory), *(i.id for i in snapshot.container_inventory),
                 *(r.id for r in snapshot.recipes), *(b.id for b in snapshot.build_options)}
    forward, reverse = {}, {}

    def rename(obj, group, index):
        original = obj.id
        if original not in forward:
            label = " ".join("".join(c if c.isprintable() else " " for c in obj.name).split())[:48] or group
            handle = f"@{group}/{label}/{index}"
            while handle in originals or handle in reverse: handle += "_"
            forward[original], reverse[handle] = handle, original
        obj.id = forward[original]

    for group, values in (("bag", view.actor.inventory), ("chest", view.container_inventory),
                          ("recipe", view.recipes), ("build", view.build_options)):
        for index, obj in enumerate(values, 1): rename(obj, group, index)
    for index, entity in enumerate(view.entities, 1):
        rename(entity, {"item":"ground", "resource":"resource", "player":"human"}.get(entity.kind, entity.kind), index)
    view.leader_id = forward.get(view.leader_id, view.leader_id)
    view.container_id = forward.get(view.container_id, view.container_id)
    return view, reverse


def native_decision(decision, reverse):
    return decision.model_copy(update={field: reverse.get(getattr(decision, field), getattr(decision, field))
                                       for field in ("target_id", "item_id")})
