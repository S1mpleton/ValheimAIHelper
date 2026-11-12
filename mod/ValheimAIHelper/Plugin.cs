using System;
using System.Collections;
using System.Linq;
using System.Text;
using BepInEx;
using BepInEx.Configuration;
using HarmonyLib;
using UnityEngine;
using UnityEngine.Networking;

namespace ValheimAIHelper;

[BepInPlugin("dev.valheim.aihelper", "Valheim AI Helper", "0.6.1")]
[DefaultExecutionOrder(-100)]
public sealed partial class Plugin : BaseUnityPlugin
{
    private ConfigEntry<string> bridgeUrl = null!, bridgeToken = null!, leaderName = null!;
    private ConfigEntry<bool> allowCombat = null!, autoSurvival = null!, autoBridge = null!, allowChat = null!, controlByAllPlayers = null!;
    private ConfigEntry<float> interval = null!;
    private readonly ActionExecutor executor = new();
    private static Plugin? instance;
    private Harmony? harmony;
    private bool inputPatchReady, panelFocusReady, placementPatchReady;
    private Player? actor;
    private PlayerController? controller;
    private bool active, showPanel, bridgeSessionReady;
    private CursorLockMode oldCursorLock;
    private bool oldCursorVisible;
    private long? leaderId;
    private Coroutine? loop;
    private UnityWebRequest? pending;
    private string session = "", status = "F8 — панель. F9 — ручное управление.";
    private string lastFailure = "";
    private int sequence, requestedRevision;
    private string requestedMode = "";
    private Rect window = new(20, 80, 480, 340);
    private GUIStyle? statusStyle;

    private void Awake()
    {
        bridgeUrl = Config.Bind("Bridge", "Url", "http://127.0.0.1:8765", "Local bridge URL. Loopback HTTP only.");
        bridgeToken = Config.Bind("Bridge", "Token", "", "Pairing code from .local/bridge-token, NOT an AI API key.");
        leaderName = Config.Bind("Player", "LeaderName", "", "Exact leader character name. Empty: lock on nearest other player when AI is enabled.");
        allowCombat = Config.Bind("Player", "AllowCombat", true, "Allow combat and immediate defence against hostile monsters. Visible in the game panel.");
        autoSurvival = Config.Bind("Player", "AutoSurvival", true, "Use available food through native inventory actions.");
        allowChat = Config.Bind("Player", "LeaderChat", true, "Accept ordinary chat from the selected leader; character name explicitly addresses the helper. Chat context is sent to the configured AI.");
        controlByAllPlayers = Config.Bind("Player", "ControlByAllPlayers", false, "Chat control: false = selected leader only; true = all other players. Network sender identity is always checked.");
        autoBridge = Config.Bind("Bridge", "AutoStart", true, "Automatically start the bundled local service on Windows.");
        interval = Config.Bind("Bridge", "DecisionInterval", 1f, new ConfigDescription("Seconds between game observations; AI request cost is controlled separately by AI.RequestInterval.", new AcceptableValueRange<float>(.5f, 2f)));
        instance = this;
        InitPanel();
        InitRuntime();
        harmony = new Harmony("dev.valheim.aihelper.input");
        try
        {
            harmony.PatchAll(typeof(PanelInputFocusPatch));
            panelFocusReady = true;
            harmony.PatchAll(typeof(ControlInputPatch));
            inputPatchReady = true;
        }
        catch (Exception ex) { Logger.LogError("Native input hook is incompatible with this game: " + ex); AddLog("input", ex.ToString()); }
        try { harmony.PatchAll(typeof(EscMenuPatch)); }
        catch (Exception ex) { Logger.LogWarning("Esc menu entry: " + ex.GetType().Name + ". Use F8."); }
        try { harmony.PatchAll(typeof(LeaderChatPatch)); }
        catch (Exception ex) { AddLog("chat", "Приём чата несовместим с игрой: " + ex.GetType().Name); }
        try
        {
            harmony.PatchAll(typeof(PlacementInputPatch)); harmony.PatchAll(typeof(PlacementButtonsPatch)); harmony.PatchAll(typeof(PlacementResultPatch)); harmony.PatchAll(typeof(StructureRepairResultPatch));
            placementPatchReady = true;
        }
        catch (Exception ex) { AddLog("placement", ex.ToString()); }
        Logger.LogInfo("Valheim version: " + global::Version.GetVersionString() + "; build references: 0.221.4");
        Logger.LogInfo("AI Helper loaded. Control is OFF. F8 panel; F9 emergency release.");
    }

    private void Update()
    {
        if (Input.GetKeyDown(KeyCode.F9)) { StopFromPanel(); SetPanel(false); }
        if (Input.GetKeyDown(KeyCode.F8)) { if (showPanel) SetPanel(false); else OpenPanelFromMenu(); }
        if (showPanel && Input.GetKeyDown(KeyCode.Escape)) SetPanel(false);
        UpdatePanel();
        UpdateRuntime();
        if (active && (actor == null || !actor || actor != Player.m_localPlayer || !actor.IsOwner() || actor.IsDead() ||
            controller == null || !controller || !controller.enabled))
            DisableControl("Нет локального управления персонажем или мир закрыт. AI отключён.");
    }

    private void FixedUpdate()
    {
        if (!active || actor == null || !actor) return;
        if (actor != Player.m_localPlayer || !actor.IsOwner())
        { DisableControl("Сетевая принадлежность персонажа изменилась. AI отключён."); return; }
        if (showPanel || (InventoryGui.IsVisible() && !executor.OwnsInventory) || Menu.IsVisible() || global::Console.IsVisible() ||
            (Chat.instance != null && Chat.instance.HasFocus()))
        { executor.Pause(); return; }
        try { executor.Tick(actor, FindLeader(), allowCombat.Value, autoSurvival.Value); ReportLocalBlock(); }
        catch (Exception ex)
        {
            Logger.LogWarning("Control stopped: " + ex.GetType().Name);
            AddLog("motor", ex.ToString());
            DisableControl("Ошибка управления; AI отключён");
        }
    }

    private bool EnableControl()
    {
        if (!inputPatchReady)
        { status = "Ввод несовместим с этой версией игры. Подробности в BepInEx/LogOutput.log."; return false; }
        var player = Player.m_localPlayer;
        if (!player || player.IsDead() || !player.IsOwner()) { status = "Сначала войдите персонажем в мир."; return false; }
        if (!Uri.TryCreate(bridgeUrl.Value, UriKind.Absolute, out var url) ||
            url.Scheme != "http" || !url.IsLoopback || !string.IsNullOrEmpty(url.UserInfo) ||
            (url.AbsolutePath != "/" && url.AbsolutePath != "") || !string.IsNullOrEmpty(url.Query) || !string.IsNullOrEmpty(url.Fragment))
        { status = "Bridge.Url должен быть локальным HTTP-адресом без пути."; return false; }
        if (bridgeToken.Value.Length < 32) { status = "Локальная служба ещё запускается. Откройте вкладку «Журнал»."; return false; }
        var input = player.GetComponent<PlayerController>();
        if (!input || !input.enabled) { status = "Управление игроком уже перехвачено другим модом."; return false; }
        actor = player;
        controller = input;
        session = Guid.NewGuid().ToString("N");
        sequence = 0;
        requestedRevision = 0; requestedMode = "";
        active = true;
        bridgeSessionReady = false;
        Logger.LogInfo("Native input control enabled for local owner; PlayerController remains enabled.");
        status = "Подключаю помощника…";
        loop = StartCoroutine(DecisionLoop());
        return true;
    }

    private void DisableControl(string reason)
    {
        active = false;
        requestedRevision = 0; requestedMode = "";
        bridgeSessionReady = false;
        if (pending != null) { pending.Abort(); pending = null; }
        if (loop != null) { StopCoroutine(loop); loop = null; }
        try { executor.Stop(actor, reason); }
        finally
        {
            controller = null;
            actor = null;
            status = reason;
        }
    }

    internal static bool TryGetInput(Player player, out ControlIntent input)
    {
        input = default;
        var plugin = instance;
        if (plugin == null || player != Player.m_localPlayer || !player.IsOwner()) return false;
        if (plugin.showPanel) return true;
        if (!plugin.active || player != plugin.actor) return false;
        input = plugin.executor.ConsumeInput();
        return true;
    }

    internal static bool PlacementControl(Player player, out bool pulse)
    {
        var p = instance; pulse = false;
        if (p == null || !p.placementPatchReady || !p.active || p.actor != player || !player.IsOwner() || player.IsDead() ||
            p.showPanel || Menu.IsVisible() || (Chat.instance && Chat.instance.HasFocus()) || InventoryGui.IsVisible() || !player.InPlaceMode()) return false;
        pulse = p.executor.PlacementInput;
        return true;
    }
    internal static void PlacementInputEnded(Player player) { if (instance?.actor == player) instance.executor.EndPlacementInput(); }
    internal static void PlacementCompleted(Player player, bool success) { if (instance?.actor == player) instance.executor.PlacementResult(success, player); }
    internal static void StructureRepairAttempted(Player player, float health) { if (instance?.actor == player) instance.executor.RepairAttempt(health); }
    internal static bool SayFromAction(string text)
    {
        if (instance == null || !Chat.instance || string.IsNullOrWhiteSpace(text)) return false;
        instance.ChatReply(text);
        return true;
    }

    private Player? FindLeader()
    {
        var local = actor ? actor : Player.m_localPlayer;
        if (local == null || !local) return null;
        var players = Player.GetAllPlayers().Where(p => p && p != local && !p.IsDead());
        if (leaderId.HasValue) return players.FirstOrDefault(p => p.GetPlayerID() == leaderId.Value);
        var chosen = !string.IsNullOrWhiteSpace(leaderName.Value)
            ? players.FirstOrDefault(p => string.Equals(p.GetPlayerName(), leaderName.Value, StringComparison.OrdinalIgnoreCase))
            : players.Where(p => Vector3.Distance(p.transform.position, local.transform.position) <= 60)
                .OrderBy(p => Vector3.SqrMagnitude(p.transform.position - local.transform.position)).FirstOrDefault();
        if (chosen) leaderId = chosen.GetPlayerID();
        return chosen;
    }

    private IEnumerator DecisionLoop()
    {
        while (active && actor != null && actor)
        {
            if (bridgeSessionReady && (showPanel || Menu.IsVisible() || (InventoryGui.IsVisible() && !executor.OwnsInventory) || global::Console.IsVisible() ||
                (Chat.instance != null && Chat.instance.HasFocus())))
            {
                executor.Pause();
                yield return new WaitForSecondsRealtime(.25f);
                continue;
            }
            string previousResult = executor.Result;
            Observation? observation = null;
            string payload = "";
            try
            {
                executor.RefreshOutcomes(actor);
                observation = Observation.Capture(actor, FindLeader(), session, ++sequence, previousResult);
                observation.Data.previous_result = ObservationValues.Text(executor.Result, 240);
                observation.Data.background_planning = true;
                observation.Data.listen_chat = allowChat.Value;
                observation.Data.plan_id = executor.PlanId;
                observation.Data.action_status = executor.ActionStatus;
                observation.Data.supported_actions = Capabilities.Supported(allowCombat.Value, placementPatchReady);
                payload = WireJson.Serialize(observation.Data);
            }
            catch (Exception ex)
            {
                observation = null; status = "Ошибка наблюдения: " + ex.GetType().Name;
                executor.Stop(actor, status); AddLog("observation", ex.ToString());
                Logger.LogWarning("Observation failed: " + ex);
            }
            float started = Time.realtimeSinceStartup;
            if (observation != null)
            {
                using (var request = new UnityWebRequest(bridgeUrl.Value.TrimEnd('/') + "/api/decision", "POST"))
                {
                    request.uploadHandler = new UploadHandlerRaw(Encoding.UTF8.GetBytes(payload));
                    request.downloadHandler = new DownloadHandlerBuffer();
                    request.redirectLimit = 0;
                    request.SetRequestHeader("Content-Type", "application/json");
                    request.SetRequestHeader("Authorization", "Bearer " + bridgeToken.Value);
                    // Bridge budget: 100s including retries; leave transport headroom.
                    request.timeout = 110;
                    if (!bridgeSessionReady) request.timeout = 8;
                    pending = request;
                    yield return request.SendWebRequest();
                    pending = null;
                    if (!active || actor == null || !actor) yield break;
                    if (request.result != UnityWebRequest.Result.Success)
                    {
                        status = BridgeFailure.Describe(request.responseCode, request.downloadHandler.text);
                        if (status != lastFailure) { Logger.LogWarning(status); AddLog("bridge", status); lastFailure = status; }
                        executor.Stop(actor, status);
                    }
                    else if (request.downloadHandler.data.Length > 8192)
                    { status = "Ответ моста слишком большой"; executor.Stop(actor, status); }
                    else
                    {
                        Decision? decision = null;
                        try { decision = WireJson.Deserialize<Decision>(request.downloadHandler.text); }
                        catch (Exception) { status = "Некорректный ответ моста"; }
                        if (DecisionPolicy.Accept(decision, observation.Data, allowCombat.Value, requestedRevision))
                        {
                            bridgeSessionReady = true;
                            if (decision!.mode == "stop" && requestedMode.Length > 0)
                            {
                                AddLog("command", "Служба остановила AI; возвращено ручное управление.");
                                DisableControl("AI остановлен службой. Управление возвращено вам; причина во вкладке «Журнал».");
                                yield break;
                            }
                            executor.Set(decision!, observation, actor);
                            status = decision!.say.Length > 0 ? decision.say : decision.action;
                            LogMotor(decision, observation);
                            lastFailure = "";
                        }
                        else { status = "Действие отклонено локальной проверкой"; executor.Stop(actor, status); }
                    }
                }
            }
            // Let the bounded action execute, then request a fresh observation.
            float remaining = Mathf.Max(Mathf.Min(interval.Value, 2f) - (Time.realtimeSinceStartup - started), .1f);
            yield return new WaitForSecondsRealtime(remaining);
            // Keep transport/schema failures visible even while the F8 panel pauses actions.
        }
    }

    private void SetPanel(bool open)
    {
        if (showPanel == open) return;
        showPanel = open;
        if (open)
        {
            oldCursorLock = Cursor.lockState;
            oldCursorVisible = Cursor.visible;
            Cursor.lockState = CursorLockMode.None;
            Cursor.visible = true;
            PanelOpened();
        }
        else { nativeWindow?.SetVisible(false); Cursor.lockState = oldCursorLock; Cursor.visible = oldCursorVisible; Game.Unpause(); }
    }

    private void OnGUI()
    {
        if (!showPanel) return;
        Cursor.lockState = CursorLockMode.None;
        Cursor.visible = true;
        if (nativeWindow != null && nativeWindow.Ready) return;
        GUI.depth = -1000;
        window.width = Mathf.Min(660, Screen.width - 32);
        window.height = Mathf.Min(720, Screen.height - 40);
        window.x = Mathf.Clamp(window.x, 16, Mathf.Max(16, Screen.width - window.width - 16));
        window.y = Mathf.Clamp(window.y, 20, Mathf.Max(20, Screen.height - window.height - 20));
        window = GUILayout.Window(GetInstanceID(), window, DrawSimplePanel, "AI-помощник");
    }

    private void OnDestroy()
    {
        DisableControl("Мод выгружен");
        SetPanel(false);
        uiKey = "";
        uiClient?.Dispose();
        menuIntegration?.Dispose();
        nativeWindow?.Dispose();
        runtime?.Dispose();
        harmony?.UnpatchSelf();
        if (instance == this) instance = null;
    }
}
