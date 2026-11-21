using BepInEx.Configuration;
using UnityEngine;

namespace ValheimAIHelper;

public sealed partial class Plugin
{
    private ConfigEntry<bool> taskHudVisible = null!;
    private GUIStyle? taskHudStyle, taskHudBoxStyle;
    private BridgeTaskPool? taskHudSource;
    private string taskHudText = "";
    private bool taskHudReflex;
    private readonly GUIContent taskHudContent = new();
    private float taskHudMeasureWidth = -1, taskHudMeasuredHeight, taskHudScale = 1, nextTaskHudScale;
    private bool TaskHudEnabled => taskHudVisible?.Value ?? true;

    private void InitTaskHud() => taskHudVisible = Config.Bind("UI", "ShowTaskHud", true, "Show a read-only task list on the right side of the game screen.");
    private void ToggleTaskHud()
    {
        if (taskHudVisible == null) InitTaskHud();
        taskHudVisible!.Value = !taskHudVisible.Value;
    }
    private void DrawTaskHud()
    {
        if (!TaskHudEnabled || !Player.m_localPlayer || Player.m_localPlayer.IsDead() ||
            showPanel || Menu.IsVisible() || Minimap.IsOpen()) return;
        var pool = CurrentTaskPool;
        if (pool == null) return;
        if (!ReferenceEquals(taskHudSource, pool) || taskHudReflex != executor.ReflexActive)
        {
            taskHudSource = pool; taskHudReflex = executor.ReflexActive;
            taskHudText = (taskHudReflex ? "Защита / выживание · приоритет 100\n" : "") + TaskPoolText.Hud(pool);
            taskHudContent.text = taskHudText; taskHudMeasureWidth = -1;
        }
        taskHudStyle ??= new GUIStyle(GUI.skin.label)
        {
            fontSize = 14, richText = false, wordWrap = true,
            alignment = TextAnchor.UpperLeft,
            normal = { textColor = new Color(.96f, .88f, .69f) }
        };
        taskHudBoxStyle ??= new GUIStyle(GUI.skin.box) { padding = new RectOffset(0, 0, 0, 0) };
        if (Time.realtimeSinceStartup >= nextTaskHudScale)
        {
            taskHudScale = Mathf.Clamp(Menu.instance ? GameUi.InterfaceScale(Menu.instance) : 1, .25f, 4);
            nextTaskHudScale = Time.realtimeSinceStartup + .5f;
        }
        float scale = taskHudScale;
        float vw = Screen.width / scale, vh = Screen.height / scale;
        if (vw < 180 || vh < 100) return;
        float width = Mathf.Min(340, vw - 32);
        if (!Mathf.Approximately(taskHudMeasureWidth, width))
        { taskHudMeasureWidth = width; taskHudMeasuredHeight = taskHudStyle.CalcHeight(taskHudContent, width - 24) + 24; }
        float height = Mathf.Min(Mathf.Max(96, taskHudMeasuredHeight), Mathf.Max(64, vh - 32));
        float x = Mathf.Max(8, vw - width - 16), y = Mathf.Clamp(240, 8, Mathf.Max(8, vh - height - 16));
        var oldMatrix = GUI.matrix; var oldColor = GUI.backgroundColor;
        try
        {
            GUI.matrix = Matrix4x4.TRS(Vector3.zero, Quaternion.identity, Vector3.one * scale);
            GUI.backgroundColor = new Color(.10f, .085f, .06f, .92f);
            // Box and Label are passive: this HUD never captures movement,
            // clicks, chat focus or NPC controls.
            GUI.Box(new Rect(x, y, width, height), GUIContent.none, taskHudBoxStyle);
            GUI.Label(new Rect(x + 12, y + 12, width - 24, height - 24), taskHudContent, taskHudStyle);
        }
        finally { GUI.matrix = oldMatrix; GUI.backgroundColor = oldColor; }
    }
}
