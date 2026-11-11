using System;
using System.Collections;
using System.Linq;
using BepInEx.Configuration;
using UnityEngine;

namespace ValheimAIHelper;

public sealed partial class Plugin
{
    private BridgeUiClient? uiClient;
    private EscMenuIntegration? menuIntegration;
    private BridgeUiStatus? bridgeStatus;
    private ConfigEntry<string> savedProvider = null!, savedModel = null!, savedObjective = null!;
    private ConfigEntry<int> savedApiInterval = null!;
    private string uiProvider = "gemini", uiModel = "", uiKey = "", uiObjective = "", uiInterval = "20";
    private string uiMessage = "", uiError = "";
    private string[] uiModels = Array.Empty<string>();
    private bool uiBusy, uiPolling, uiHydrated, uiSettings, uiModelList;
    private float nextUiPoll;
    private int uiOperation;
    private Vector2 panelScroll, modelScroll;
    private GUIStyle? titleStyle, hintStyle, panelButtonStyle, panelErrorStyle;

    internal static bool PanelOpen => instance != null && instance.showPanel;

    private void InitPanel()
    {
        savedProvider = Config.Bind("AI", "Provider", "openai", "AI provider. API keys are never saved here.");
        savedModel = Config.Bind("AI", "Model", "", "API model name selected in the game panel.");
        savedObjective = Config.Bind("AI", "Objective", "Следуй за лидером и помогай ему выжить.", "Companion objective.");
        savedApiInterval = Config.Bind("AI", "RequestInterval", 20, new ConfigDescription("Minimum seconds between AI decisions.", new AcceptableValueRange<int>(4, 300)));
        uiProvider = savedProvider.Value == "gemini" ? "gemini" : "openai";
        uiModel = savedModel.Value;
        uiObjective = savedObjective.Value;
        uiInterval = savedApiInterval.Value.ToString();
        CreateUiClient();
        menuIntegration = new EscMenuIntegration(OpenPanelFromMenu);
    }

    private void CreateUiClient()
    {
        try { uiClient = new BridgeUiClient(bridgeUrl.Value, bridgeToken.Value); }
        catch (ArgumentException ex) { uiError = ex.Message; uiClient = null; }
    }

    internal static void InstallMenuEntry(Menu menu)
    {
        var plugin = instance;
        if (plugin?.menuIntegration == null) return;
        try { plugin.menuIntegration.Install(menu); }
        catch (Exception ex) { plugin.Logger.LogWarning("AI menu entry: " + ex.GetType().Name + ". F8 remains available."); }
    }

    private void OpenPanelFromMenu()
    {
        if (!panelFocusReady) { Logger.LogWarning("AI panel input focus is incompatible with this game version."); return; }
        if (Menu.instance) Menu.instance.Hide();
        SetPanel(true);
    }

    private void PanelOpened()
    {
        float width = Mathf.Min(660, Screen.width - 32);
        float height = Mathf.Min(700, Screen.height - 40);
        window = new Rect((Screen.width - width) / 2, (Screen.height - height) / 2, width, height);
        if (Player.m_localPlayer) Game.Pause();
        nextUiPoll = 0;
        StartCoroutine(RefreshPanelStatus());
    }

    private void UpdatePanel()
    {
        if (showPanel && !uiPolling && Time.realtimeSinceStartup >= nextUiPoll)
            StartCoroutine(RefreshPanelStatus());
    }

    private IEnumerator RefreshPanelStatus()
    {
        if (uiClient == null || uiPolling) yield break;
        uiPolling = true;
        int operation = uiOperation;
        try
        {
            yield return uiClient.Request<BridgeUiStatus>("/api/status", null, value =>
            {
                if (operation != uiOperation) return;
                bridgeStatus = value;
                if (value.provider_error != null)
                    uiError = value.provider_error.message + (!string.IsNullOrEmpty(value.provider_error.detail) ? " Ответ API: " + value.provider_error.detail : "");
                if (!uiHydrated)
                {
                    if (value.provider is "gemini" or "openai")
                    {
                        uiProvider = value.provider;
                        uiModel = value.model;
                        uiObjective = value.objective;
                        uiInterval = value.request_interval_seconds.ToString();
                    }
                    uiSettings = !HasKey(uiProvider) || string.IsNullOrEmpty(uiModel);
                    uiHydrated = true;
                }
            }, message => { if (!uiBusy) uiError = message; }, 8);
        }
        finally { uiPolling = false; nextUiPoll = Time.realtimeSinceStartup + 2; }
    }

    private bool HasKey(string provider) => bridgeStatus?.keys_present != null &&
        (provider == "gemini" ? bridgeStatus.keys_present.gemini : bridgeStatus.keys_present.openai);

    private bool ValidateAi(out int seconds)
    {
        seconds = 20;
        if (string.IsNullOrWhiteSpace(uiModel)) { uiSettings = true; uiError = "Выберите модель в разделе «Подключение»."; return false; }
        if (string.IsNullOrWhiteSpace(uiKey) && !HasKey(uiProvider)) { uiSettings = true; uiError = "Введите API-ключ в разделе «Подключение»."; return false; }
        if (!int.TryParse(uiInterval, out seconds) || seconds < 4 || seconds > 300)
        { uiSettings = true; uiError = "Пауза между решениями должна быть от 4 до 300 секунд."; return false; }
        return true;
    }

    private void SaveNonSecretSettings(int seconds)
    {
        savedProvider.Value = uiProvider;
        savedModel.Value = uiModel.Trim();
        savedObjective.Value = uiObjective;
        savedApiInterval.Value = seconds;
    }

    private IEnumerator SaveAiSettings(int operation, Action saved)
    {
        if (uiClient == null || !ValidateAi(out int seconds)) yield break;
        bool ok = false, keyPresent = false;
        var options = new ProviderOptions { provider = uiProvider, model = uiModel.Trim(), api_key = uiKey.Trim(), request_interval_seconds = seconds };
        yield return uiClient.Request<BridgeOk>("/api/provider", options, value => { ok = value.ok; keyPresent = value.key_present; }, error => { if (operation == uiOperation) uiError = error; });
        options.api_key = "";
        if (operation != uiOperation || !ok) yield break;
        uiKey = "";
        bridgeStatus ??= new BridgeUiStatus();
        bridgeStatus.keys_present ??= new BridgeKeys();
        if (uiProvider == "gemini") bridgeStatus.keys_present.gemini = keyPresent;
        else bridgeStatus.keys_present.openai = keyPresent;
        bridgeStatus.provider = uiProvider;
        bridgeStatus.model = uiModel.Trim();
        bridgeStatus.provider_error = null;
        SaveNonSecretSettings(seconds);
        yield return RefreshPanelStatus();
        if (operation == uiOperation) saved();
    }

    private IEnumerator ApplySettings()
    {
        int operation = ++uiOperation;
        uiBusy = true; uiError = ""; uiMessage = "Сохраняю подключение…";
        try
        {
            yield return SaveAiSettings(operation, () => { uiMessage = "Подключение сохранено. Можно включать AI."; uiSettings = false; });
        }
        finally { if (operation == uiOperation) uiBusy = false; }
    }

    private IEnumerator StartMode(string mode)
    {
        int operation = ++uiOperation;
        uiBusy = true; uiError = ""; uiMessage = "Подключаю помощника…";
        try
        {
            if (uiClient == null) { uiError = "Служба AI не подключена."; yield break; }
            if (mode == "ai")
            {
                bool saved = false;
                yield return SaveAiSettings(operation, () => saved = true);
                if (!saved || operation != uiOperation) yield break;
            }
            if (!active && !EnableControl()) { uiError = status; yield break; }
            float until = Time.realtimeSinceStartup + 12;
            while (active && !bridgeSessionReady && Time.realtimeSinceStartup < until && operation == uiOperation) yield return null;
            if (!active || !bridgeSessionReady || operation != uiOperation)
            { if (operation == uiOperation) uiError = "Нет данных персонажа от службы AI. " + status; yield break; }
            bool started = false;
            yield return uiClient.Request<BridgeOk>("/api/command", new BridgeCommand { mode = mode, objective = uiObjective }, value => started = value.ok, error => uiError = error, 8);
            if (!started || operation != uiOperation) yield break;
            uiSettings = false;
            uiMessage = mode == "follow" ? "Помощник следует за лидером." : "AI выполняет задачу.";
            if (Menu.instance) Menu.instance.Hide();
            SetPanel(false);
            nextUiPoll = 0;
        }
        finally { if (operation == uiOperation) uiBusy = false; }
    }

    private void StopFromPanel()
    {
        ++uiOperation;
        DisableControl("Помощник остановлен. Управление возвращено вам.");
        uiClient?.Dispose();
        CreateUiClient();
        uiBusy = true; uiError = ""; uiMessage = "Помощник остановлен.";
        StartCoroutine(PostStop());
    }

    private IEnumerator PostStop()
    {
        int operation = uiOperation;
        try
        {
            if (uiClient != null)
                yield return uiClient.Request<BridgeOk>("/api/command", new BridgeCommand { mode = "stop", objective = uiObjective }, _ => { }, _ => { }, 5);
        }
        finally { if (operation == uiOperation) uiBusy = false; }
    }

    private IEnumerator FetchModels()
    {
        int operation = ++uiOperation;
        uiBusy = true; uiError = ""; uiMessage = "Получаю список моделей…";
        try
        {
            if (uiClient == null) yield break;
            yield return uiClient.Request<ModelListReply>("/api/models/" + uiProvider, new ModelListRequest { api_key = uiKey.Trim() }, value =>
            {
                if (operation != uiOperation) return;
                uiModels = value.models ?? Array.Empty<string>();
                uiModelList = true;
                if (uiModel.Length == 0)
                {
                    string preferred = uiProvider == "openai" ? "gpt-6-luna" : "gemini-3.8-flash";
                    if (Array.IndexOf(uiModels, preferred) >= 0) { uiModel = preferred; uiModelList = false; }
                }
                uiMessage = uiModels.Length == 0 ? "Список моделей пуст." : uiModel.Length > 0 ? "Модель выбрана. Нажмите «AI: выполнить задачу» или сохраните подключение." : "Выберите модель из списка.";
            }, error => { if (operation == uiOperation) uiError = error; });
        }
        finally { if (operation == uiOperation) uiBusy = false; }
    }

    private IEnumerator CheckModel()
    {
        int operation = ++uiOperation;
        uiBusy = true; uiError = ""; uiMessage = "Проверяю модель…";
        try
        {
            if (uiClient == null || !ValidateAi(out _)) yield break;
            yield return uiClient.Request<ModelCheckReply>("/api/check/" + uiProvider, new ModelCheckRequest { model = uiModel.Trim(), api_key = uiKey.Trim() }, value =>
            {
                if (operation == uiOperation) uiMessage = "Модель отвечает. " + string.Join("; ", value.checks ?? Array.Empty<string>());
            }, error => { if (operation == uiOperation) uiError = error; });
        }
        finally { if (operation == uiOperation) uiBusy = false; }
    }

    private void DrawSimplePanel(int id)
    {
        titleStyle ??= new GUIStyle(GUI.skin.label) { fontSize = 21, fontStyle = FontStyle.Bold, wordWrap = true };
        statusStyle ??= new GUIStyle(GUI.skin.label) { fontSize = 14, wordWrap = true };
        hintStyle ??= new GUIStyle(GUI.skin.label) { fontSize = 12, wordWrap = true };
        panelErrorStyle ??= new GUIStyle(statusStyle) { normal = { textColor = new Color(1f, .65f, .55f) } };
        panelButtonStyle ??= new GUIStyle(GUI.skin.button) { fontSize = 15, wordWrap = true, padding = new RectOffset(10, 10, 9, 9) };
        panelScroll = GUILayout.BeginScrollView(panelScroll, GUILayout.Height(window.height - 52));
        GUILayout.Label("AI-помощник", titleStyle);
        string mode = !active ? "Остановлен — персонажем управляете вы" : bridgeStatus?.mode == "follow" ? "Следует за лидером" : bridgeStatus?.mode == "ai" ? "Выполняет задачу" : "Готов к команде";
        GUILayout.Label(mode, statusStyle);
        GUILayout.Label("Помощником становится текущий персонаж. Лидер — другой игрок рядом.", hintStyle);
        if (uiError.Length > 0) GUILayout.Label(uiError, panelErrorStyle);
        else if (uiMessage.Length > 0) GUILayout.Label(uiMessage, statusStyle);
        GUILayout.Space(12);
        GUILayout.Label("Что нужно сделать?", statusStyle);
        uiObjective = GUILayout.TextArea(uiObjective, 1000, GUILayout.Height(70));
        GUI.enabled = !uiBusy;
        GUILayout.BeginHorizontal();
        if (GUILayout.Button("Следовать", panelButtonStyle)) StartCoroutine(StartMode("follow"));
        if (GUILayout.Button("AI: выполнить задачу", panelButtonStyle)) StartCoroutine(StartMode("ai"));
        GUI.enabled = true;
        if (GUILayout.Button("Стоп", panelButtonStyle)) StopFromPanel();
        GUILayout.EndHorizontal();
        GUILayout.Label("Следование не требует ключа и не расходует квоту. F9 — немедленный стоп.", hintStyle);
        GUILayout.Space(12);
        string connection = uiModel.Length > 0 ? (uiProvider == "gemini" ? "Gemini" : "OpenAI") + " · " + uiModel : "Нейросеть не подключена";
        if (GUILayout.Button((uiSettings ? "▼ " : "▶ ") + "Подключение: " + connection, panelButtonStyle)) uiSettings = !uiSettings;
        if (uiSettings) DrawConnectionSettings();
        GUILayout.Space(10);
        if (GUILayout.Button("Назад в меню", panelButtonStyle))
        { SetPanel(false); if (Menu.instance) Menu.instance.Show(); }
        GUILayout.Label("Valheim AI Helper " + typeof(Plugin).Assembly.GetName().Version?.ToString(3) + " · F8 — панель · F9 — стоп", hintStyle);
        GUILayout.EndScrollView();
        GUI.enabled = true;
        GUI.DragWindow(new Rect(0, 0, window.width, 26));
    }

    private void DrawConnectionSettings()
    {
        GUI.enabled = !uiBusy;
        GUILayout.BeginHorizontal();
        if (GUILayout.Toggle(uiProvider == "gemini", "Google Gemini", GUI.skin.button) && uiProvider != "gemini")
        { uiProvider = "gemini"; uiModel = ""; uiModels = Array.Empty<string>(); uiKey = ""; }
        if (GUILayout.Toggle(uiProvider == "openai", "OpenAI", GUI.skin.button) && uiProvider != "openai")
        { uiProvider = "openai"; uiModel = ""; uiModels = Array.Empty<string>(); uiKey = ""; }
        GUILayout.EndHorizontal();
        GUILayout.Label(HasKey(uiProvider) ? "API-ключ уже задан. Введите новый, только если хотите заменить его." : "API-ключ", hintStyle);
        string enteredKey = GUILayout.PasswordField(uiKey, '•', 512, GUILayout.Height(29));
        if (enteredKey != uiKey) { uiKey = enteredKey; uiModels = Array.Empty<string>(); uiModelList = false; uiError = ""; }
        GUILayout.Label("Модель", hintStyle);
        uiModel = GUILayout.TextField(uiModel, 100, GUILayout.Height(29));
        {
            if (GUILayout.Button(uiModels.Length > 0 ? "Выбрать другую модель" : "Получить модели", panelButtonStyle))
            { if (uiModels.Length > 0) uiModelList = !uiModelList; else StartCoroutine(FetchModels()); }
            if (uiModelList && uiModels.Length > 0)
            {
                modelScroll = GUILayout.BeginScrollView(modelScroll, GUILayout.Height(150));
                foreach (string model in uiModels)
                    if (GUILayout.Button((uiModel == model ? "✓ " : "") + model, GUI.skin.button)) { uiModel = model; uiModelList = false; }
                GUILayout.EndScrollView();
            }
        }
        GUILayout.Label("Пауза между решениями AI, секунды (20 — около 3 решений в минуту)", hintStyle);
        uiInterval = GUILayout.TextField(uiInterval, 3, GUILayout.Height(29));
        GUILayout.BeginHorizontal();
        if (GUILayout.Button("Сохранить", panelButtonStyle)) StartCoroutine(ApplySettings());
        if (GUILayout.Button("Проверить модель", panelButtonStyle)) StartCoroutine(CheckModel());
        GUILayout.EndHorizontal();
        GUILayout.Label("Ключ хранится только до выключения службы AI. Проверка модели и AI расходуют квоту API.", hintStyle);
        GUI.enabled = true;
    }
}
