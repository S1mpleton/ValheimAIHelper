"use strict";
// Credentials remain in this tab's memory, never localStorage, URLs or HTML.
let bridgeToken = "";
let lastStatus = null;
let polling = false;
let modelListRevision = 0;
const $ = id => document.getElementById(id);
function notice(text, error = false) {
  $("notice").textContent = text;
  $("notice").classList.toggle("error", error);
}
async function api(path, body) {
  const response = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: {"Authorization": `Bearer ${bridgeToken}`, ...(body === undefined ? {} : {"Content-Type": "application/json"})},
    ...(body === undefined ? {} : {body: JSON.stringify(body)}),
    signal: AbortSignal.timeout(path === "/api/check/ollama" ? 310000 : 110000)
  });
  const data = await response.json();
  if (!response.ok) {
    if (response.status === 401) {
      $("dashboard").hidden = true;
      $("pairing").hidden = false;
      bridgeToken = "";
    }
    throw new Error(typeof data.detail === "string" ? data.detail : `Ошибка запроса (${response.status})`);
  }
  return data;
}
function keyStatus() {
  const selected = $("provider").value;
  $("key-status").textContent = lastStatus?.keys_present[selected] ? "· уже задан" : "";
}
function clearGeminiModels() {
  modelListRevision += 1;
  const option = document.createElement("option");
  option.value = ""; option.textContent = "Сначала получите список моделей";
  $("gemini-model-choice").replaceChildren(option);
  $("gemini-model-choice").disabled = true;
  $("models-hint").textContent = "После выбора модели нажмите «Применить настройки».";
}
function providerFields() {
  const p = $("provider").value;
  $("gemini-model-controls").hidden = p === "offline";
  $("ollama-fields").hidden = p !== "ollama";
  $("cloud-key-fields").hidden = p === "ollama" || p === "offline";
  $("models-label").textContent = p === "ollama" ? "Установленные локальные модели" : "Модели провайдера";
  $("check-hint").textContent = p === "ollama" ? "Проверка локального JSON-плана; первая загрузка может занять до 5 минут. Стоп/F9 отменяет ожидание. Данные игры не отправляются." : "Проверка выполняет короткие запросы и может расходовать квоту API. Данные игры не отправляются.";
  $("provider-hint").textContent = p === "ollama" ? "AI выполняется локально без облачных токенов. Скорость зависит от модели и ресурсов ПК." : "Ключ оплачивается по тарифам API. Подписка ChatGPT или Gemini не заменяет API-ключ.";
  $("interval-hint").textContent = p === "ollama" ? "Интервал ограничивает нагрузку на ПК. Движение персонажа продолжается между решениями модели." : "20 секунд — около 3 решений в минуту. Повторы и ручные проверки тоже расходуют квоту.";
  $("model").required = p !== "offline";
  $("model").disabled = p === "offline";
  $("api-key").disabled = p === "offline";
}
async function refresh() {
  if (!bridgeToken || polling) return;
  polling = true;
  try {
    const s = await api("/api/status");
    lastStatus = s;
    $("connection").textContent = s.snapshot_error ? "Данные игры отклонены" : s.connected ? "Персонаж подключён" : "Ждём игру";
    $("connection").classList.toggle("online", s.connected && !s.snapshot_error);
    $("bridge-error").hidden = !s.snapshot_error;
    $("bridge-error").textContent = s.snapshot_error?.message || "";
    const ready = s.connected && !s.snapshot_error;
    $("ai-start").disabled = !ready;
    $("follow").disabled = !ready;
    $("mode").textContent = `Режим: ${{stop: "стоп", follow: "следование без AI", ai: "AI"}[s.mode]}${s.provider !== "offline" ? ` · ${{gemini:"Gemini",openai:"OpenAI",ollama:"Ollama"}[s.provider]} · ${s.model}` : ""}`;
    $("actor").textContent = s.actor
      ? `${s.actor.name} · здоровье ${Math.round(s.actor.health)}/${Math.round(s.actor.max_health)} · выносливость ${Math.round(s.actor.stamina)} · предметов в инвентаре: ${s.actor.inventory.length}`
      : "Ждём наблюдение из игры…";
    $("events").replaceChildren(...s.events.map(event => {
      const li = document.createElement("li");
      const time = document.createElement("time"); time.textContent = event.time;
      const source = document.createElement("span"); source.className = "source"; source.textContent = event.source;
      li.append(time, source, document.createTextNode(event.message));
      return li;
    }));
    keyStatus();
  } catch (e) { notice(e.message || "Мост недоступен", true); }
  finally { polling = false; }
}
$("pair-form").addEventListener("submit", async e => {
  e.preventDefault();
  bridgeToken = $("bridge-token").value.trim();
  $("bridge-token").value = "";
  try {
    const s = await api("/api/status");
    lastStatus = s;
    $("provider").value = s.provider === "offline" ? "openai" : s.provider;
    $("model").value = s.model || (s.provider === "openai" ? "gpt-4.1-mini" : "");
    $("ollama-url").value = s.ollama_url || "http://127.0.0.1:11434";
    $("api-interval").value = s.request_interval_seconds;
    providerFields();
    clearGeminiModels();
    $("objective").value = s.objective;
    $("pairing").hidden = true; $("dashboard").hidden = false;
    notice("Мост подключён. Включите мод на отдельном персонаже через F8, затем выберите режим здесь.");
    await refresh();
  } catch (error) { notice(error.message, true); }
});
$("provider").addEventListener("change", async () => {
  const p = $("provider").value;
  clearGeminiModels();
  $("model").value = p === "openai" ? "gpt-4.1-mini" : "";
  providerFields();
  $("api-key").value = "";
  keyStatus();
  if (bridgeToken) {
    try { await api("/api/command", {mode:"stop"}); }
    catch (error) { notice(error.message, true); }
  }
});
$("provider-form").addEventListener("submit", async e => {
  e.preventDefault();
  const key = $("api-key").value.trim();
  $("api-key").value = "";
  try {
    await api("/api/provider", {provider: $("provider").value, model: $("model").value.trim(), api_key: $("provider").value === "ollama" ? "" : key,
      ollama_url: $("ollama-url").value.trim(),
      request_interval_seconds: Number($("api-interval").value)});
    notice("Настройки применены. Проверьте выбранную модель и включите AI. Управление остановлено.");
    await refresh();
  } catch (error) { notice(error.message, true); }
});
async function command(mode) {
  if (mode === "ai" && (!lastStatus || lastStatus.provider !== $("provider").value
      || lastStatus.model !== $("model").value.trim()
      || lastStatus.request_interval_seconds !== Number($("api-interval").value) || $("api-key").value.trim())) {
    notice("Сначала нажмите «Применить настройки» для выбранного провайдера и модели.", true);
    return;
  }
  try {
    await api("/api/command", {mode, objective: $("objective").value});
    notice(mode === "ai" ? $("provider").value === "ollama" ? "AI включён: локальная Ollama, без облачных токенов." : "AI включён. Запросы могут расходовать баланс API." : mode === "follow" ? "Следование включено без запросов к API." : "Стоп отправлен. F9 в игре немедленно отключает мод.");
    await refresh();
  } catch (error) { notice(error.message, true); }
}
$("objective-form").addEventListener("submit", e => {e.preventDefault(); command("ai");});
$("follow").addEventListener("click", () => command("follow"));
$("stop").addEventListener("click", () => command("stop"));
setInterval(refresh, 2000);

$("api-key").addEventListener("input", clearGeminiModels);
$("ollama-url").addEventListener("input", clearGeminiModels);
$("model").addEventListener("input", () => {
  $("gemini-model-choice").value = $("model").value.trim();
});
$("gemini-model-choice").addEventListener("change", () => {
  if (!$("gemini-model-choice").value) return;
  $("model").value = $("gemini-model-choice").value;
  $("models-hint").textContent = "Модель выбрана. Нажмите «Применить настройки», затем включите AI.";
  notice("Модель выбрана. Нажмите «Применить настройки», затем включите AI.");
});

$("check-model").addEventListener("click", async () => {
  const model = $("model").value.trim();
  if (!model) { notice("Сначала выберите модель или введите её API-имя.", true); return; }
  const key = $("api-key").value.trim();
  const provider = $("provider").value;
  const revision = modelListRevision;
  const button = $("check-model");
  button.disabled = true;
  notice(`Проверяю ${provider} / ${model}…${provider === "ollama" ? " Первая загрузка может занять до 5 минут." : ""}`);
  try {
    const result = await api(`/api/check/${provider}`, {model, api_key: provider === "ollama" ? "" : key, ollama_url:$("ollama-url").value.trim()});
    if (revision === modelListRevision && $("model").value.trim() === model && $("provider").value === provider) {
      notice(`${provider} / ${model}: проверка прошла. ${result.checks.join("; ")}. Примените настройки, затем включите AI.`);
    }
  } catch (error) {
    if (revision === modelListRevision && $("model").value.trim() === model && $("provider").value === provider) {
      notice(`Проверка ${model}: ${error.message}`, true);
    }
  }
  finally { button.disabled = false; }
});

$("load-models").addEventListener("click", async () => {
  const button = $("load-models");
  button.disabled = true;
  const revision = ++modelListRevision;
  const key = $("api-key").value.trim();
  const provider = $("provider").value;
  $("models-hint").textContent = "Получаю список моделей…";
  try {
    const data = await api(`/api/models/${provider}`, {api_key: provider === "ollama" ? "" : key, ollama_url:$("ollama-url").value.trim()});
    if (revision !== modelListRevision || $("provider").value !== provider) return;
    const placeholder = document.createElement("option");
    placeholder.value = ""; placeholder.textContent = data.models.length ? "Выберите модель из списка" : "Доступные модели не найдены";
    $("gemini-model-choice").replaceChildren(placeholder, ...data.models.map(model => {
      const option = document.createElement("option");
      option.value = model; option.textContent = model; return option;
    }));
    $("gemini-model-choice").disabled = !data.models.length;
    const current = $("model").value.trim();
    const found = data.models.includes(current);
    $("gemini-model-choice").value = found ? current : "";
    const message = !data.models.length ? provider === "ollama" ? "В Ollama нет локальных моделей. Загрузите текстовую модель через Ollama и обновите список." : "Провайдер не вернул доступных моделей."
      : current && !found ? `Модели «${current}» нет в полученном списке. Выберите другое имя и примените настройки.`
      : `Получено моделей: ${data.models.length}. Выберите модель в списке и примените настройки.`;
    $("models-hint").textContent = message;
    notice(message, !data.models.length || Boolean(current && !found));
  } catch (error) {
    if (revision === modelListRevision) {
      $("models-hint").textContent = error.message;
      notice(error.message, true);
    }
  }
  finally { button.disabled = false; }
});
