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
    private System.Reflection.PropertyInfo? currentSystem;
    private System.Reflection.PropertyInfo? selectedObject;
    private System.Reflection.PropertyInfo? interactable;
    private System.Reflection.PropertyInfo?[] colorProperties = Array.Empty<System.Reflection.PropertyInfo?>();
    private readonly Vector3[] corners = new Vector3[4];
    public void Initialize(Component button)
    {
        selectable = button; area = button.transform as RectTransform;
        var eventType = GameUi.FindType("UnityEngine.EventSystems.EventSystem");
        if (eventType != null) { currentSystem = UiReflection.Property(eventType, "current"); selectedObject = UiReflection.Property(eventType, "currentSelectedGameObject"); }
        interactable = UiReflection.Property(button.GetType(), "interactable");
        captions = button.GetComponentsInChildren<Component>(true).Where(GameUi.IsText).ToArray();
        colorProperties = captions.Select(c => UiReflection.Property(c.GetType(), "color")).ToArray();
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
        var system = currentSystem?.GetValue(null, null);
        var selected = system != null ? selectedObject?.GetValue(system, null) as GameObject : null;
        bool selectedHere = selected && selected!.transform.IsChildOf(transform);
        area!.GetWorldCorners(corners);
        if (eventCamera) for (int i = 0; i < corners.Length; i++) corners[i] = eventCamera!.WorldToScreenPoint(corners[i]);
        float minX = corners[0].x, minY = corners[0].y, maxX = minX, maxY = minY;
        for (int i = 1; i < 4; i++) { minX = Mathf.Min(minX, corners[i].x); minY = Mathf.Min(minY, corners[i].y); maxX = Mathf.Max(maxX, corners[i].x); maxY = Mathf.Max(maxY, corners[i].y); }
        var bounds = Rect.MinMaxRect(minX, minY, maxX, maxY);
        bool hover = interactable?.GetValue(selectable, null) is not false &&
            (selectedHere || bounds.Contains(Input.mousePosition));
        highlighted = hover;
        for (int i = 0; i < captions.Length; i++)
            if (captions[i] && colorProperties[i] != null)
            { var desired = hover ? new Color(1, .63f, .12f) : normal[i]; if (!Equals(colorProperties[i]!.GetValue(captions[i], null), desired)) colorProperties[i]!.SetValue(captions[i], desired, null); }
    }
    private void OnDisable()
    {
        if (!highlighted) return;
        for (int i = 0; i < captions.Length; i++)
            if (captions[i]) colorProperties[i]?.SetValue(captions[i], normal[i], null);
        highlighted = false;
    }
}
