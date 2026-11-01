using System;
using System.Collections;
using System.Linq;
using System.Text;
using BepInEx;
using BepInEx.Configuration;
using UnityEngine;
using UnityEngine.Networking;

namespace ValheimAIHelper;

[BepInPlugin("dev.valheim.aihelper", "Valheim AI Helper", "0.1.0")]
[DefaultExecutionOrder(-100)]
public sealed class Plugin : BaseUnityPlugin
{
    private ConfigEntry<string> bridgeUrl = null!, bridgeToken = null!, leaderName = null!;
    private ConfigEntry<bool> allowCombat = null!;
    private ConfigEntry<float> interval = null!;
    private readonly ActionExecutor executor = new();
    private Player? actor;
    private PlayerController? controller;
    private bool oldControllerEnabled, active, showPanel;
    private CursorLockMode oldCursorLock;
    private bool oldCursorVisible;
    private long? leaderId;
    private Coroutine? loop;
    private UnityWebRequest? pending;
    private string session = "", status = "F8 — панель. F9 — ручное управление.";
    private int sequence;
    private Rect window = new(20, 80, 480, 340);

    private void Awake()
    {
        bridgeUrl = Config.Bind("Bridge", "Url", "http://127.0.0.1:8765", "Local bridge URL. Loopback HTTP only.");
        bridgeToken = Config.Bind("Bridge", "Token", "", "Pairing code from .local/bridge-token, NOT an AI API key.");
        leaderName = Config.Bind("Player", "LeaderName", "", "Exact leader character name. Empty: lock on nearest other player when AI is enabled.");
        allowCombat = Config.Bind("Player", "AllowCombat", false, "Allow basic melee against hostile monsters targeting you/leader.");
        interval = Config.Bind("Bridge", "DecisionInterval", 4f, new ConfigDescription("Minimum seconds between decisions (API costs apply).", new AcceptableValueRange<float>(3f, 60f)));
        Logger.LogInfo("AI Helper loaded. Control is OFF. F8 panel; F9 emergency release.");
    }

    private void Update()
    {
        if (Input.GetKeyDown(KeyCode.F9)) { DisableControl("Ручное управление (F9)"); SetPanel(false); }
        if (Input.GetKeyDown(KeyCode.F8)) SetPanel(!showPanel);
        if (active && (actor == null || !actor || actor != Player.m_localPlayer || actor.IsDead()))
            DisableControl("Персонаж погиб или мир закрыт. AI отключён.");
    }

    private void FixedUpdate()
    {
        if (!active || actor == null || !actor) return;
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
        oldControllerEnabled = input.enabled;
        input.enabled = false;
        leaderId = null;
        session = Guid.NewGuid().ToString("N");
        sequence = 0;
        active = true;
        status = "Подключаю мост. Затем выберите режим в локальной панели.";
        loop = StartCoroutine(DecisionLoop());
        return true;
    }

    private void DisableControl(string reason)
    {
        active = false;
        if (pending != null) { pending.Abort(); pending = null; }
        if (loop != null) { StopCoroutine(loop); loop = null; }
        try { executor.Stop(actor, reason); }
        finally
        {
            if (controller != null && controller) controller.enabled = oldControllerEnabled;
            controller = null;
            actor = null;
            leaderId = null;
            status = reason;
        }
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
            // No controls are held while the API is thinking or the connection is down.
            executor.Stop(actor, "Ожидаю решение");
            Observation? observation = null;
            try { observation = Observation.Capture(actor, FindLeader(), session, ++sequence, status); }
            catch (Exception ex) { status = "Ошибка наблюдения: " + ex.GetType().Name; }
            float started = Time.realtimeSinceStartup;
            if (observation != null)
            {
                using (var request = new UnityWebRequest(bridgeUrl.Value.TrimEnd('/') + "/api/decision", "POST"))
                {
                    request.uploadHandler = new UploadHandlerRaw(Encoding.UTF8.GetBytes(JsonUtility.ToJson(observation.Data)));
                    request.downloadHandler = new DownloadHandlerBuffer();
                    request.SetRequestHeader("Content-Type", "application/json");
                    request.SetRequestHeader("Authorization", "Bearer " + bridgeToken.Value);
                    request.timeout = 17;
                    pending = request;
                    yield return request.SendWebRequest();
                    pending = null;
                    if (!active || actor == null || !actor) yield break;
                    if (request.result != UnityWebRequest.Result.Success)
                    {
                        status = "Мост недоступен (HTTP " + request.responseCode + "). Управление остановлено.";
                        executor.Stop(actor, status);
                    }
                    else if (request.downloadHandler.data.Length > 8192)
                    { status = "Ответ моста слишком большой"; executor.Stop(actor, status); }
                    else
                    {
                        Decision? decision = null;
                        try { decision = JsonUtility.FromJson<Decision>(request.downloadHandler.text); }
                        catch (Exception) { status = "Некорректный ответ моста"; }
                        if (DecisionPolicy.Accept(decision, observation.Data, allowCombat.Value))
                        {
                            executor.Set(decision!, observation, actor);
                            status = decision!.say.Length > 0 ? decision.say : decision.action;
                        }
                        else { status = "Действие отклонено локальной проверкой"; executor.Stop(actor, status); }
                    }
                }
            }
            // Let the bounded action execute, then request a fresh observation.
            float remaining = Mathf.Max(interval.Value - (Time.realtimeSinceStartup - started), 3f);
            yield return new WaitForSecondsRealtime(remaining);
            if (active) status = executor.Result;
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
        }
        else { Cursor.lockState = oldCursorLock; Cursor.visible = oldCursorVisible; }
    }

    private void OnGUI()
    {
        if (!showPanel) return;
        Cursor.lockState = CursorLockMode.None;
        Cursor.visible = true;
        window = GUILayout.Window(GetInstanceID(), window, DrawPanel, "Valheim AI Helper · 0.1");
    }

    private void DrawPanel(int id)
    {
        GUILayout.Label("Этот мод управляет ТЕКУЩИМ персонажем.\nВключайте его только в отдельном клиенте напарника.");
        GUILayout.Space(10);
        GUILayout.Label(active ? "Управление AI: включено" : "Управление AI: выключено");
        GUILayout.Label(status);
        GUILayout.Label("Действие: " + executor.Result);
        GUILayout.Space(10);
        if (!active && GUILayout.Button("Включить для этого персонажа"))
        { if (EnableControl()) SetPanel(false); }
        if (active && GUILayout.Button("Отключить и вернуть управление (F9)")) DisableControl("Ручное управление");
        bool combat = GUILayout.Toggle(allowCombat.Value, "Разрешить простой ближний бой (экспериментально)");
        if (combat != allowCombat.Value) allowCombat.Value = combat;
        GUILayout.Label("Задача и GPT/Gemini выбираются в локальной веб-панели.\nF8 — закрыть панель. F9 — немедленно отключить AI.");
        if (GUILayout.Button("Закрыть")) SetPanel(false);
        GUI.DragWindow();
    }

    private void OnDestroy()
    {
        DisableControl("Мод выгружен");
        SetPanel(false);
    }
}
