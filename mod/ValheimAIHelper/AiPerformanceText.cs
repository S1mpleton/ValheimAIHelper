using System.Globalization;

namespace ValheimAIHelper;

internal static class AiPerformanceText
{
    private static string N(double? value) => value?.ToString("0.0", CultureInfo.InvariantCulture) ?? "—";
    public static string Summary(AiPerformance? value)
    {
        if (value?.current != null) return "AI: ожидание ответа " + N(value.current.elapsed_seconds) + " с · " + value.current.model;
        var last = value?.last;
        if (last == null) return "Скорость AI появится после первого запроса.";
        string result = last.status == "ok" ? "план проверен" : last.status == "cancelled" ? "отменён" : "ошибка ответа";
        return "AI: " + N(last.request_seconds) + " с" + (last.tokens_per_second.HasValue ? " · " + N(last.tokens_per_second) + " ток/с" : "") + " · " + result;
    }
    public static string Details(AiPerformance? value)
    {
        var last = value?.last;
        if (last == null) return "Время ответа и скорость не оценивают правильность решений модели.";
        if (last.provider != "ollama") return "Последний ответ: " + last.model;
        return "Загрузка " + N(last.load_seconds) + " с · вход " + N(last.prompt_seconds) + " с · генерация " + N(last.generation_seconds) + " с" +
            " · токены " + (last.prompt_tokens?.ToString() ?? "—") + "/" + (last.output_tokens?.ToString() ?? "—");
    }
}
