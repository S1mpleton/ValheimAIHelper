"use strict";
// Credentials remain in this tab's memory, never localStorage, URLs or HTML.
let bridgeToken = "";
let lastStatus = null;
let polling = false;
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
    $("mode").textContent = `Режим: ${{stop: "стоп", follow: "следование без AI", ai: "AI"}[s.mode]}${s.mode === "ai" ? ` · ${s.model}` : ""}`;
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
    $("model").value = s.model || "gpt-4.1-mini";
    $("load-models").hidden = $("provider").value !== "gemini";
    $("objective").value = s.objective;
    $("pairing").hidden = true; $("dashboard").hidden = false;
    notice("Мост подключён. Включите мод на отдельном персонаже через F8, затем выберите режим здесь.");
    await refresh();
  } catch (error) { notice(error.message, true); }
});
$("provider").addEventListener("change", () => {
  const p = $("provider").value;
  $("load-models").hidden = p !== "gemini";
  $("gemini-models").replaceChildren();
  $("model").value = p === "gemini" ? "gemini-2.5-flash" : p === "openai" ? "gpt-4.1-mini" : "";
  $("model").required = p !== "offline";
  $("model").disabled = p === "offline";
  $("api-key").disabled = p === "offline";
  $("api-key").value = "";
  keyStatus();
});
$("provider-form").addEventListener("submit", async e => {
  e.preventDefault();
  const key = $("api-key").value.trim();
  $("api-key").value = "";
  try {
    await api("/api/provider", {provider: $("provider").value, model: $("model").value.trim(), api_key: key});
    notice("Настройки применены. Ключ будет проверен первым запросом AI из игры. Управление остановлено.");
    await refresh();
  } catch (error) { notice(error.message, true); }
});
async function command(mode) {
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


$("load-models").addEventListener("click", async () => {
  const button = $("load-models");
  button.disabled = true;
  const key = $("api-key").value.trim();
  try {
    const data = await api("/api/models/gemini", {api_key: key});
    if ($("provider").value !== "gemini") return;
    $("gemini-models").replaceChildren(...data.models.map(model => {
      const option = document.createElement("option"); option.value = model; return option;
    }));
    notice(data.models.length ? `Получено моделей: ${data.models.length}. Выберите модель в поле выше и примените настройки.` : "API не вернул моделей с generateContent.");
  } catch (error) { notice(error.message, true); }
  finally { button.disabled = false; }
});
