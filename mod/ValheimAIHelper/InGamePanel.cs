using System;
using System.Collections;
using System.Linq;
using BepInEx.Configuration;
using UnityEngine;

namespace ValheimAIHelper;

public sealed partial class Plugin
{
    private const string CompanionObjective = "Следуй за лидером и помогай ему выжить.";
    private BridgeUiClient? uiClient;
    private EscMenuIntegration? menuIntegration;
    private BridgeUiStatus? bridgeStatus;
    private ConfigEntry<string> savedProvider = null!, savedModel = null!, savedObjective = null!, savedOllamaUrl = null!;
    private ConfigEntry<int> savedApiInterval = null!;
    private string uiProvider = "gemini", uiModel = "", uiKey = "", uiObjective = "", uiInterval = "20";
    private string uiOllamaUrl = AiProviders.DefaultOllamaUrl;
    private string uiMessage = "", uiError = "";
    private string[] uiModels = Array.Empty<string>();
    private bool uiBusy, uiPolling, uiHydrated, uiSettings, uiModelList, uiJournal, uiAdditional;
    private float nextUiPoll;
    private int uiOperation;
    private string loggedUiError = "", lastStatusError = "";
    private Vector2 panelScroll, modelScroll;
    private GUIStyle? titleStyle, hintStyle, panelButtonStyle, panelErrorStyle;

    internal static bool PanelOpen => instance != null && instance.showPanel;

    private void InitPanel()
    {
        savedProvider = Config.Bind("AI", "Provider", "openai", "AI provider. API keys are never saved here.");
        savedModel = Config.Bind("AI", "Model", "", "API model name selected in the game panel.");
        savedOllamaUrl = Config.Bind("AI", "OllamaUrl", AiProviders.DefaultOllamaUrl, "Local Ollama HTTP URL on this PC. No API key is required.");
        savedObjective = Config.Bind("AI", "Objective", CompanionObjective, "Last chat objective; normal interaction uses the in-game chat.");
        savedApiInterval = Config.Bind("AI", "RequestInterval", 20, new ConfigDescription("Minimum seconds between AI decisions.", new AcceptableValueRange<int>(4, 300)));
        uiProvider = AiProviders.Known(savedProvider.Value) ? savedProvider.Value : "openai";
        uiOllamaUrl = savedOllamaUrl.Value;
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
        try
        {
            bool wasInstalled = plugin.menuIntegration.Installed;
            plugin.menuIntegration.Install(menu);
            if (!wasInstalled && plugin.menuIntegration.Installed) plugin.AddLog("menu", "Пункт «Управление персонажем» добавлен в Esc. " + GameUi.Describe(menu));
            if (!plugin.menuIntegration.Installed)
            {
                string detail = "Не найден образец кнопки Esc. " + GameUi.Describe(menu);
                if (plugin.menuProbeError != detail) { plugin.menuProbeError = detail; plugin.AddLog("menu", detail); }
            }
        }
        catch (Exception ex) { plugin.AddLog("menu", ex.ToString() + "; " + GameUi.Describe(menu)); }
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
        try
        {
            nativeWindow ??= new NativeControlWindow(this);
            nativeWindow.Open();
        }
        catch (Exception ex) { AddLog("ui", "Игровое окно: " + ex + ". Открыта резервная панель F8."); }
        nextUiPoll = 0;
        StartCoroutine(RefreshPanelStatus());
    }

    private void UpdatePanel()
    {
        if (uiError != loggedUiError) { loggedUiError = uiError; if (uiError.Length > 0) AddLog("error", uiError); }
        try { nativeWindow?.Refresh(); }
        catch (Exception ex) { nativeWindow?.Dispose(); AddLog("ui", "Ошибка игрового окна: " + ex.GetType().Name + ". Используется резервная панель."); }
        if ((showPanel || active || (allowChat.Value && Player.m_localPlayer)) && !uiPolling && Time.realtimeSinceStartup >= nextUiPoll)
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
                if (uiError == lastStatusError) uiError = "";
                lastStatusError = "";
                ImportEvents(value);
                if (!uiBusy && value.mode == "ai" && value.objective != savedObjective.Value) { uiObjective = value.objective; savedObjective.Value = value.objective; }
                if (value.provider_error != null)
                    uiError = value.provider_error.message + (!string.IsNullOrEmpty(value.provider_error.detail) ? " Ответ API: " + value.provider_error.detail : "");
                if (!uiHydrated)
                {
                    if (AiProviders.Known(value.provider))
                    {
                        uiProvider = value.provider;
                        uiModel = value.model;
                        if (value.provider == "ollama" && !string.IsNullOrEmpty(value.ollama_url)) uiOllamaUrl = value.ollama_url;
                        uiObjective = value.objective;
                        uiInterval = value.request_interval_seconds.ToString();
                    }
                    uiSettings = !ProviderReady(uiProvider) || string.IsNullOrEmpty(uiModel);
                    uiHydrated = true;
                }
            }, message => { if (!uiBusy) { lastStatusError = message; uiError = message; } }, 8);
        }
        finally { uiPolling = false; nextUiPoll = Time.realtimeSinceStartup + 2; }
    }

    private bool HasKey(string provider) => bridgeStatus?.keys_present != null &&
        (provider == "gemini" ? bridgeStatus.keys_present.gemini : bridgeStatus.keys_present.openai);
    private bool ProviderReady(string provider) => provider == "ollama" || HasKey(provider);
    private bool CanInferChat => ProviderReady(uiProvider) && bridgeStatus?.provider == uiProvider &&
        !string.IsNullOrEmpty(uiModel) && bridgeStatus.model == uiModel.Trim();

    private void SelectProvider(string provider)
    {
        if (uiProvider == provider) return;
        StopFromPanel();
        uiProvider = provider; uiModel = uiKey = ""; uiModels = Array.Empty<string>();
        uiMessage = provider == "ollama" ? "Запустите Ollama и получите список установленных моделей. API-ключ не нужен." : "Выберите модель и сохраните подключение.";
    }

    private bool ValidateAi(out int seconds)
    {
        seconds = 20;
        if (string.IsNullOrWhiteSpace(uiModel)) { uiSettings = true; uiError = "Выберите модель в разделе «Подключение»."; return false; }
        if (AiProviders.RequiresKey(uiProvider) && string.IsNullOrWhiteSpace(uiKey) && !HasKey(uiProvider)) { uiSettings = true; uiError = "Введите API-ключ в разделе «Подключение»."; return false; }
        if (uiProvider == "ollama" && !BridgeAddress.IsValid(uiOllamaUrl.Trim()))
        { uiSettings = true; uiError = "Ollama: укажите локальный HTTP-адрес, например http://127.0.0.1:11434."; return false; }
        if (!int.TryParse(uiInterval, out seconds) || seconds < 4 || seconds > 300)
        { uiSettings = true; uiError = "Пауза между решениями должна быть от 4 до 300 секунд."; return false; }
        return true;
    }

    private void SaveNonSecretSettings(int seconds)
    {
        savedProvider.Value = uiProvider;
        savedModel.Value = uiModel.Trim();
        if (BridgeAddress.IsValid(uiOllamaUrl.Trim())) savedOllamaUrl.Value = uiOllamaUrl.Trim();
        savedObjective.Value = uiObjective;
        savedApiInterval.Value = seconds;
    }

    private IEnumerator SaveAiSettings(int operation, Action saved)
    {
        if (uiClient == null || !ValidateAi(out int seconds)) yield break;
        bool ok = false, keyPresent = false;
        var options = new ProviderOptions { provider = uiProvider, model = uiModel.Trim(), api_key = AiProviders.RequiresKey(uiProvider) ? uiKey.Trim() : "",
            ollama_url = uiOllamaUrl.Trim(), request_interval_seconds = seconds };
        journal.RegisterSecret(options.api_key);
        yield return uiClient.Request<BridgeOk>("/api/provider", options, value => { ok = value.ok; keyPresent = value.key_present; }, error => { if (operation == uiOperation) uiError = error; });
        options.api_key = "";
        if (operation != uiOperation || !ok) yield break;
        uiKey = "";
        bridgeStatus ??= new BridgeUiStatus();
        bridgeStatus.keys_present ??= new BridgeKeys();
        if (uiProvider == "gemini") bridgeStatus.keys_present.gemini = keyPresent;
        else if (uiProvider == "openai") bridgeStatus.keys_present.openai = keyPresent;
        bridgeStatus.provider = uiProvider;
        bridgeStatus.model = uiModel.Trim();
        bridgeStatus.ollama_url = uiOllamaUrl.Trim();
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
        bool completed = false;
        // Configuring a provider intentionally stops the bridge. Don't confuse
        // that handshake with a stop issued after this new command is accepted.
        requestedMode = ""; requestedRevision = 0;
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
            yield return uiClient.Request<BridgeOk>("/api/command", new BridgeCommand { mode = mode, objective = uiObjective }, value =>
            {
                if (operation != uiOperation) return;
                started = value.ok;
                if (started) { requestedMode = mode; requestedRevision = value.revision; }
            }, error => uiError = error, 8);
            if (!started || operation != uiOperation) yield break;
            completed = true;
            AddLog("command", mode + ": " + uiObjective);
            uiSettings = false;
            uiMessage = mode == "follow" ? "Помощник следует за лидером." : "AI выполняет задачу.";
            if (Menu.instance) Menu.instance.Hide();
            SetPanel(false);
            nextUiPoll = 0;
        }
        finally
        {
            if (operation == uiOperation)
            {
                uiBusy = false;
                if (!completed && active) DisableControl("Команда не запущена. " + uiError);
            }
        }
    }

    private void StopFromPanel()
    {
        ++uiOperation;
        chatQueue.Clear();
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
            journal.RegisterSecret(uiKey.Trim());
            yield return uiClient.Request<ModelListReply>("/api/models/" + uiProvider, new ModelListRequest { api_key = AiProviders.RequiresKey(uiProvider) ? uiKey.Trim() : "", ollama_url = uiOllamaUrl.Trim() }, value =>
            {
                if (operation != uiOperation) return;
                uiModels = value.models ?? Array.Empty<string>();
                uiModelList = true;
                if (uiModel.Length == 0)
                {
                    string preferred = uiProvider == "openai" ? "gpt-6-luna" : "gemini-3.8-flash";
                    if (uiProvider == "ollama") preferred = uiModels.Length == 1 ? uiModels[0] : "";
                    if (Array.IndexOf(uiModels, preferred) >= 0) { uiModel = preferred; uiModelList = false; }
                }
                uiMessage = uiModels.Length == 0 ? uiProvider == "ollama" ? "В Ollama нет локальных моделей. Загрузите текстовую модель через Ollama, затем обновите список." : "Список моделей пуст."
                    : uiModel.Length > 0 ? "Модель выбрана. Сохраните подключение или нажмите «Включить AI»." : "Выберите модель из списка.";
            }, error => { if (operation == uiOperation) uiError = error; });
        }
        finally { if (operation == uiOperation) uiBusy = false; }
    }

    private IEnumerator CheckModel()
    {
        int operation = ++uiOperation;
        uiBusy = true; uiError = ""; uiMessage = uiProvider == "ollama" ? "Проверяю Ollama… Первая загрузка модели может занять до 5 минут. F9 — отмена." : "Проверяю модель…";
        try
        {
            if (uiClient == null || !ValidateAi(out _)) yield break;
            journal.RegisterSecret(uiKey.Trim());
            yield return uiClient.Request<ModelCheckReply>("/api/check/" + uiProvider, new ModelCheckRequest { model = uiModel.Trim(), api_key = AiProviders.RequiresKey(uiProvider) ? uiKey.Trim() : "", ollama_url = uiOllamaUrl.Trim() }, value =>
            {
                if (operation == uiOperation) uiMessage = "Модель отвечает. " + string.Join("; ", value.checks ?? Array.Empty<string>());
            }, error => { if (operation == uiOperation) uiError = error; }, AiProviders.UiTimeout(uiProvider));
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
        GUILayout.Label("Управление персонажем", titleStyle);
        string mode = !active ? "Остановлен — персонажем управляете вы" : bridgeStatus?.mode == "follow" ? "Следует за лидером" : bridgeStatus?.mode == "ai" ? "Выполняет задачу" : "Готов к команде";
        GUILayout.Label(mode, statusStyle);
        GUILayout.Label("Помощником становится текущий персонаж. Лидер — другой игрок рядом.", hintStyle);
        if (uiError.Length > 0) GUILayout.Label(uiError, panelErrorStyle);
        else if (uiMessage.Length > 0) GUILayout.Label(uiMessage, statusStyle);
        GUILayout.BeginHorizontal();
        if (GUILayout.Button((!uiSettings && !uiJournal ? "● " : "") + "Персонаж", panelButtonStyle)) { uiSettings = uiJournal = false; }
        if (GUILayout.Button((uiSettings ? "● " : "") + "Подключение AI", panelButtonStyle)) { uiSettings = true; uiJournal = false; }
        if (GUILayout.Button((uiJournal ? "● " : "") + "Журнал", panelButtonStyle)) { uiJournal = true; uiSettings = false; }
        GUILayout.EndHorizontal();
        if (uiJournal)
        {
            GUILayout.BeginHorizontal();
            if (GUILayout.Button("Копировать отчёт", panelButtonStyle)) CopyReport();
            if (GUILayout.Button("Сохранить", panelButtonStyle)) SaveReport();
            if (GUILayout.Button("Очистить", panelButtonStyle)) journal.Clear();
            GUILayout.EndHorizontal();
            GUILayout.TextArea(journal.Text, GUILayout.MinHeight(230));
            if (GUILayout.Button("Перезапустить службу", panelButtonStyle)) { StopFromPanel(); StartLocalRuntime(); }
        }
        else if (uiSettings) DrawConnectionSettings();
        else if (uiAdditional)
        {
            if (GUILayout.Button("‹ Персонаж", panelButtonStyle)) uiAdditional = false;
            GUILayout.Label("Дополнительно", titleStyle);
            allowCombat.Value = GUILayout.Toggle(allowCombat.Value, "Разрешить бой и защиту лидера");
            autoSurvival.Value = GUILayout.Toggle(autoSurvival.Value, "Автоматически есть доступную еду");
            if (GUILayout.Button("Управление персонажем: " + (controlByAllPlayers.Value ? "Все игроки" : "Только лидер"), panelButtonStyle)) ToggleChatControlScope();
            GUILayout.Label("Изменение доступа останавливает текущую задачу. Сообщения разрешённых игроков могут отправляться AI.", hintStyle);
        }
        else
        {
        var leader = FindLeader();
        if (GUILayout.Button("Лидер: " + (leader ? leader!.GetPlayerName() : "не выбран") + " · сменить", panelButtonStyle)) SelectNextLeader();
        GUI.enabled = !uiBusy;
        GUILayout.BeginHorizontal();
        if (GUILayout.Button("Следовать", panelButtonStyle)) StartCoroutine(StartMode("follow"));
        if (GUILayout.Button("Включить AI", panelButtonStyle)) { uiObjective = CompanionObjective; StartCoroutine(StartMode("ai")); }
        GUI.enabled = true;
        if (GUILayout.Button("Стоп", panelButtonStyle)) StopFromPanel();
        GUILayout.EndHorizontal();
        GUILayout.Label("Следование не требует ключа и не расходует квоту. F9 — немедленный стоп.", hintStyle);
        GUILayout.Space(12);
        if (GUILayout.Button("Дополнительно ›", panelButtonStyle)) uiAdditional = true;
        GUILayout.Label("Обратитесь по имени персонажа: «Бъерн, подбери камни». Продолжения без имени AI понимает по контексту.", hintStyle);
        }
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
            SelectProvider("gemini");
        if (GUILayout.Toggle(uiProvider == "openai", "OpenAI", GUI.skin.button) && uiProvider != "openai")
            SelectProvider("openai");
        if (GUILayout.Toggle(uiProvider == "ollama", "Ollama", GUI.skin.button) && uiProvider != "ollama")
            SelectProvider("ollama");
        GUILayout.EndHorizontal();
        if (uiProvider == "ollama")
        {
            GUILayout.Label("Адрес Ollama на этом ПК. API-ключ не нужен.", hintStyle);
            uiOllamaUrl = GUILayout.TextField(uiOllamaUrl, 256, GUILayout.Height(29));
        }
        else
        {
        GUILayout.Label(HasKey(uiProvider) ? "API-ключ уже задан. Введите новый, только если хотите заменить его." : "API-ключ", hintStyle);
        string enteredKey = GUILayout.PasswordField(uiKey, '•', 512, GUILayout.Height(29));
        if (enteredKey != uiKey) { uiKey = enteredKey; uiModels = Array.Empty<string>(); uiModelList = false; uiError = ""; }
        }
        GUILayout.Label("Модель", hintStyle);
        uiModel = GUILayout.TextField(uiModel, 200, GUILayout.Height(29));
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
        GUILayout.Label(uiProvider == "ollama" ? "Нужна установленная текстовая модель Ollama. Первая загрузка может занять несколько минут; F9 отменяет ожидание."
            : "Ключ хранится только до выключения службы AI. Проверка модели и AI расходуют квоту API.", hintStyle);
        GUI.enabled = true;
    }
}
