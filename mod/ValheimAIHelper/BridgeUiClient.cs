using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using UnityEngine.Networking;

namespace ValheimAIHelper;

internal sealed class BridgeUiClient : IDisposable
{
    private readonly string url, token;
    private readonly HashSet<UnityWebRequest> pending = new();

    public BridgeUiClient(string url, string token)
    {
        if (!ValidAddress(url)) throw new ArgumentException("Адрес службы AI должен быть локальным HTTP-адресом без пути.");
        this.url = url.TrimEnd('/');
        this.token = token;
    }

    public static bool ValidAddress(string value) => BridgeAddress.IsValid(value);

    public IEnumerator Request<T>(string path, object? body, Action<T> success, Action<string> failure, int timeout = 110)
    {
        using (var request = new UnityWebRequest(url + path, body == null ? "GET" : "POST"))
        {
            request.downloadHandler = new DownloadHandlerBuffer();
            request.redirectLimit = 0;
            request.SetRequestHeader("Authorization", "Bearer " + token);
            if (body != null)
            {
                // No logging of bodies: provider settings can contain an API key.
                string json = body switch
                {
                    ProviderOptions p => WireJson.Serialize(p),
                    BridgeCommand c => WireJson.Serialize(c),
                    ModelListRequest m => WireJson.Serialize(m),
                    ModelCheckRequest c => WireJson.Serialize(c),
                    _ => throw new ArgumentException("Unsupported bridge request")
                };
                request.uploadHandler = new UploadHandlerRaw(Encoding.UTF8.GetBytes(json));
                request.SetRequestHeader("Content-Type", "application/json");
            }
            request.timeout = timeout;
            pending.Add(request);
            try
            {
                yield return request.SendWebRequest();
                if (request.downloadHandler.data != null && request.downloadHandler.data.Length > 131072)
                { failure("Слишком большой ответ службы AI."); yield break; }
                if (request.result != UnityWebRequest.Result.Success)
                {
                    if (request.responseCode == 0)
                        failure("Служба AI запускается или недоступна. Проверьте вкладку «Журнал» и наличие папки runtime.");
                    else if (request.responseCode == 401)
                        failure("Код локальной службы не совпадает. Перезапустите службу во вкладке «Журнал».");
                    else
                    {
                        string detail = "";
                        try { detail = WireJson.Deserialize<BridgeError>(request.downloadHandler.text)?.detail ?? ""; }
                        catch (Exception) { }
                        // The local service supplies sanitised API diagnostics. Never show raw response bodies.
                        failure(detail.Length > 0 ? detail.Substring(0, Math.Min(detail.Length, 1000)) : "Ошибка службы AI: HTTP " + request.responseCode);
                    }
                    yield break;
                }
                T parsed;
                try { parsed = WireJson.Deserialize<T>(request.downloadHandler.text); }
                catch (Exception) { failure("Ответ службы AI не распознан. Обновите мод и службу вместе."); yield break; }
                success(parsed);
            }
            finally { pending.Remove(request); }
        }
    }

    public void Dispose()
    {
        foreach (var request in pending.ToArray()) request.Abort();
        pending.Clear();
    }
}
