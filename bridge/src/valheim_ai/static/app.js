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
    signal: AbortSignal.timeout(110000)
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
  $("gemini-model-controls").hidden = p !== "gemini";
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
    $("mode").textContent = `Режим: ${{stop: "стоп", follow: "следование без AI", ai: "AI"}[s.mode]}${s.provider !== "offline" ? ` · ${s.provider === "gemini" ? "Gemini" : "OpenAI"} · ${s.model}` : ""}`;
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
    $("model").value = s.model || (s.provider === "gemini" ? "" : "gpt-4.1-mini");
    $("api-interval").value = s.request_interval_seconds;
    providerFields();
    clearGeminiModels();
    $("objective").value = s.objective;
    $("pairing").hidden = true; $("dashboard").hidden = false;
    notice("Мост подключён. Включите мод на отдельном персонаже через F8, затем выберите режим здесь.");
    await refresh();
  } catch (error) { notice(error.message, true); }
});
$("provider").addEventListener("change", () => {
  const p = $("provider").value;
  clearGeminiModels();
  $("model").value = p === "openai" ? "gpt-4.1-mini" : "";
  providerFields();
  $("api-key").value = "";
  keyStatus();
});
$("provider-form").addEventListener("submit", async e => {
  e.preventDefault();
  const key = $("api-key").value.trim();
  $("api-key").value = "";
  try {
    await api("/api/provider", {provider: $("provider").value, model: $("model").value.trim(), api_key: key,
      request_interval_seconds: Number($("api-interval").value)});
    notice("Настройки применены. Ключ будет проверен первым запросом AI из игры. Управление остановлено.");
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
    notice(mode === "ai" ? "AI включён. Каждый цикл обращается к API и может расходовать баланс." : mode === "follow" ? "Следование включено без запросов к API." : "Стоп отправлен. F9 в игре немедленно отключает мод.");
    await refresh();
  } catch (error) { notice(error.message, true); }
}
$("objective-form").addEventListener("submit", e => {e.preventDefault(); command("ai");});
$("follow").addEventListener("click", () => command("follow"));
$("stop").addEventListener("click", () => command("stop"));
setInterval(refresh, 2000);

$("api-key").addEventListener("input", clearGeminiModels);
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
  const revision = modelListRevision;
  const button = $("check-model");
  button.disabled = true;
  notice(`Проверяю Gemini / ${model}…`);
  try {
    const result = await api("/api/check/gemini", {model, api_key: key});
    if (revision === modelListRevision && $("model").value.trim() === model && $("provider").value === "gemini") {
      notice(`Gemini / ${model}: проверка прошла. ${result.checks.join("; ")}. Примените настройки, затем включите AI.`);
    }
  } catch (error) {
    if (revision === modelListRevision && $("model").value.trim() === model && $("provider").value === "gemini") {
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
  $("models-hint").textContent = "Получаю список моделей…";
  try {
    const data = await api("/api/models/gemini", {api_key: key});
    if (revision !== modelListRevision || $("provider").value !== "gemini") return;
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
    const message = !data.models.length ? "API не вернул моделей с generateContent."
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
