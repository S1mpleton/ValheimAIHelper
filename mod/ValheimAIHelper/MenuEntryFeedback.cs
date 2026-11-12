using System;
using System.Linq;
using UnityEngine;

namespace ValheimAIHelper;

// Keep the game's visual components. This small fallback also supports native
// menu prefabs whose hover animator targets only the original localized label.
[DefaultExecutionOrder(10000)]
internal sealed class MenuEntryFeedback : MonoBehaviour
{
    private RectTransform? area;
    private Component[] captions = Array.Empty<Component>();
    private Color[] normal = Array.Empty<Color>();
    private Component? selectable;
    private Camera? eventCamera;
    private bool highlighted;
    private readonly Vector3[] corners = new Vector3[4];
    public void Initialize(Component button)
    {
        selectable = button; area = button.transform as RectTransform;
        captions = button.GetComponentsInChildren<Component>(true).Where(GameUi.IsText).ToArray();
        // Continue is often already selected when it is cloned. Its current
        // orange is not the new entry's idle color.
        normal = captions.Select(c => new Color(.84f, .84f, .80f,
            NativeAccess.Read(c, "color") is Color color ? color.a : 1)).ToArray();
        var canvasType = GameUi.FindType("UnityEngine.Canvas");
        var canvas = canvasType == null ? null : button.GetComponentInParent(canvasType);
        if (NativeAccess.Read(canvas, "renderMode")?.ToString() != "ScreenSpaceOverlay") eventCamera = NativeAccess.Read(canvas, "worldCamera") as Camera;
    }
    private void LateUpdate()
    {
        if (!area || !selectable) return;
        var system = GameUi.FindType("UnityEngine.EventSystems.EventSystem")?.GetProperty("current")?.GetValue(null, null);
        var selected = NativeAccess.Read(system, "currentSelectedGameObject") as GameObject;
        bool selectedHere = selected && selected!.transform.IsChildOf(transform);
        area!.GetWorldCorners(corners);
        if (eventCamera) for (int i = 0; i < corners.Length; i++) corners[i] = eventCamera!.WorldToScreenPoint(corners[i]);
        var bounds = Rect.MinMaxRect(corners.Min(c => c.x), corners.Min(c => c.y), corners.Max(c => c.x), corners.Max(c => c.y));
        bool hover = NativeAccess.Read(selectable, "interactable") is not false &&
            (selectedHere || bounds.Contains(Input.mousePosition));
        highlighted = hover;
        for (int i = 0; i < captions.Length; i++)
            if (captions[i]) captions[i].GetType().GetProperty("color")?.SetValue(captions[i], hover ? new Color(1, .63f, .12f) : normal[i], null);
    }
    private void OnDisable()
    {
        if (!highlighted) return;
        for (int i = 0; i < captions.Length; i++)
            if (captions[i]) captions[i].GetType().GetProperty("color")?.SetValue(captions[i], normal[i], null);
        highlighted = false;
    }
}
