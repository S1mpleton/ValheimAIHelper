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

[BepInPlugin("dev.valheim.aihelper", "Valheim AI Helper", "0.2.0")]
[DefaultExecutionOrder(-100)]
public sealed partial class Plugin : BaseUnityPlugin
{
    private ConfigEntry<string> bridgeUrl = null!, bridgeToken = null!, leaderName = null!;
    private ConfigEntry<bool> allowCombat = null!;
    private ConfigEntry<float> interval = null!;
    private readonly ActionExecutor executor = new();
    private static Plugin? instance;
    private Harmony? harmony;
    private bool inputPatchReady, panelFocusReady;
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
    private int sequence;
    private Rect window = new(20, 80, 480, 340);
    private GUIStyle? statusStyle;

    private void Awake()
    {
        bridgeUrl = Config.Bind("Bridge", "Url", "http://127.0.0.1:8765", "Local bridge URL. Loopback HTTP only.");
        bridgeToken = Config.Bind("Bridge", "Token", "", "Pairing code from .local/bridge-token, NOT an AI API key.");
        leaderName = Config.Bind("Player", "LeaderName", "", "Exact leader character name. Empty: lock on nearest other player when AI is enabled.");
        allowCombat = Config.Bind("Player", "AllowCombat", false, "Allow basic melee against hostile monsters targeting you/leader.");
        interval = Config.Bind("Bridge", "DecisionInterval", 4f, new ConfigDescription("Minimum seconds between decisions (API costs apply).", new AcceptableValueRange<float>(3f, 60f)));
        instance = this;
        InitPanel();
        harmony = new Harmony("dev.valheim.aihelper.input");
        try
        {
            harmony.PatchAll(typeof(PanelInputFocusPatch));
            panelFocusReady = true;
            harmony.PatchAll(typeof(ControlInputPatch));
            inputPatchReady = true;
        }
        catch (Exception ex) { Logger.LogError("Native input hook is incompatible with this game: " + ex); }
        try { harmony.PatchAll(typeof(EscMenuPatch)); }
        catch (Exception ex) { Logger.LogWarning("Esc menu entry: " + ex.GetType().Name + ". Use F8."); }
        Logger.LogInfo("Valheim version: " + global::Version.GetVersionString() + "; build references: 0.221.4");
        Logger.LogInfo("AI Helper loaded. Control is OFF. F8 panel; F9 emergency release.");
    }

    private void Update()
    {
        if (Input.GetKeyDown(KeyCode.F9)) { StopFromPanel(); SetPanel(false); }
        if (Input.GetKeyDown(KeyCode.F8)) { if (showPanel) SetPanel(false); else OpenPanelFromMenu(); }
        if (showPanel && Input.GetKeyDown(KeyCode.Escape)) SetPanel(false);
        UpdatePanel();
        if (active && (actor == null || !actor || actor != Player.m_localPlayer || !actor.IsOwner() || actor.IsDead() ||
            controller == null || !controller || !controller.enabled))
            DisableControl("Нет локального управления персонажем или мир закрыт. AI отключён.");
    }

    private void FixedUpdate()
    {
        if (!active || actor == null || !actor) return;
        if (actor != Player.m_localPlayer || !actor.IsOwner())
        { DisableControl("Сетевая принадлежность персонажа изменилась. AI отключён."); return; }
        if (showPanel || InventoryGui.IsVisible() || Menu.IsVisible() || global::Console.IsVisible() ||
            (Chat.instance != null && Chat.instance.HasFocus()))
        { executor.Stop(actor, "Пауза: открыта панель или игровое меню"); return; }
        try { executor.Tick(actor, FindLeader(), allowCombat.Value); }
        catch (Exception ex)
        {
            Logger.LogWarning("Control stopped: " + ex.GetType().Name);
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
        if (bridgeToken.Value.Length < 32) { status = "Сначала выполните сопряжение пусковым скриптом."; return false; }
        var input = player.GetComponent<PlayerController>();
        if (!input || !input.enabled) { status = "Управление игроком уже перехвачено другим модом."; return false; }
        actor = player;
        controller = input;
        leaderId = null;
        session = Guid.NewGuid().ToString("N");
        sequence = 0;
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
        bridgeSessionReady = false;
        if (pending != null) { pending.Abort(); pending = null; }
        if (loop != null) { StopCoroutine(loop); loop = null; }
        try { executor.Stop(actor, reason); }
        finally
        {
            controller = null;
            actor = null;
            leaderId = null;
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
        input = plugin.executor.Input;
        return true;
    }

    private Player? FindLeader()
    {
        if (actor == null || !actor) return null;
        var players = Player.GetAllPlayers().Where(p => p && p != actor && !p.IsDead());
        if (leaderId.HasValue) return players.FirstOrDefault(p => p.GetPlayerID() == leaderId.Value);
        var chosen = !string.IsNullOrWhiteSpace(leaderName.Value)
            ? players.FirstOrDefault(p => string.Equals(p.GetPlayerName(), leaderName.Value, StringComparison.OrdinalIgnoreCase))
            : players.Where(p => Vector3.Distance(p.transform.position, actor.transform.position) <= 60)
                .OrderBy(p => Vector3.SqrMagnitude(p.transform.position - actor.transform.position)).FirstOrDefault();
        if (chosen) leaderId = chosen.GetPlayerID();
        return chosen;
    }

    private IEnumerator DecisionLoop()
    {
        while (active && actor != null && actor)
        {
            if (bridgeSessionReady && (showPanel || Menu.IsVisible()))
            {
                executor.Stop(actor, "Пауза: открыто меню помощника");
                yield return new WaitForSecondsRealtime(.25f);
                continue;
            }
            // No controls are held while the API is thinking or the connection is down.
            string previousResult = executor.Result;
            executor.Stop(actor, "Ожидаю решение");
            Observation? observation = null;
            string payload = "";
            try
            {
                observation = Observation.Capture(actor, FindLeader(), session, ++sequence, previousResult);
                payload = WireJson.Serialize(observation.Data);
            }
            catch (Exception ex) { observation = null; status = "Ошибка наблюдения: " + ex.GetType().Name; }
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
                        if (status != lastFailure) { Logger.LogWarning(status); lastFailure = status; }
                        executor.Stop(actor, status);
                    }
                    else if (request.downloadHandler.data.Length > 8192)
                    { status = "Ответ моста слишком большой"; executor.Stop(actor, status); }
                    else
                    {
                        Decision? decision = null;
                        try { decision = WireJson.Deserialize<Decision>(request.downloadHandler.text); }
                        catch (Exception) { status = "Некорректный ответ моста"; }
                        if (DecisionPolicy.Accept(decision, observation.Data, allowCombat.Value))
                        {
                            bridgeSessionReady = true;
                            executor.Set(decision!, observation, actor);
                            status = decision!.say.Length > 0 ? decision.say : decision.action;
                            lastFailure = "";
                        }
                        else { status = "Действие отклонено локальной проверкой"; executor.Stop(actor, status); }
                    }
                }
            }
            // Let the bounded action execute, then request a fresh observation.
            float remaining = Mathf.Max(interval.Value - (Time.realtimeSinceStartup - started), 3f);
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
        else { Cursor.lockState = oldCursorLock; Cursor.visible = oldCursorVisible; Game.Unpause(); }
    }

    private void OnGUI()
    {
        if (!showPanel) return;
        Cursor.lockState = CursorLockMode.None;
        Cursor.visible = true;
        GUI.depth = -1000;
        window.width = Mathf.Min(660, Screen.width - 32);
        window.height = Mathf.Min(uiSettings ? 720 : 430, Screen.height - 40);
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
        harmony?.UnpatchSelf();
        if (instance == this) instance = null;
    }
}
