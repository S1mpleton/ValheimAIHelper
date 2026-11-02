using System;

namespace ValheimAIHelper;

public static class BridgeFailure
{
    public static string Describe(long code, string body)
    {
        string detail = "";
        // Only read our structured error field. Never display raw HTML, headers or bodies.
        if (body.Length <= 8192)
            try { detail = ObservationValues.Text(WireJson.Deserialize<BridgeError>(body)?.detail, 220); }
            catch (Exception) { }
        string prefix;
        switch (code)
        {
            case 0: return "Нет соединения с мостом. Проверьте, что сервис запущен и порт совпадает.";
            case 401: prefix = "Не совпадает код сопряжения (HTTP 401)"; break;
            case 409: prefix = "Конфликт сессии или устаревший снимок (HTTP 409)"; break;
            case 422: prefix = "Данные персонажа отклонены (HTTP 422)"; break;
            default: prefix = "Ошибка моста (HTTP " + code + ")"; break;
        }
        return prefix + (detail.Length > 0 ? ": " + detail : ". Обновите мод и мост вместе.");
    }
}
