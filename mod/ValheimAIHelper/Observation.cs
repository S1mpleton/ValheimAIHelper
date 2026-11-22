using System;
using System.Collections.Generic;
using System.Linq;
using System.Runtime.CompilerServices;
using System.Text.RegularExpressions;
using HarmonyLib;
using UnityEngine;

namespace ValheimAIHelper;

internal sealed class Observation
{
    public Snapshot Data = new();
    public readonly Dictionary<string, Character> Characters = new();
    public readonly Dictionary<string, ItemDrop> Drops = new();
    public readonly Dictionary<string, ItemDrop.ItemData> Items = new(), ContainerItems = new();
    public readonly Dictionary<string, Component> Interactables = new(), Resources = new();
    public readonly Dictionary<string, Recipe> Recipes = new();
    public readonly Dictionary<string, Piece> Pieces = new();
    private static readonly Collider[] hits = new Collider[768];
    private static readonly System.Reflection.FieldInfo? dropInstances = AccessTools.Field(typeof(ItemDrop), "s_instances");
    private static Component[] loadedPickables = Array.Empty<Component>();
    private static ItemDrop[] loadedDropFallback = Array.Empty<ItemDrop>();
    private static float nextPickableScan;
    private static long pickableWorld;
    private static readonly Dictionary<string, string> categories = new()
    {
        ["Pickable"]="pickable", ["PickableItem"]="pickable", ["Door"]="door", ["Container"]="container",
        ["CraftingStation"]="station", ["CookingStation"]="cooking", ["Smelter"]="smelter",
        ["Fermenter"]="fermenter", ["Fireplace"]="fire", ["Beehive"]="beehive", ["Bed"]="bed",
        ["Chair"]="chair", ["ItemStand"]="item_stand", ["ArmorStand"]="item_stand", ["Switch"]="station_input"
    };
    internal static IEnumerable<ItemDrop> LoadedDrops()
    {
        RefreshGroundCache();
        var registered = dropInstances?.GetValue(null) as IEnumerable<ItemDrop>;
        return registered == null ? loadedDropFallback : registered.Concat(loadedDropFallback).Distinct();
    }
    private static void RefreshGroundCache()
    {
        long world = ZNet.instance ? ZNet.instance.GetWorldUID() : 0;
        if (world != pickableWorld || Time.realtimeSinceStartup >= nextPickableScan)
        {
            // Generated E-pickups are not ItemDrop instances and may have no
            // collider in the short interaction query. Cache a loaded-object
            // scan instead of tying visibility to collider size or frame rate.
            loadedPickables = UnityEngine.Object.FindObjectsOfType<Pickable>().Cast<Component>()
                .Concat(UnityEngine.Object.FindObjectsOfType<PickableItem>()).ToArray();
            // Registry layout/lifecycle can differ between supported game
            // versions. Include real active scene drops even when the registry
            // exists but has not registered them; never scan prefab assets.
            loadedDropFallback = UnityEngine.Object.FindObjectsOfType<ItemDrop>();
            pickableWorld = world; nextPickableScan = Time.realtimeSinceStartup + 2;
        }
    }
    private static IEnumerable<Component> LoadedPickables() { RefreshGroundCache(); return loadedPickables; }
    private static bool AvailableDrop(ItemDrop drop)
    {
        if (!drop || !drop.gameObject.activeInHierarchy || drop.m_itemData == null || drop.m_itemData.m_stack <= 0) return false;
        // Attached item visuals may retain an ItemDrop component. They are not
        // loose inventory stock and must never be passed to Player.Pickup.
        if (drop.GetComponentInParent<Character>() || drop.GetComponentInParent<Pickable>() || drop.GetComponentInParent<PickableItem>()) return false;
        if (drop.GetComponentsInParent<Component>(true).Any(c=>c && (c.GetType().Name=="ItemStand" || c.GetType().Name=="ArmorStand"))) return false;
        // Use the view actually owned by ItemDrop. A visual on a corpse or
        // another networked object must not borrow that parent's ZNetView.
        var view = NativeAccess.Read(drop,"m_nview") as ZNetView ?? drop.GetComponent<ZNetView>();
        return view && view.IsValid();
    }
    private static bool AvailablePickable(Component component)
    {
        if (!component || !component.gameObject.activeInHierarchy || NativeAccess.Read(component, "m_picked") is true ||
            NativeAccess.Read(component, "m_enabled") is int enabled && enabled == 0) return false;
        var view = NativeAccess.Read(component,"m_nview") as ZNetView ?? component.GetComponent<ZNetView>();
        return view && view.IsValid() && ItemCatalog.ProducedItem(component);
    }
    internal static string Id(Character c) => "character:" + c.GetInstanceID();
    internal static string WorldId(Component c) => "world:" + c.GetInstanceID();
    internal static string ItemId(ItemDrop.ItemData item, bool container = false) => (container ? "container-item:" : "inventory:") + RuntimeHelpers.GetHashCode(item);
    internal static Position Pos(Vector3 p)
    {
        if (!ObservationValues.Finite(p.x) || !ObservationValues.Finite(p.y) || !ObservationValues.Finite(p.z))
            throw new InvalidOperationException("Position is not ready");
        return new Position { x=p.x, y=p.y, z=p.z };
    }
    internal static string Name(string value, int limit = 120)
    {
        string text = Localization.instance != null ? Localization.instance.Localize(value ?? "") : value;
        return ObservationValues.Text(Regex.Replace(text ?? "", "<[^>]+>", ""), limit);
    }
    public static bool IsEnemy(Player actor, Character target, Player? leader) =>
        target && !(target is Player) && !target.IsDead() && !target.IsTamed() && target.GetBaseAI() is MonsterAI && BaseAI.IsEnemy(actor, target);
    public static bool IsWildAnimal(Character target) =>
        target && !(target is Player) && !target.IsDead() && !target.IsTamed() && target.GetBaseAI() is AnimalAI;
    private static InventoryEntry Item(Player actor, ItemDrop.ItemData item, bool container = false)
    {
        string category = item.m_shared.m_itemType.ToString();
        bool weapon = item.IsWeapon();
        bool damage = ItemCatalogPolicy.HasDamage(category, weapon);
        string prefab = ItemCatalog.Prefab(item);
        return new InventoryEntry {
        id=ItemId(item, container), name=Name(item.m_shared.m_name), count=Math.Min(item.m_stack,100000),
        equipped=item.m_equipped, food=item.m_shared.m_food>0, equipable=item.IsEquipable(),
        item_prefab=prefab, item_token=ObservationValues.Text(item.m_shared.m_name,160),
        category=category, damage=damage?ObservationValues.Vital(item.GetDamage().GetTotalDamage()):0,
        durability=item.m_shared.m_useDurability ? Mathf.Clamp01(item.m_durability/Mathf.Max(1,item.GetMaxDurability())) : 1,
        food_health=ObservationValues.Vital(item.m_shared.m_food), food_stamina=ObservationValues.Vital(item.m_shared.m_foodStamina),
        usable=item.m_shared.m_itemType==ItemDrop.ItemData.ItemType.Consumable,
        item_key=(prefab.Length>0?prefab:item.m_shared.m_name)+":"+item.m_quality+":"+item.m_variant, quest=item.m_shared.m_questItem,
        can_eat=item.m_shared.m_food>0 && actor.CanEat(item,false), weight=ObservationValues.Vital(item.m_shared.m_weight*item.m_stack),
        tool_tier=ItemCatalogPolicy.HasToolTier(category,weapon)?Mathf.Clamp(item.m_shared.m_toolTier,0,20):0, effect=Name(item.m_shared.m_consumeStatusEffect ? item.m_shared.m_consumeStatusEffect.name : ""),
        quality=Mathf.Clamp(item.m_quality,1,100),max_quality=Mathf.Clamp(item.m_shared.m_maxQuality,1,100),
        armor=ItemCatalogPolicy.HasArmor(category)?ObservationValues.Vital(item.GetArmor()):0,
        block_power=ItemCatalogPolicy.HasBlock(category,weapon)?ObservationValues.Vital(item.m_shared.m_blockPower):0,
        ammo_type=damage?Name(item.m_shared.m_ammoType,80):"",
        skill=ItemCatalogPolicy.HasSkill(category,weapon)?item.m_shared.m_skillType.ToString():"",damage_types=damage?DamageTypes(item):""
        };
    }
    private static string DamageTypes(ItemDrop.ItemData item)
    {
        object damage=item.GetDamage();
        return Name(string.Join("; ",new[]{"m_damage","m_blunt","m_slash","m_pierce","m_fire","m_frost","m_lightning","m_poison","m_spirit","m_chop","m_pickaxe"}
            .Select(n=>new{n,v=NativeAccess.Read(damage,n)}).Where(v=>v.v is float f && f>0).Select(v=>v.n.Substring(2)+":"+v.v)),240);
    }
    private static string Requirements(Piece.Requirement[] requirements) => Name(string.Join("; ", requirements.Where(r=>r.m_resItem)
        .Select(r=>Name(r.m_resItem.m_itemData.m_shared.m_name)+": "+r.m_amount)),500);

    public static Observation Capture(Player actor, Player? leader, string session, int sequence, string result)
    {
        var o=new Observation(); var origin=actor.transform.position; var entities=new List<Entity>();
        ItemCatalog.Refresh();
        o.Data.item_catalog=ItemCatalog.SnapshotEntries;o.Data.item_catalog_version=ItemCatalog.Version;
        o.Data.item_catalog_total=ItemCatalog.Count;o.Data.item_catalog_truncated=ItemCatalog.Count>ItemCatalog.SnapshotEntries.Length;
        var inventory=actor.GetInventory().GetAllItems().Where(i=>i!=null && i.m_stack>0).Take(64).ToArray();
        foreach(var item in inventory) o.Items[ItemId(item)]=item;
        o.Data.leader_status = leader ? "out_of_range" : "unavailable";
        if(leader && !leader!.IsDead() && Vector3.Distance(origin,leader.transform.position)<=128)
        {
            string id=Id(leader);o.Characters[id]=leader;o.Data.leader_id=id;o.Data.leader_status="visible";
            entities.Add(new Entity {id=id,name=Name(leader.GetPlayerName()),kind="player",position=Pos(leader.transform.position),distance=Vector3.Distance(origin,leader.transform.position)});
        }
        foreach(var other in Player.GetAllPlayers().Where(p=>p && !CompanionNpc.Is(p) && p!=actor && p!=leader && !p.IsDead() && Vector3.Distance(origin,p.transform.position)<=60)
            .OrderBy(p=>Vector3.SqrMagnitude(origin-p.transform.position)).Take(15))
        {
            string id=Id(other);o.Characters[id]=other;
            entities.Add(new Entity {id=id,name=Name(other.GetPlayerName()),kind="player",position=Pos(other.transform.position),distance=Vector3.Distance(origin,other.transform.position)});
        }
        foreach(var c in Character.GetAllCharacters().Where(c=>IsEnemy(actor,c,leader)||IsWildAnimal(c))
            .Where(c=>Vector3.Distance(origin,c.transform.position)<=20).OrderBy(c=>Vector3.SqrMagnitude(origin-c.transform.position)).Take(16))
        {
            string id=Id(c);o.Characters[id]=c;var victim=c.GetBaseAI()?.GetTargetCreature();
            entities.Add(new Entity {id=id,name=Name(c.GetHoverName()),kind=IsWildAnimal(c)?"animal":"enemy",position=Pos(c.transform.position),
                distance=Vector3.Distance(origin,c.transform.position),health=ObservationValues.Vital(c.GetHealth()),max_health=ObservationValues.Vital(c.GetMaxHealth()),
                targeting_us=victim==actor,targeting_leader=leader && victim==leader,attacking=c.InAttack(),category=c.GetFaction().ToString()});
        }
        int count=Physics.OverlapSphereNonAlloc(origin,20,hits);
        var nearby=hits.Take(count).Where(c=>c).ToArray();
        // Use the game's loaded ItemDrop registry, not the short collider query
        // for interactions: small drops and drops beyond 20m were disappearing.
        var drops = LoadedDrops().Where(AvailableDrop)
            .Where(d=>Vector3.Distance(origin,d.transform.position)<=CollectiblePolicy.Radius)
            .Distinct().OrderBy(d=>Vector3.SqrMagnitude(origin-d.transform.position)).ToArray();
        var ground = new List<Entity>();
        o.Data.ground_scan_radius=CollectiblePolicy.Radius;o.Data.ground_items_total=drops.Length;
        foreach(var d in drops)
        {
            string id="drop:"+d.GetInstanceID();o.Drops[id]=d;
            bool allowed = d.CanPickup(false);
            ground.Add(new Entity {id=id,name=Name(d.m_itemData.m_shared.m_name),kind="item",interaction="pickup",category=d.m_itemData.m_shared.m_itemType.ToString(),
                item_prefab=ItemCatalog.Prefab(d.m_itemData),item_token=ObservationValues.Text(d.m_itemData.m_shared.m_name,160),
                position=Pos(d.transform.position),distance=Vector3.Distance(origin,d.transform.position),count=Mathf.Clamp(d.m_itemData.m_stack,1,100000),
                weight=ObservationValues.Vital(d.m_itemData.GetWeight()),pickup_allowed=allowed,state=allowed?"Выпавший предмет":"Подбор временно недоступен игре"});
        }
        foreach(var c in LoadedPickables().Where(AvailablePickable).Where(c=>Vector3.Distance(origin,c.transform.position)<=CollectiblePolicy.Radius))
        {
            string[] produces = ItemCatalog.Produces(c);
            if (produces.Length == 0) continue;
            var item = ItemCatalog.ProducedItem(c)!.m_itemData;
            if (item?.m_shared == null) continue;
            string id = WorldId(c);o.Interactables[id]=c;
            string name = Name(item.m_shared.m_name);
            if (name.Length == 0) name = produces[0];
            int amount = NativeAccess.Read(c, "m_amount", "m_stack") is int configured ? Mathf.Clamp(configured,1,100000) : 1;
            ground.Add(new Entity {id=id,name=name,kind="interactable",interaction="interact",category="pickable",
                item_prefab=produces[0],item_token=ObservationValues.Text(item.m_shared.m_name,160),produces=produces,
                count=amount,position=Pos(c.transform.position),distance=Vector3.Distance(origin,c.transform.position),
                state="Собираемый предмет: подойти и нажать E"});
        }
        var selectedGround = CollectiblePolicy.Select(ground,Math.Min(CollectiblePolicy.Limit,Math.Max(0,128-entities.Count-24)));
        entities.AddRange(selectedGround);
        o.Data.ground_items_truncated=drops.Length>selectedGround.Count(e=>e.kind=="item");
        var parents=nearby.SelectMany(c=>c.GetComponentsInParent<Component>(true)).Where(c=>c).Distinct().ToArray();
        var switches=parents.Where(c=>new[]{"Smelter","CookingStation","Fermenter"}.Contains(c.GetType().Name))
            .SelectMany(c=>c.GetComponentsInChildren<Component>(true)).Where(c=>c && c.GetType().Name=="Switch");
        var objects=parents.Concat(switches).Distinct().ToArray();
        foreach(var c in objects.Where(c=>categories.ContainsKey(c.GetType().Name) && c is Interactable && !(c is Pickable) && !(c is PickableItem))
            .Where(c=>Vector3.Distance(origin,c.transform.position)<=20 && !(NativeAccess.Read(c,"m_picked") is true))
            .OrderBy(c=>Vector3.SqrMagnitude(origin-c.transform.position)).Take(16))
        {
            string id=WorldId(c);o.Interactables[id]=c;var hover=c as Hoverable;
            entities.Add(new Entity {id=id,name=Name(hover?.GetHoverName()??c.name),kind="interactable",interaction="interact",category=categories[c.GetType().Name],
                produces=ItemCatalog.Produces(c),
                state=Name(hover?.GetHoverText()??"",240),position=Pos(c.transform.position),distance=Vector3.Distance(origin,c.transform.position)});
        }
        foreach(var c in objects.Where(c=>ResourcePolicy.IsHarvestResource(c.GetType().Name, c.GetComponent<Pickable>() || c.GetComponent<PickableItem>(), c.GetComponent<ItemDrop>(),
                c.GetType().Name == "Destructible" && string.Equals(NativeAccess.Read(c,"m_destructibleType")?.ToString(), "Tree", StringComparison.OrdinalIgnoreCase)))
            .Where(c=>!c.GetComponent<Piece>() && Vector3.Distance(origin,c.transform.position)<=20)
            .OrderBy(c=>Vector3.SqrMagnitude(origin-c.transform.position)).Take(8))
        {
            string id=WorldId(c);o.Resources[id]=c;
            entities.Add(new Entity {id=id,name=Name((c as Hoverable)?.GetHoverName()??c.name),kind="resource",interaction="gather",required_tool=c.GetType().Name.StartsWith("MineRock")?"pickaxe":"axe",
                category=c.GetType().Name=="Destructible"?"DestructibleTree":c.GetType().Name,position=Pos(c.transform.position),distance=Vector3.Distance(origin,c.transform.position),
                tool_tier=Mathf.Clamp(NativeAccess.Read(c,"m_minToolTier") is int tier?tier:0,0,20)});
        }
        foreach(var piece in objects.OfType<Piece>().Where(p=>p.GetComponent<WearNTear>() && !o.Interactables.ContainsKey(WorldId(p)))
            .OrderBy(p=>Vector3.SqrMagnitude(origin-p.transform.position)).Take(Math.Max(0,128-entities.Count)))
        {
            var view=piece.GetComponent<ZNetView>();if(!view||!view.IsValid())continue;
            string id=WorldId(piece);o.Interactables[id]=piece;
            entities.Add(new Entity {id=id,name=Name(piece.m_name),kind="interactable",category="structure",
                position=Pos(piece.transform.position),distance=Vector3.Distance(origin,piece.transform.position)});
        }
        Array.Clear(hits,0,count);
        o.Data.session_id=session;o.Data.sequence=sequence;o.Data.previous_result=ObservationValues.Text(result,240);
        o.Data.entities=entities.Take(128).ToArray();
        float maxHealth=actor.GetMaxHealth();if(!ObservationValues.Finite(maxHealth)||maxHealth<=0)throw new InvalidOperationException("Player health is not ready");
        var effects=actor.GetSEMan().GetStatusEffects().Select(s=>Name(s.name,80)).Take(20).ToArray();
        actor.GetGuardianPowerHUD(out _,out float guardianCooldown);
        o.Data.actor=new Actor {id="player:"+actor.GetPlayerID(),name=Name(actor.GetPlayerName()),position=Pos(origin),forward=Pos(actor.transform.forward),
            health=ObservationValues.Vital(actor.GetHealth()),max_health=maxHealth,stamina=ObservationValues.Vital(actor.GetStamina()),max_stamina=Mathf.Max(1,actor.GetMaxStamina()),
            food_slots=Mathf.Min(3,actor.GetFoods().Count),weapon=Name((NativeAccess.HeldItem(actor)??actor.GetCurrentWeapon())?.m_shared.m_name??""),blocking=actor.IsBlocking(),
            swimming=actor.IsSwimming(),encumbered=actor.IsEncumbered(),biome=actor.GetCurrentBiome().ToString(),inventory=inventory.Select(i=>Item(actor,i)).ToArray(),
            carry_weight=ObservationValues.Vital(actor.GetInventory().GetTotalWeight()),max_carry_weight=Mathf.Max(1,actor.GetMaxCarryWeight()),
            free_slots=Mathf.Clamp(actor.GetInventory().GetWidth()*actor.GetInventory().GetHeight()-inventory.Length,0,256),comfort=Mathf.Clamp(actor.GetComfortLevel(),0,100),
            sheltered=actor.InShelter(),rested=effects.Any(s=>s.Equals("Rested",StringComparison.OrdinalIgnoreCase)),wet=effects.Any(s=>s.Equals("Wet",StringComparison.OrdinalIgnoreCase)),
            cold=effects.Any(s=>s.IndexOf("Cold",StringComparison.OrdinalIgnoreCase)>=0||s.IndexOf("Freezing",StringComparison.OrdinalIgnoreCase)>=0),
            attached=actor.IsAttached(),sitting=actor.IsSitting(),eitr=ObservationValues.Vital(actor.GetEitr()),status_effects=effects,
            station_id=actor.GetCurrentCraftingStation()?WorldId(actor.GetCurrentCraftingStation()):"",guardian_power=Name(actor.GetGuardianPowerName()),
            guardian_cooldown=ObservationValues.Vital(guardianCooldown)};
        var available=new List<Recipe>();actor.GetAvailableRecipes(ref available);
        var recipes=available.Where(r=>r&&r.m_item).Select(r=>new {r,can=actor.HaveRequirements(r,false,1)})
            .OrderByDescending(r=>r.can).ThenBy(r=>r.r.m_item.m_itemData.m_shared.m_name).Take(24).ToArray();
        foreach(var r in recipes)o.Recipes["recipe:"+r.r.GetInstanceID()]=r.r;
        o.Data.recipes=recipes.Select(r=>new RecipeOption {id="recipe:"+r.r.GetInstanceID(),name=Name(r.r.m_item.m_itemData.m_shared.m_name),
            amount=Mathf.Clamp(r.r.m_amount,1,10000),craftable=r.can,station=Name(r.r.m_craftingStation?r.r.m_craftingStation.m_name:"inventory"),
            requirements=Requirements(r.r.m_resources)}).ToArray();
        var table=NativeAccess.HeldItem(actor)?.m_shared.m_buildPieces;
        var pieces=table!=null?table.m_pieces.Where(g=>g).Select(g=>g.GetComponent<Piece>()).Where(p=>p&&actor.IsPieceAvailable(p)).ToArray():Array.Empty<Piece>();
        pieces=pieces.OrderByDescending(p=>actor.HaveRequirements(p,Player.RequirementMode.CanBuild)).Take(32).ToArray();
        foreach(var p in pieces)o.Pieces["piece:"+p.GetInstanceID()]=p;
        o.Data.build_options=pieces.Select(p=>new BuildOption {id="piece:"+p.GetInstanceID(),name=Name(p.m_name),category=p.m_category.ToString(),
            available=!p.m_removePiece&&!p.m_repairPiece&&actor.HaveRequirements(p,Player.RequirementMode.CanBuild),requirements=Requirements(p.m_resources)}).ToArray();
        var opened=NativeInventorySession.CurrentContainer;
        if(opened && NativeInventorySession.HasAccess(actor,opened!))
        {
            string id=WorldId(opened!);
            if(!o.Interactables.ContainsKey(id))
            {
                o.Interactables[id]=opened!;
                if(entities.Count>=128)entities.RemoveAt(entities.Count-1);
                entities.Add(new Entity {id=id,name=Name(opened!.GetHoverName()),kind="interactable",category="container",
                    position=Pos(opened.transform.position),distance=Vector3.Distance(origin,opened.transform.position)});
            }
            if(o.Interactables.ContainsKey(id))
            {
                o.Data.container_id=id;
                var contents=opened!.GetInventory().GetAllItems().Where(i=>i.m_stack>0).Take(64).ToArray();
                foreach(var item in contents)o.ContainerItems[ItemId(item,true)]=item;
                o.Data.container_inventory=contents.Select(i=>Item(actor,i,true)).ToArray();
            }
        }
        if(EnvMan.instance){o.Data.daylight=EnvMan.IsDay();o.Data.weather=Name(EnvMan.instance.GetCurrentEnvironment().m_name,80);}
        o.Data.entities=entities.Take(128).ToArray();
        return o;
    }
}
