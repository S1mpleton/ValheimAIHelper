using System;
using System.Collections.Generic;
using System.Linq;
using HarmonyLib;
using UnityEngine;
using UnityEngine.Events;

namespace ValheimAIHelper;

public sealed partial class Plugin
{
    // Use the game's loaded UGUI/TMP types, font and effective canvas scale.
    // Reflection keeps UI assemblies out of the distributable plugin.
    private sealed class NativeControlWindow : IDisposable
    {
        private readonly Plugin p;
        private GameObject? root, frame, characterMain, characterAdvanced;
        private Component? textTemplate, canvas;
        private readonly List<GameObject> pages = new();
        private readonly List<Component> busyButtons = new();
        private readonly List<Component> tabs = new();
        private readonly List<GameObject> tabMarkers = new();
        private Component? stateLabel, messageLabel, leaderLabel, combatButton, foodButton, controlButton, logText;
        private Component? keyInput, modelInput, intervalInput, providerButton, keyStatusLabel;
        private readonly List<Component> modelButtons = new();
        private int page, modelPage, logPage;
        private float nextRefresh;
        private bool built, additional;
        public bool Ready => built && root && frame;
        public NativeControlWindow(Plugin plugin) { p = plugin; }

        private static Type Type(string name) => AccessTools.TypeByName(name) ?? throw new InvalidOperationException("Missing game UI: " + name);
        private static object? Get(Component c, string name) => c.GetType().GetProperty(name)?.GetValue(c, null);
        private static void Set(Component c, string name, object? value) => c.GetType().GetProperty(name)?.SetValue(c, value, null);
        private static void EnumValue(Component c, string name, string value)
        {
            var prop = c.GetType().GetProperty(name);
            if (prop != null && prop.CanWrite) prop.SetValue(c, Enum.Parse(prop.PropertyType, value), null);
        }
        private static Component? Find(GameObject obj, string type)
        {
            var expected = AccessTools.TypeByName(type) ?? AccessTools.TypeByName("TMPro." + type);
            return obj.GetComponentsInChildren<Component>(true)
                .FirstOrDefault(c => c && (c.GetType().Name == type || expected?.IsAssignableFrom(c.GetType()) == true));
        }
        private static RectTransform Place(GameObject obj, Transform parent, float x, float y, float width, float height)
        {
            obj.transform.SetParent(parent, false);
            var rect = obj.GetComponent<RectTransform>();
            rect.anchorMin = rect.anchorMax = rect.pivot = new Vector2(0, 1);
            rect.anchoredPosition = new Vector2(x, -y); rect.sizeDelta = new Vector2(width, height);
            rect.localScale = Vector3.one; rect.localRotation = Quaternion.identity;
            return rect;
        }
        private static GameObject Box(string name, Transform parent, float x, float y, float w, float h)
        { var obj = new GameObject(name, typeof(RectTransform)); Place(obj, parent, x, y, w, h); return obj; }
        private static void Clean(GameObject obj)
        {
            foreach (var c in obj.GetComponentsInChildren<Behaviour>(true))
            {
                string ns = c.GetType().Namespace ?? "";
                if (ns != "UnityEngine.UI" && ns != "TMPro" && AccessTools.TypeByName("TMPro.TMP_InputField")?.IsAssignableFrom(c.GetType()) != true) c.enabled = false;
                if (c.GetType().Name is "TextMeshProUGUI" or "Text") Set(c, "raycastTarget", false);
            }
        }
        private static void Navigation(Component c)
        {
            var prop = c.GetType().GetProperty("navigation");
            if (prop == null) return;
            var nav = Activator.CreateInstance(prop.PropertyType);
            var mode = prop.PropertyType.GetProperty("mode");
            mode?.SetValue(nav, Enum.Parse(mode.PropertyType, "Automatic"), null);
            prop.SetValue(c, nav, null);
        }
        private void Image(GameObject obj, Color color, bool native = true)
        {
            var image = obj.AddComponent(Type("UnityEngine.UI.Image"));
            Set(image, "color", color); Set(image, "raycastTarget", true);
        }
        private Component Label(Transform parent, string text, float x, float y, float w, float h, int size = 18)
        {
            var obj = Box("Label", parent, x, y, w, h);
            var label = obj.AddComponent(Type("TMPro.TextMeshProUGUI"));
            if (textTemplate != null)
            { Set(label, "font", Get(textTemplate, "font")); Set(label, "fontSharedMaterial", Get(textTemplate, "fontSharedMaterial")); }
            Set(label, "fontSize", (float)size); Set(label, "enableAutoSizing", false);
            Set(label, "enableWordWrapping", true); Set(label, "richText", false); Set(label, "raycastTarget", false);
            EnumValue(label, "overflowMode", "Ellipsis");
            Set(label, "color", new Color(.88f, .85f, .76f)); EnumValue(label, "alignment", "TopLeft");
            Set(label, "text", text); return label;
        }
        private Component Button(Transform parent, string text, float x, float y, float w, Action click, bool busy = false)
        {
            var obj = Box("AI " + text, parent, x, y, w, 36);
            Image(obj, new Color(.28f, .22f, .14f, 1), false);
            var button = obj.AddComponent(Type("UnityEngine.UI.Button"));
            Set(button, "targetGraphic", obj.GetComponent(Type("UnityEngine.UI.Image")));
            var buttonCaption = Label(obj.transform, text, 8, 5, w - 16, 26, 18);
            EnumValue(buttonCaption, "alignment", "Center"); Set(buttonCaption, "color", new Color(1, .73f, .27f));
            // The default white/white ColorBlock does not visibly highlight a
            // brown image. Give every action and tab explicit pointer states.
            var colorsProperty = button.GetType().GetProperty("colors");
            if (colorsProperty != null)
            {
                object colors = colorsProperty.GetValue(button, null);
                foreach (var state in new[] { "highlightedColor", "selectedColor" })
                    colors.GetType().GetProperty(state)?.SetValue(colors, new Color(1.6f, 1.5f, 1.3f), null);
                colors.GetType().GetProperty("pressedColor")?.SetValue(colors, new Color(.65f, .65f, .65f), null);
                colorsProperty.SetValue(button, colors, null);
            }
            Navigation(button);
            var prop = button.GetType().GetProperty("onClick")!;
            var evt = (UnityEvent)Activator.CreateInstance(prop.PropertyType); evt.AddListener(() => click()); prop.SetValue(button, evt, null);
            foreach (var label in obj.GetComponentsInChildren<Component>(true).Where(c => c && c.GetType().Name is "TextMeshProUGUI" or "Text"))
            { Set(label, "text", text); Set(label, "fontSize", 18f); Set(label, "enableAutoSizing", true); Set(label, "fontSizeMin", 12f); Set(label, "fontSizeMax", 18f); }
            obj.SetActive(true); if (busy) busyButtons.Add(button); return button;
        }
        private static void ButtonText(Component? button, string text)
        {
            if (!button) return;
            foreach (var label in button!.GetComponentsInChildren<Component>(true).Where(c => c && c.GetType().Name is "TextMeshProUGUI" or "Text")) Set(label, "text", text);
        }
        private Component InputField(Transform parent, string value, float x, float y, float w, float h,
            int limit, Action<string> changed, bool password = false, bool multiline = false)
        {
            // Construct the input from loaded UI types: Valheim 1.x wraps its
            // inputs in GUIFramework and need not contain a TMP input template.
            var obj = Box("AI input", parent, x, y, w, h);
            Image(obj, new Color(.08f, .075f, .06f, .96f), false);
            var input = obj.AddComponent(Type("TMPro.TMP_InputField"));
            var area = Box("Viewport", obj.transform, 10, 6, w - 20, h - 12);
            area.AddComponent(Type("UnityEngine.UI.RectMask2D"));
            var content = Label(area.transform, "", 0, 0, w - 20, h - 12, 18);
            Set(input, "textViewport", area.GetComponent<RectTransform>());
            Set(input, "textComponent", content);
            Set(input, "targetGraphic", obj.GetComponent(Type("UnityEngine.UI.Image")));
            Navigation(input);
            foreach (var prop in input.GetType().GetProperties().Where(pr => pr.CanWrite && typeof(UnityEventBase).IsAssignableFrom(pr.PropertyType)))
                prop.SetValue(input, Activator.CreateInstance(prop.PropertyType), null);
            EnumValue(input, "contentType", password ? "Password" : "Standard");
            EnumValue(input, "lineType", multiline ? "MultiLineNewline" : "SingleLine");
            Set(input, "characterLimit", limit); Set(input, "text", value); Set(input, "richText", false);
            var text = Get(input, "textComponent") as Component;
            if (text != null) { Set(text, "fontSize", 18f); Set(text, "richText", false); Set(text, "color", new Color(.94f, .93f, .86f)); }
            var viewport = Get(input, "textViewport") as RectTransform;
            if (viewport)
            { viewport!.anchorMin = Vector2.zero; viewport.anchorMax = Vector2.one; viewport.offsetMin = new Vector2(10, 6); viewport.offsetMax = new Vector2(-10, -6); }
            var placeholder = Get(input, "placeholder") as Component; if (placeholder) placeholder!.gameObject.SetActive(false);
            var evt = Get(input, "onValueChanged") as UnityEvent<string>;
            evt?.AddListener(v => changed(v));
            obj.SetActive(true); return input;
        }
        private static void Sync(Component? input, string value)
        {
            if (input == null || Get(input, "isFocused") is true || (Get(input, "text") as string) == value) return;
            // Don't fire user-change handlers during model selection/key-buffer clearing.
            var method = input.GetType().GetMethod("SetTextWithoutNotify", new[] { typeof(string) });
            if (method != null) method.Invoke(input, new object[] { value }); else Set(input, "text", value);
        }

        public void Open()
        {
            try { if (!Ready) Build(); }
            catch { Dispose(); throw; }
            root!.SetActive(true); page = 0; ShowPage(); Refresh(true);
        }
        public void SetVisible(bool visible) { if (root) root!.SetActive(visible); }
        private void Build()
        {
            Dispose();
            var menu = Menu.instance;
            if (!menu) throw new InvalidOperationException("Game menu is not loaded");
            textTemplate = GameUi.Font(menu);
            if (textTemplate == null) throw new InvalidOperationException("Не найден загруженный шрифт TMP. " + GameUi.Describe(menu));
            root = new GameObject("Valheim AI Control", typeof(RectTransform)); root.SetActive(false);
            canvas = root.AddComponent(Type("UnityEngine.Canvas"));
            EnumValue(canvas, "renderMode", "ScreenSpaceOverlay"); Set(canvas, "sortingOrder", 30000);
            // Follow the live game canvas (resolution and GUI scale), rather
            // than running a competing scaler with a fixed 1280x800 baseline.
            Set(canvas, "scaleFactor", GameUi.InterfaceScale(menu));
            root.AddComponent(Type("UnityEngine.UI.GraphicRaycaster"));
            var rect = (RectTransform)root.transform;
            rect.anchorMin = Vector2.zero; rect.anchorMax = Vector2.one; rect.offsetMin = rect.offsetMax = Vector2.zero;
            root.transform.SetAsLastSibling(); Image(root, new Color(0, 0, 0, .72f), false);
            frame = Box("Settings panel", root.transform, 0, 0, UiLayout.Width, UiLayout.Height);
            var fr = (RectTransform)frame.transform; fr.anchorMin = fr.anchorMax = fr.pivot = new Vector2(.5f, .5f); fr.anchoredPosition = Vector2.zero;
            Image(frame, new Color(.14f, .12f, .09f, .98f), false);
            Label(frame.transform, "УПРАВЛЕНИЕ ПЕРСОНАЖЕМ", 24, 20, 812, 34, 24);
            stateLabel = Label(frame.transform, "", 24, 65, 812, 26, 16);
            var strip = Box("Navigation tabs", frame.transform, 24, 102, 812, 48);
            Image(strip, new Color(.065f, .055f, .04f), false);
            string[] names = { "Персонаж", "Подключение AI", "Журнал" };
            for (int i = 0; i < names.Length; i++)
            {
                int index = i;
                tabs.Add(Button(strip.transform, names[i], i * 274, 4, 264, () => { page = index; ShowPage(); }));
                var marker = Box("Selected tab", strip.transform, i * 274, 44, 264, 3);
                Image(marker, new Color(.85f, .59f, .18f), false); tabMarkers.Add(marker);
                pages.Add(Box("Tab " + i, frame.transform, 24, 166, 812, 382));
            }
            BuildCharacter(pages[0].transform); BuildConnection(pages[1].transform); BuildJournal(pages[2].transform);
            messageLabel = Label(frame.transform, "", 24, 554, 812, 44, 14);
            Button(frame.transform, "Назад", 24, 606, 160, () => { p.SetPanel(false); if (Menu.instance) Menu.instance.Show(); });
            Label(frame.transform, "F8 — окно управления     F9 — немедленный стоп", 208, 615, 624, 24, 15);
            built = true;
        }
        private void BuildCharacter(Transform tab)
        {
            characterMain = Box("Character", tab, 0, 0, 812, 382);
            characterAdvanced = Box("Additional settings", tab, 0, 0, 812, 382);
            var main = characterMain.transform; var advanced = characterAdvanced.transform;
            leaderLabel = Label(main, "", 0, 0, 535, 32);
            Button(main, "Сменить лидера", 552, 0, 260, p.SelectNextLeader);
            string name = Player.m_localPlayer ? Player.m_localPlayer.GetPlayerName() : "Бъерн";
            Label(main, "Поручения — в игровом чате.\nНапример: «" + name + ", подбери камни».\nПродолжения без имени AI разбирает по контексту. Дальние сообщения — /s.", 0, 72, 812, 100);
            Button(main, "Следовать", 0, 192, 260, () => p.StartCoroutine(p.StartMode("follow")), true);
            Button(main, "Включить AI", 276, 192, 260, () => { p.uiObjective = CompanionObjective; p.StartCoroutine(p.StartMode("ai")); }, true);
            Button(main, "Стоп", 552, 192, 260, p.StopFromPanel);
            Button(main, "Дополнительно  ›", 0, 258, 812, () => { additional = true; ShowPage(); });
            Label(main, "Следование работает без ключа. AI и проверка модели расходуют квоту API.", 0, 336, 812, 42, 16);
            Button(advanced, "‹  Персонаж", 0, 0, 200, () => { additional = false; ShowPage(); });
            Label(advanced, "Дополнительно", 228, 7, 570, 28, 21);
            combatButton = Button(advanced, "", 0, 64, 812, () => p.allowCombat.Value = !p.allowCombat.Value);
            foodButton = Button(advanced, "", 0, 116, 812, () => p.autoSurvival.Value = !p.autoSurvival.Value);
            controlButton = Button(advanced, "", 0, 168, 812, p.ToggleChatControlScope);
            Label(advanced, "Обращения проверяются по сетевому отправителю.\nПри изменении доступа текущая задача останавливается.\nСообщения разрешённых игроков передаются выбранному AI для разбора контекста.", 0, 236, 812, 110, 17);
        }
        private void BuildConnection(Transform tab)
        {
            providerButton = Button(tab, "", 0, 0, 270, () =>
            { p.uiProvider = p.uiProvider == "openai" ? "gemini" : "openai"; p.uiModels = Array.Empty<string>(); p.uiKey = p.uiModel = ""; modelPage = 0; }, true);
            Label(tab, "API-ключ (только в памяти до закрытия игры)", 0, 48, 812, 25, 17);
            keyInput = InputField(tab, p.uiKey, 0, 76, 812, 36, 512, s => { p.uiKey = s; p.uiError = ""; }, password: true);
            Label(tab, "API-имя модели", 0, 124, 510, 25, 17);
            modelInput = InputField(tab, p.uiModel, 0, 152, 510, 36, 100, s => p.uiModel = s);
            Button(tab, "Получить модели", 528, 152, 284, () => p.StartCoroutine(p.FetchModels()), true);
            for (int i = 0; i < 2; i++)
            {
                int slot = i;
                modelButtons.Add(Button(tab, "", 0, 196 + i * 39, 670, () =>
                { int index = modelPage * 2 + slot; if (index < p.uiModels.Length) p.uiModel = p.uiModels[index]; }));
            }
            Button(tab, "Ещё модели", 684, 215, 128, () => { if (p.uiModels.Length > 0) modelPage = (modelPage + 1) % ((p.uiModels.Length + 1) / 2); });
            Label(tab, "Пауза API, сек. (20 ≈ 3 запроса/мин)", 0, 299, 416, 24, 15);
            intervalInput = InputField(tab, p.uiInterval, 422, 291, 74, 36, 3, s => p.uiInterval = s);
            Button(tab, "Сохранить", 528, 291, 284, () => p.StartCoroutine(p.ApplySettings()), true);
            Button(tab, "Проверить модель", 528, 336, 284, () => p.StartCoroutine(p.CheckModel()), true);
            keyStatusLabel = Label(tab, "", 0, 348, 510, 25, 15);
        }
        private void BuildJournal(Transform tab)
        {
            Label(tab, "Планы AI, действия и ошибки. Ключи скрываются в отчёте.", 0, 0, 812, 28, 16);
            logText = Label(tab, "", 0, 38, 812, 246, 14);
            Button(tab, "Копировать отчёт", 0, 296, 260, p.CopyReport);
            Button(tab, "Сохранить в файл", 276, 296, 260, p.SaveReport);
            Button(tab, "Очистить", 552, 296, 260, () => { p.journal.Clear(); logPage = 0; });
            Button(tab, "Ранее", 0, 343, 155, () => logPage++);
            Button(tab, "Новые", 166, 343, 155, () => logPage = Math.Max(0, logPage - 1));
            Button(tab, "Перезапустить службу", 552, 343, 260, () => { p.StopFromPanel(); p.StartLocalRuntime(); });
        }
        private void ShowPage()
        {
            for (int i = 0; i < pages.Count; i++)
            {
                pages[i].SetActive(i == page); tabMarkers[i].SetActive(i == page);
                Set(tabs[i].GetComponent(Type("UnityEngine.UI.Image")), "color", i == page ? new Color(.36f, .26f, .12f) : new Color(.10f, .09f, .07f));
                foreach (var caption in tabs[i].GetComponentsInChildren<Component>(true).Where(GameUi.IsText))
                    Set(caption, "color", i == page ? new Color(1, .77f, .35f) : new Color(.77f, .74f, .65f));
            }
            characterMain!.SetActive(!additional); characterAdvanced!.SetActive(additional);
            Refresh(true);
        }
        public void Refresh(bool force = false)
        {
            if (!Ready || !p.showPanel || (!force && Time.realtimeSinceStartup < nextRefresh)) return;
            nextRefresh = Time.realtimeSinceStartup + .2f;
            float nativeScale = Menu.instance ? GameUi.InterfaceScale(Menu.instance) : 1;
            Set(canvas!, "scaleFactor", nativeScale);
            Vector2 settings = Menu.instance ? GameUi.SettingsSize(Menu.instance) : Vector2.zero;
            frame!.transform.localScale = Vector3.one * UiLayout.Fit(Screen.width / nativeScale, Screen.height / nativeScale, settings.x, settings.y);
            Set(stateLabel!, "text", !p.active ? "Ручное управление" : p.executor.Result + (p.bridgeStatus?.planning == true ? " · AI обдумывает план" : ""));
            Set(messageLabel!, "text", p.journal.Redact(p.uiError.Length > 0 ? p.uiError : p.uiMessage));
            Set(messageLabel!, "color", p.uiError.Length > 0 ? new Color(1, .55f, .4f) : new Color(.85f, .75f, .45f));
            var leader = p.FindLeader();
            Set(leaderLabel!, "text", "Лидер: " + (leader ? leader!.GetPlayerName() : p.leaderName.Value.Length > 0 ? p.leaderName.Value + " (не виден)" : "не выбран"));
            ButtonText(combatButton, "Бой: " + (p.allowCombat.Value ? "включён" : "выключен"));
            ButtonText(foodButton, "Еда: " + (p.autoSurvival.Value ? "автоматически" : "по команде"));
            ButtonText(controlButton, "Управление персонажем: " + (p.controlByAllPlayers.Value ? "Все игроки" : "Только лидер"));
            ButtonText(providerButton, p.uiProvider == "openai" ? "Провайдер: OpenAI" : "Провайдер: Gemini");
            Set(keyStatusLabel!, "text", p.HasKey(p.uiProvider) ? "API-ключ уже задан в памяти службы" : "Введите API-ключ и сохраните подключение");
            foreach (var button in busyButtons) Set(button, "interactable", !p.uiBusy);
            foreach (var input in new[] { keyInput, modelInput, intervalInput }) if (input != null) Set(input, "interactable", !p.uiBusy);
            Sync(modelInput, p.uiModel); Sync(keyInput, p.uiKey); Sync(intervalInput, p.uiInterval);
            for (int i = 0; i < modelButtons.Count; i++)
            {
                int index = modelPage * 2 + i; bool available = index < p.uiModels.Length;
                modelButtons[i].gameObject.SetActive(available);
                if (available) ButtonText(modelButtons[i], (p.uiModel == p.uiModels[index] ? "✓ " : "") + p.uiModels[index]);
            }
            string[] lines = p.journal.Text.Split('\n');
            logPage = Math.Min(logPage, Math.Max(0, (lines.Length - 1) / 7));
            int start = Math.Max(0, lines.Length - (logPage + 1) * 7);
            Set(logText!, "text", string.Join("\n", lines.Skip(start).Take(Math.Min(7, lines.Length - start))
                .Select(l => l.Length > 160 ? l.Substring(0, 157) + "..." : l)).Replace("\n\n", "\n"));
        }
        public void Dispose()
        {
            built = false;
            if (root) UnityEngine.Object.Destroy(root);
            root = frame = characterMain = characterAdvanced = null; canvas = null;
            pages.Clear(); busyButtons.Clear(); modelButtons.Clear(); tabs.Clear(); tabMarkers.Clear();
        }
    }
}
