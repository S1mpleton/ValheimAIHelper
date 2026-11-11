using System;
using System.IO;
using BepInEx;

namespace ValheimAIHelper;

public sealed partial class Plugin
{
    private readonly LogJournal journal = new();
    private BridgeRuntime? runtime;
    private NativeControlWindow? nativeWindow;
    private bool runtimeFailed;
    private string lastMotor = "", eventInstance = "";
    private int lastEventId;

    private void InitRuntime()
    {
        journal.RegisterSecret(bridgeToken.Value);
        AddLog("version", "Valheim AI Helper 0.3.0; Valheim " + global::Version.GetVersionString() + "; references 0.221.4");
        if (!autoBridge.Value) { AddLog("service", "Автозапуск выключен в конфигурации; используется внешний мост."); return; }
        StartLocalRuntime();
    }
    private void StartLocalRuntime()
    {
        try
        {
            runtime?.Dispose();
            runtime = new BridgeRuntime(AddLog);
            runtime.Start(Path.GetDirectoryName(Info.Location)!, Paths.ConfigPath);
            bridgeUrl.Value = runtime.Url; bridgeToken.Value = runtime.Token;
            journal.RegisterSecret(runtime.Token);
            uiClient?.Dispose(); CreateUiClient();
            bridgeStatus = null; uiHydrated = false; runtimeFailed = false;
            uiMessage = "Служба AI запускается…";
        }
        catch (Exception ex) { runtimeFailed = true; uiError = ex.Message; AddLog("service", ex.Message); }
    }
    private void UpdateRuntime()
    {
        if (runtime != null && !runtimeFailed && !runtime.Running)
        {
            runtimeFailed = true;
            DisableControl("Локальная служба AI завершилась. Откройте журнал и перезапустите её.");
            uiError = status; AddLog("service", status);
        }
    }
    private void AddLog(string source, string text) => journal.Add(source, text);
    private void LogMotor(Decision decision, Observation observation)
    {
        string message = decision.source + " · " + decision.action + " · " + decision.target_id + " · " + executor.Result;
        if (message == lastMotor) return;
        lastMotor = message;
        AddLog("motor", message + " | HP " + observation.Data.actor.health + "/" + observation.Data.actor.max_health +
            ", stamina " + observation.Data.actor.stamina + ", visible " + observation.Data.entities.Length);
    }
    private void ImportEvents(BridgeUiStatus value)
    {
        if (eventInstance != value.instance_id) { eventInstance = value.instance_id; lastEventId = 0; }
        var events = value.events;
        if (events == null) return;
        for (int i = events.Length - 1; i >= 0; --i)
            if (events[i].id > lastEventId) { AddLog(events[i].source, events[i].message); lastEventId = events[i].id; }
    }
    private string Report() => journal.Redact("Valheim AI Helper 0.3.0\nValheim: " + global::Version.GetVersionString() +
        "\nMode: " + (active ? bridgeStatus?.mode ?? "connecting" : "manual") + "\nLeader: " + leaderName.Value +
        "\nAI: " + uiProvider + " / " + uiModel + "\nCombat: " + allowCombat.Value + "; survival: " + autoSurvival.Value +
        "\nStatus: " + status + "\nError: " + uiError + "\n\n" + journal.Text);
    private void CopyReport() { UnityEngine.GUIUtility.systemCopyBuffer = Report(); uiMessage = "Отчёт скопирован. Его можно отправить сообщением."; }
    private void SaveReport()
    {
        try
        {
            string folder = Path.Combine(Paths.ConfigPath, "ValheimAIHelper", "reports"); Directory.CreateDirectory(folder);
            string file = Path.Combine(folder, "AIHelper-" + DateTime.Now.ToString("yyyyMMdd-HHmmss") + ".txt");
            File.WriteAllText(file, Report()); uiMessage = "Отчёт сохранён: " + file;
        }
        catch (Exception ex) { uiError = "Не удалось сохранить отчёт: " + ex.GetType().Name; }
    }
}
