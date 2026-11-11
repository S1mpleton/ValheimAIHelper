using System;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Security.Cryptography;

namespace ValheimAIHelper;

// The process handle is owned by this mod instance. Never terminate a process
// found by name, a user-started bridge, or a service belonging to another game.
internal sealed class BridgeRuntime : IDisposable
{
    private Process? process;
    private readonly Action<string, string> log;
    public string Url { get; private set; } = "";
    public string Token { get; private set; } = "";
    public bool Running => process != null && !process.HasExited;

    public BridgeRuntime(Action<string, string> log) { this.log = log; }
    public void Start(string pluginDirectory, string configDirectory)
    {
        if (Environment.OSVersion.Platform != PlatformID.Win32NT)
            throw new PlatformNotSupportedException("Автозапуск готового сервиса поддерживает Windows. Для разработки запустите Python-мост.");
        string executable = Path.Combine(pluginDirectory, "runtime", "ValheimAIBridge.exe");
        if (!File.Exists(executable))
            throw new FileNotFoundException("Не найден runtime/ValheimAIBridge.exe. Установите полный Windows-архив из Releases, включая папку runtime.");
        string data = Path.Combine(configDirectory, "ValheimAIHelper", "runtime-" + Process.GetCurrentProcess().Id);
        Directory.CreateDirectory(data);
        byte[] bytes = new byte[32]; using (var random = RandomNumberGenerator.Create()) random.GetBytes(bytes);
        Token = Convert.ToBase64String(bytes);
        File.WriteAllText(Path.Combine(data, "bridge-token"), Token + "\n");
        var listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start(); int port = ((IPEndPoint)listener.LocalEndpoint).Port; listener.Stop();
        Url = "http://127.0.0.1:" + port;
        var info = new ProcessStartInfo(executable, "--port " + port + " --data-dir " + Quote(data) + " --parent-pid " + Process.GetCurrentProcess().Id)
        {
            WorkingDirectory = pluginDirectory, UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardOutput = true, RedirectStandardError = true,
            StandardOutputEncoding = System.Text.Encoding.UTF8, StandardErrorEncoding = System.Text.Encoding.UTF8,
        };
        info.EnvironmentVariables["PYTHONUTF8"] = "1";
        process = new Process { StartInfo = info };
        process.OutputDataReceived += (_, e) => { if (!string.IsNullOrEmpty(e.Data)) log("service", e.Data); };
        process.ErrorDataReceived += (_, e) => { if (!string.IsNullOrEmpty(e.Data)) log("service", e.Data); };
        if (!process.Start()) throw new InvalidOperationException("Windows не запустила службу AI");
        process.BeginOutputReadLine(); process.BeginErrorReadLine();
        log("service", "Запущена локальная служба AI. PID " + process.Id + ", порт " + port);
    }
    private static string Quote(string value) => "\"" + value.Replace("\"", "") .TrimEnd('\\') + "\"";
    public void Dispose()
    {
        if (process == null) return;
        try { if (!process.HasExited) process.Kill(); }
        catch (InvalidOperationException) { }
        catch (System.ComponentModel.Win32Exception) { }
        process.Dispose(); process = null;
    }
}
