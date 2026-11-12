using System;
using System.Collections;
using System.IO;
using System.Linq;
using System.Text;
using BepInEx;
using UnityEngine;

namespace ValheimAIHelper;

public sealed partial class Plugin
{
    private bool uiDialogue, dialoguePolling, dialogueBusy, dialogueLatest = true;
    private int dialogueId, dialoguePage, dialoguePages = 1, dialogueNavigation;
    private string dialoguePart = "request", dialogueText = "Диалогов пока нет. Включите AI или проверьте модель.", dialogueInstance = "";
    private DialogueEntry[] dialogueEntries = Array.Empty<DialogueEntry>();
    private float nextDialoguePoll;
    private DialogueEntry? CurrentDialogue => dialogueEntries.FirstOrDefault(e => e.id == dialogueId);
    private string DialogueTitle => CurrentDialogue is DialogueEntry e ? "#" + e.id + " · " + e.time + " · " + e.provider + "/" + e.model +
        "\n" + (e.purpose == "check" ? "Проверка модели · " : e.purpose == "chat" ? "Разбор чата · " : "Игровой план · ") +
        (e.status == "running" ? "Ожидание " : e.status == "ok" ? "План проверен · " : e.status == "cancelled" ? "Отменён · " : "Ошибка · ") +
        e.seconds.ToString("0.0") + " с" + (e.truncated ? " · текст сокращён" : "") : "Последние 8 запросов. Хранятся в памяти до закрытия службы.";

    private void UpdateDialogue()
    {
        if (showPanel && uiDialogue && !dialoguePolling && !dialogueBusy && Time.realtimeSinceStartup >= nextDialoguePoll)
            StartCoroutine(RefreshDialogue());
    }
    private void NavigateDialogue(int direction)
    {
        if (dialogueBusy || dialogueEntries.Length == 0) return;
        int current = Array.FindIndex(dialogueEntries, e => e.id == dialogueId);
        int index = Mathf.Clamp(current + direction, 0, dialogueEntries.Length - 1);
        dialogueLatest = index == 0;
        dialogueId = dialogueEntries[index].id; dialoguePage = 0; dialoguePages = 1;
        dialogueText = "Загружаю диалог…"; dialogueNavigation++; nextDialoguePoll = 0;
    }
    private void SelectDialoguePart(string part)
    {
        dialogueLatest = false;
        dialoguePart = part; dialoguePage = 0; dialoguePages = 1; dialogueText = "Загружаю…";
        dialogueNavigation++; nextDialoguePoll = 0;
    }
    private void ChangeDialoguePage(int direction)
    {
        dialogueLatest = false;
        dialoguePage = Mathf.Clamp(dialoguePage + direction, 0, dialoguePages - 1);
        dialogueNavigation++; nextDialoguePoll = 0;
    }
    private IEnumerator RefreshDialogue()
    {
        if (uiClient == null || dialoguePolling) yield break;
        dialoguePolling = true;
        int operation = uiOperation, navigation = dialogueNavigation;
        try
        {
            yield return uiClient.Request<DialogueIndex>("/api/dialogue", null, value =>
            {
                if (operation != uiOperation || navigation != dialogueNavigation) return;
                if (dialogueInstance != value.instance_id)
                { dialogueInstance = value.instance_id; dialogueId = 0; dialogueLatest = true; dialoguePage = 0; }
                dialogueEntries = value.entries ?? Array.Empty<DialogueEntry>();
                if (dialogueLatest || !dialogueEntries.Any(e => e.id == dialogueId))
                {
                    int newest = dialogueEntries.FirstOrDefault()?.id ?? 0;
                    if (dialogueId != newest) { dialogueId = newest; dialoguePage = 0; }
                }
                if (dialogueId == 0) { dialogueText = "Диалогов пока нет. Включите AI или проверьте модель."; dialoguePages = 1; }
            }, error => { if (operation == uiOperation && navigation == dialogueNavigation) dialogueText = error; }, 8);
            if (operation != uiOperation || navigation != dialogueNavigation || dialogueId == 0) yield break;
            int id = dialogueId; string part = dialoguePart; int page = dialoguePage;
            yield return uiClient.Request<DialoguePage>("/api/dialogue/" + id + "?part=" + part + "&page=" + page, null, value =>
            {
                if (operation != uiOperation || navigation != dialogueNavigation || id != dialogueId || part != dialoguePart) return;
                dialoguePage = value.page; dialoguePages = Math.Max(1, value.pages); dialogueText = value.text;
            }, error => { if (operation == uiOperation && navigation == dialogueNavigation) dialogueText = error; }, 8);
        }
        finally { dialoguePolling = false; nextDialoguePoll = navigation == dialogueNavigation ? Time.realtimeSinceStartup + 2 : 0; }
    }
    private IEnumerator ExportDialogue(bool save)
    {
        if (uiClient == null || dialogueBusy || CurrentDialogue == null) yield break;
        if (CurrentDialogue.status == "running") { uiMessage = "Дождитесь ответа или отмените запрос через F9 перед экспортом."; yield break; }
        dialogueBusy = true; int operation = uiOperation, id = dialogueId, offset = 0;
        var text = new StringBuilder();
        try
        {
            for (int chunk = 0; chunk < 64; chunk++)
            {
                DialogueExport? reply = null;
                yield return uiClient.Request<DialogueExport>("/api/dialogue/" + id + "/export?offset=" + offset, null,
                    value => reply = value, error => { if (operation == uiOperation) uiError = error; }, 8);
                if (operation != uiOperation || reply == null) yield break;
                if (reply.id != id || reply.offset != offset || reply.next_offset <= offset || text.Length > 1800000)
                { uiError = "Служба вернула несогласованные части диалога."; yield break; }
                text.Append(reply.text); offset = reply.next_offset;
                if (!reply.complete) continue;
                string report = journal.Redact(text.ToString());
                if (save)
                {
                    string folder = Path.Combine(Paths.ConfigPath, "ValheimAIHelper", "reports");
                    string file = Path.Combine(folder, "Dialogue-" + id + "-" + DateTime.Now.ToString("yyyyMMdd-HHmmss") + ".txt");
                    try { Directory.CreateDirectory(folder); File.WriteAllText(file, report); uiMessage = "Диалог сохранён: " + file; }
                    catch (Exception ex) { uiError = "Не удалось сохранить диалог: " + ex.GetType().Name; }
                }
                else { GUIUtility.systemCopyBuffer = report; uiMessage = "Полный диалог #" + id + " скопирован."; }
                yield break;
            }
            uiError = "Диалог превысил лимит экспорта.";
        }
        finally { dialogueBusy = false; }
    }
    private IEnumerator ClearDialogue()
    {
        if (uiClient == null || dialogueBusy) yield break;
        dialogueBusy = true; int operation = uiOperation;
        try
        {
            yield return uiClient.Request<BridgeOk>("/api/dialogue/clear", new BridgeEmpty(), value =>
            {
                if (operation != uiOperation) return;
                dialogueEntries = Array.Empty<DialogueEntry>(); dialogueId = dialoguePage = 0; dialogueLatest = true;
                dialogueText = "История диалога очищена."; dialoguePages = 1; nextDialoguePoll = 0;
            }, error => { if (operation == uiOperation) uiError = error; }, 8);
        }
        finally { dialogueBusy = false; }
    }
    private void DrawDialogue()
    {
        if (GUILayout.Button("‹ Журнал", panelButtonStyle)) uiDialogue = false;
        GUILayout.Label(DialogueTitle, hintStyle);
        GUILayout.BeginHorizontal();
        if (GUILayout.Button("Ранее", panelButtonStyle)) NavigateDialogue(1);
        if (GUILayout.Button("Новее", panelButtonStyle)) NavigateDialogue(-1);
        GUILayout.EndHorizontal();
        GUILayout.BeginHorizontal();
        foreach (var part in new[] { "request", "response", "result" })
            if (GUILayout.Button((dialoguePart == part ? "● " : "") + (part == "request" ? "Запрос" : part == "response" ? "Ответ AI" : "Проверка"), panelButtonStyle)) SelectDialoguePart(part);
        GUILayout.EndHorizontal();
        GUILayout.TextArea(dialogueText, GUILayout.MinHeight(210));
        GUILayout.BeginHorizontal();
        if (GUILayout.Button("‹", panelButtonStyle)) ChangeDialoguePage(-1);
        GUILayout.Label((dialoguePage + 1) + "/" + dialoguePages, hintStyle);
        if (GUILayout.Button("›", panelButtonStyle)) ChangeDialoguePage(1);
        GUILayout.EndHorizontal();
        GUI.enabled = !dialogueBusy && CurrentDialogue != null && CurrentDialogue.status != "running";
        GUILayout.BeginHorizontal();
        if (GUILayout.Button("Копировать всё", panelButtonStyle)) StartCoroutine(ExportDialogue(false));
        if (GUILayout.Button("Сохранить всё", panelButtonStyle)) StartCoroutine(ExportDialogue(true));
        GUILayout.EndHorizontal(); GUI.enabled = !dialogueBusy;
        if (GUILayout.Button("Очистить диалоги", panelButtonStyle)) StartCoroutine(ClearDialogue());
        GUI.enabled = true;
    }
}
