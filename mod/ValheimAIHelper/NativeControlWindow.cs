using System;
using System.Collections.Generic;
using System.Linq;
using HarmonyLib;
using UnityEngine;
using UnityEngine.Events;

namespace ValheimAIHelper;

public sealed partial class Plugin
{
    // Use the game's loaded UGUI/TMP types, fonts and nine-slice sprites. Reflection
    // keeps version-specific UI assemblies out of the distributable plugin.
    private sealed class NativeControlWindow : IDisposable
    {
        private readonly Plugin p;
        private GameObject? root, frame;
        private Component? buttonTemplate, textTemplate, imageTemplate, inputTemplate;
        private readonly List<GameObject> pages = new();
        private readonly List<Component> busyButtons = new();
        private Component? stateLabel, messageLabel, leaderLabel, combatButton, foodButton, chatButton, logText;
        private Component? objectiveInput, keyInput, modelInput, intervalInput, providerButton, keyStatusLabel;
        private readonly List<Component> modelButtons = new();
        private int page, modelPage, logPage;
        private float nextRefresh;
        private bool built;
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
            if (native && imageTemplate != null)
            {
                Set(image, "sprite", Get(imageTemplate, "sprite"));
                Set(image, "material", Get(imageTemplate, "material"));
                EnumValue(image, "type", "Sliced");
            }
            Set(image, "color", color); Set(image, "raycastTarget", true);
        }
        private Component Label(Transform parent, string text, float x, float y, float w, float h, int size = 21)
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
            var obj = UnityEngine.Object.Instantiate(buttonTemplate!.gameObject, parent, false);
            obj.name = "AI " + text; Clean(obj);
            Place(obj, parent, x, y, w, 42);
            var button = obj.GetComponent(buttonTemplate.GetType());
            Navigation(button);
            var prop = button.GetType().GetProperty("onClick")!;
            var evt = (UnityEvent)Activator.CreateInstance(prop.PropertyType); evt.AddListener(() => click()); prop.SetValue(button, evt, null);
            foreach (var label in obj.GetComponentsInChildren<Component>(true).Where(c => c && c.GetType().Name is "TextMeshProUGUI" or "Text"))
            { Set(label, "text", text); Set(label, "fontSize", 20f); Set(label, "enableAutoSizing", true); Set(label, "fontSizeMin", 13f); Set(label, "fontSizeMax", 20f); }
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
            var obj = UnityEngine.Object.Instantiate(inputTemplate!.gameObject, parent, false);
            Clean(obj); obj.name = "AI input"; Place(obj, parent, x, y, w, h);
            var input = obj.GetComponent(inputTemplate.GetType());
            Navigation(input);
            foreach (var prop in input.GetType().GetProperties().Where(pr => pr.CanWrite && typeof(UnityEventBase).IsAssignableFrom(pr.PropertyType)))
                prop.SetValue(input, Activator.CreateInstance(prop.PropertyType), null);
            EnumValue(input, "contentType", password ? "Password" : "Standard");
            EnumValue(input, "lineType", multiline ? "MultiLineNewline" : "SingleLine");
            Set(input, "characterLimit", limit); Set(input, "text", value); Set(input, "richText", false);
            var text = Get(input, "textComponent") as Component;
            if (text != null) { Set(text, "fontSize", 20f); Set(text, "richText", false); Set(text, "color", new Color(.94f, .93f, .86f)); }
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
            var nativeRoot = AccessTools.Field(typeof(Menu), "m_root")?.GetValue(menu) as Transform;
            var settings = AccessTools.Field(typeof(Menu), "m_settingsPrefab")?.GetValue(menu) as GameObject;
            var source = settings ? settings! : menu.gameObject;
            buttonTemplate = Find(source, "Button") ?? AccessTools.Field(typeof(Menu), "saveButton")?.GetValue(menu) as Component;
            textTemplate = Find(source, "TextMeshProUGUI");
            imageTemplate = source.GetComponentsInChildren<Component>(true).Where(c => c && c.GetType().Name == "Image" && Get(c, "sprite") != null)
                .OrderByDescending(c => c.transform is RectTransform r ? Math.Abs(r.rect.width * r.rect.height) : 0).FirstOrDefault();
            inputTemplate = Chat.instance ? Find(Chat.instance.gameObject, "TMP_InputField") : null;
            inputTemplate ??= Find(source, "TMP_InputField");
            if (!nativeRoot || buttonTemplate == null || textTemplate == null || inputTemplate == null)
                throw new InvalidOperationException("Native UI templates unavailable");
            root = new GameObject("Valheim AI Control", typeof(RectTransform)); root.SetActive(false);
            var rect = (RectTransform)root.transform; rect.SetParent(nativeRoot!.parent, false);
            rect.anchorMin = Vector2.zero; rect.anchorMax = Vector2.one; rect.offsetMin = rect.offsetMax = Vector2.zero;
            root.transform.SetAsLastSibling(); Image(root, new Color(0, 0, 0, .72f), false);
            frame = Box("Settings panel", root.transform, 0, 0, 920, 700);
            var fr = (RectTransform)frame.transform; fr.anchorMin = fr.anchorMax = fr.pivot = new Vector2(.5f, .5f); fr.anchoredPosition = Vector2.zero;
            Image(frame, Color.white);
            Label(frame.transform, "УПРАВЛЕНИЕ ПЕРСОНАЖЕМ", 32, 22, 850, 40, 29);
            stateLabel = Label(frame.transform, "", 32, 68, 850, 28, 18);
            Button(frame.transform, "Персонаж", 30, 110, 278, () => { page = 0; ShowPage(); });
            Button(frame.transform, "Подключение AI", 320, 110, 278, () => { page = 1; ShowPage(); });
            Button(frame.transform, "Журнал", 610, 110, 278, () => { page = 2; ShowPage(); });
            for (int i = 0; i < 3; i++) pages.Add(Box("Tab " + i, frame.transform, 30, 165, 860, 425));
            BuildCharacter(pages[0].transform); BuildConnection(pages[1].transform); BuildJournal(pages[2].transform);
            messageLabel = Label(frame.transform, "", 32, 596, 850, 50, 18);
            Button(frame.transform, "Назад", 30, 651, 170, () => { p.SetPanel(false); if (Menu.instance) Menu.instance.Show(); });
            Label(frame.transform, "F8 — окно управления     F9 — немедленный стоп", 226, 662, 650, 28, 17);
            built = true;
        }
        private void BuildCharacter(Transform tab)
        {
            leaderLabel = Label(tab, "", 0, 0, 650, 30);
            Button(tab, "Сменить лидера", 660, 0, 200, p.SelectNextLeader);
            Label(tab, "Задача помощнику", 0, 52, 860, 28);
            objectiveInput = InputField(tab, p.uiObjective, 0, 84, 860, 76, 1000, s => p.uiObjective = s, multiline: true);
            Button(tab, "Следовать", 0, 176, 277, () => p.StartCoroutine(p.StartMode("follow")), true);
            Button(tab, "Выполнять задачу AI", 290, 176, 277, () => p.StartCoroutine(p.StartMode("ai")), true);
            Button(tab, "Стоп", 580, 176, 280, p.StopFromPanel);
            combatButton = Button(tab, "", 0, 236, 277, () => p.allowCombat.Value = !p.allowCombat.Value);
            foodButton = Button(tab, "", 290, 236, 277, () => p.autoSurvival.Value = !p.autoSurvival.Value);
            chatButton = Button(tab, "", 580, 236, 280, () => p.allowChat.Value = !p.allowChat.Value);
            Label(tab, "В чате лидера: !ai следуй · !ai стоп · !ai статус\n!ai защищай меня и собирай выпавшие предметы\nКоманды принимает только выбранный лидер. Для дальнего сообщения: /s !ai ...", 0, 302, 860, 94, 20);
            Label(tab, "Следование работает без ключа. AI и проверка модели расходуют квоту API.", 0, 398, 860, 30, 17);
        }
        private void BuildConnection(Transform tab)
        {
            providerButton = Button(tab, "", 0, 0, 270, () =>
            { p.uiProvider = p.uiProvider == "openai" ? "gemini" : "openai"; p.uiModels = Array.Empty<string>(); p.uiKey = p.uiModel = ""; modelPage = 0; }, true);
            Label(tab, "API-ключ (только в памяти до закрытия игры)", 0, 56, 860, 27, 19);
            keyInput = InputField(tab, p.uiKey, 0, 86, 860, 38, 512, s => { p.uiKey = s; p.uiError = ""; }, password: true);
            Label(tab, "API-имя модели", 0, 139, 560, 27, 19);
            modelInput = InputField(tab, p.uiModel, 0, 169, 560, 38, 100, s => p.uiModel = s);
            Button(tab, "Получить модели", 580, 166, 280, () => p.StartCoroutine(p.FetchModels()), true);
            for (int i = 0; i < 3; i++)
            {
                int slot = i;
                modelButtons.Add(Button(tab, "", 0, 220 + i * 43, 710, () =>
                { int index = modelPage * 3 + slot; if (index < p.uiModels.Length) p.uiModel = p.uiModels[index]; }));
            }
            Button(tab, "Ещё модели", 724, 263, 136, () => { if (p.uiModels.Length > 0) modelPage = (modelPage + 1) % ((p.uiModels.Length + 2) / 3); });
            Label(tab, "Пауза API, сек. (20 ≈ 3 запроса/мин)", 0, 359, 480, 26, 18);
            intervalInput = InputField(tab, p.uiInterval, 480, 352, 80, 38, 3, s => p.uiInterval = s);
            Button(tab, "Сохранить", 580, 352, 280, () => p.StartCoroutine(p.ApplySettings()), true);
            Button(tab, "Проверить модель", 580, 398, 280, () => p.StartCoroutine(p.CheckModel()), true);
            keyStatusLabel = Label(tab, "", 0, 405, 560, 27, 18);
        }
        private void BuildJournal(Transform tab)
        {
            Label(tab, "Планы AI, действия персонажа и ошибки. Ключи скрываются в отчёте.", 0, 0, 860, 30, 18);
            logText = Label(tab, "", 0, 42, 860, 300, 17);
            Button(tab, "Копировать отчёт", 0, 353, 277, p.CopyReport);
            Button(tab, "Сохранить в файл", 290, 353, 277, p.SaveReport);
            Button(tab, "Очистить", 580, 353, 280, () => { p.journal.Clear(); logPage = 0; });
            Button(tab, "Ранее", 0, 402, 170, () => logPage++);
            Button(tab, "Новые", 180, 402, 170, () => logPage = Math.Max(0, logPage - 1));
            Button(tab, "Перезапустить службу", 580, 402, 280, () => { p.StopFromPanel(); p.StartLocalRuntime(); });
        }
        private void ShowPage() { for (int i = 0; i < pages.Count; i++) pages[i].SetActive(i == page); Refresh(true); }
        public void Refresh(bool force = false)
        {
            if (!Ready || !p.showPanel || (!force && Time.realtimeSinceStartup < nextRefresh)) return;
            nextRefresh = Time.realtimeSinceStartup + .2f;
            var parent = root!.transform.parent as RectTransform;
            float scale = parent != null ? Mathf.Min(1, (parent.rect.width - 30) / 920, (parent.rect.height - 30) / 700) : 1;
            frame!.transform.localScale = Vector3.one * Mathf.Max(.2f, scale);
            Set(stateLabel!, "text", !p.active ? "Ручное управление" : p.executor.Result + (p.bridgeStatus?.planning == true ? " · AI обдумывает план" : ""));
            Set(messageLabel!, "text", p.journal.Redact(p.uiError.Length > 0 ? p.uiError : p.uiMessage));
            Set(messageLabel!, "color", p.uiError.Length > 0 ? new Color(1, .55f, .4f) : new Color(.85f, .75f, .45f));
            var leader = p.FindLeader();
            Set(leaderLabel!, "text", "Лидер: " + (leader ? leader!.GetPlayerName() : p.leaderName.Value.Length > 0 ? p.leaderName.Value + " (не виден)" : "не выбран"));
            ButtonText(combatButton, "Бой: " + (p.allowCombat.Value ? "включён" : "выключен"));
            ButtonText(foodButton, "Еда: " + (p.autoSurvival.Value ? "автоматически" : "по команде"));
            ButtonText(chatButton, "Чат лидера: " + (p.allowChat.Value ? "включён" : "выключен"));
            ButtonText(providerButton, p.uiProvider == "openai" ? "Провайдер: OpenAI" : "Провайдер: Gemini");
            Set(keyStatusLabel!, "text", p.HasKey(p.uiProvider) ? "API-ключ уже задан в памяти службы" : "Введите API-ключ и сохраните подключение");
            foreach (var button in busyButtons) Set(button, "interactable", !p.uiBusy);
            foreach (var input in new[] { keyInput, modelInput, intervalInput }) if (input != null) Set(input, "interactable", !p.uiBusy);
            Sync(objectiveInput, p.uiObjective); Sync(modelInput, p.uiModel); Sync(keyInput, p.uiKey); Sync(intervalInput, p.uiInterval);
            for (int i = 0; i < modelButtons.Count; i++)
            {
                int index = modelPage * 3 + i; bool available = index < p.uiModels.Length;
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
            root = frame = null; pages.Clear(); busyButtons.Clear(); modelButtons.Clear();
        }
    }
}
