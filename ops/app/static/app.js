/* PRIVÉ Ops — панель оператора. Один файл, без сборки. */
let META = {};
const $ = (s, el = document) => el.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const dt = s => s ? new Date(s).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—";
const overdue = s => s && new Date(s) < new Date();

async function api(method, url, body, isForm) {
  const opt = { method, headers: {} };
  if (body !== undefined) {
    if (isForm) opt.body = body; else { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  }
  const r = await fetch(url, opt);
  if (!r.ok) { const t = await r.text(); toast("Ошибка: " + (JSON.parse(t).detail || t), true); throw new Error(t); }
  return r.json();
}
function toast(msg, bad) { const d = document.createElement("div"); d.textContent = msg; if (bad) d.style.borderColor = "var(--bad)"; $("#toast").appendChild(d); setTimeout(() => d.remove(), 4000); }
function modal(html) { $("#modal-box").innerHTML = html; $("#modal").classList.remove("hidden"); }
function closeModal() { $("#modal").classList.add("hidden"); }
$("#modal").addEventListener("click", e => { if (e.target.id === "modal") closeModal(); });
const tag = (txt, cls = "") => `<span class="tag ${cls}">${esc(txt)}</span>`;
const stTag = (status, dict) => tag(dict?.[status] || status, { new: "info", review: "warn", takedown: "warn", monitoring: "acc", done: "ok", removed: "ok", confirmed: "acc", sent: "info", escalated: "bad", returned: "bad", error: "bad", verifying: "warn", draft: "" }[status] || "");

/* ---------------- роутер ---------------- */
const views = {};
async function route() {
  const name = (location.hash || "#dashboard").slice(1).split("/")[0];
  const arg = location.hash.split("/")[1];
  document.querySelectorAll("#nav a").forEach(a => a.classList.toggle("active", a.dataset.view === name));
  $("#view").innerHTML = '<p class="muted">Загрузка…</p>';
  try { await (views[name] || views.dashboard)(arg); } catch (e) { console.error(e); }
  badges();
}
window.addEventListener("hashchange", route);

async function badges() {
  const d = await api("GET", "/api/dashboard");
  $("#n-verify").textContent = d.clients_to_verify || "";
  $("#n-findings").textContent = d.findings_to_review || "";
  $("#n-td").textContent = (d.takedowns_due + d.takedowns_draft) || "";
  $("#n-dash").textContent = d.sos_open ? "SOS" : "";
}

/* ---------------- дашборд ---------------- */
views.dashboard = async () => {
  const d = await api("GET", "/api/dashboard");
  const sos = await api("GET", "/api/orders?service=sos_consult");
  const sos2 = await api("GET", "/api/orders?service=sos");
  const open = [...sos, ...sos2].filter(o => !["done", "cancelled"].includes(o.status));
  const card = (n, l, cls = "", href = "") => `<div class="card ${cls} ${href ? "link" : ""}" ${href ? `onclick="location.hash='${href}'"` : ""}><div class="num">${n}</div><div class="lbl">${l}</div></div>`;
  const ints = d.integrations;
  const intCount = [ints.search.google_cse, ints.search.brave, ints.search.serpapi, ints.leaks.hibp, ints.leaks.dehashed, ints.takedown.mail_configured, ints.telegram.bot].filter(Boolean).length;
  $("#view").innerHTML = `
    <h1>Дашборд</h1>
    <div class="grid cards">
      ${card(d.sos_open, "SOS в работе", d.sos_open ? "bad" : "", "#orders/sos")}
      ${card(d.clients_to_verify, "кружков на проверку", d.clients_to_verify ? "warn" : "", "#verify")}
      ${card(d.findings_to_review, "находок на разбор", d.findings_to_review ? "warn" : "", "#findings")}
      ${card(d.takedowns_draft, "черновиков жалоб", "", "#takedowns/draft")}
      ${card(d.takedowns_due, "снятий к повторной проверке", d.takedowns_due ? "warn" : "", "#takedowns")}
      ${card(d.orders_overdue, "заказов просрочено", d.orders_overdue ? "bad" : "", "#orders")}
      ${card(d.removed_total, "снято всего", "ok")}
      ${card(intCount + "/7", "интеграций настроено", intCount < 3 ? "warn" : "ok", "#integrations")}
    </div>
    <h2>Воронка за 30 дней</h2>
    <div class="grid cards">
      ${card(d.funnel.clients_new, "новых клиенток")}${card(d.funnel.clients_verified, "верифицировано")}${card(d.funnel.free_reports_sent + "/" + d.funnel.free_checks, "бесплатных отчётов / заявок")}
      ${card(d.funnel.paid_checks, "платных проверок", "ok")}${card(d.funnel.conversion_free_to_paid_pct ?? "—", "% конверсия в платную", (d.funnel.conversion_free_to_paid_pct ?? 0) < 6 ? "warn" : "ok")}
      ${card(d.funnel.median_hours_to_free_report ?? "—", "медиана часов до бесплатного отчёта", (d.funnel.median_hours_to_free_report ?? 0) > 24 ? "bad" : "ok")}
      ${card("$" + d.funnel.revenue_usd, "выручка")}${card("$" + d.funnel.api_cost_usd, "расходы на API")}
    </div>
    ${open.length ? `<h2>SOS — ответить в течение 15 минут</h2>${ordersTable(open)}` : ""}
    <h2>Заказы по статусам</h2>
    <div class="row">${Object.entries(d.orders_by_status).map(([s, n]) => `<a href="#orders/${s}" class="tag">${esc(META.order_statuses[s] || s)}: ${n}</a>`).join("") || '<span class="muted">пока нет заказов</span>'}</div>`;
};

/* ---------------- заявки / верификация ---------------- */
views.verify = async () => {
  const clients = await api("GET", "/api/clients");
  const rows = clients.map(c => `<tr>
    <td><b>${esc(c.code)}</b><br><span class="muted small">${esc(c.display)} ${c.telegram_id ? "· tg " + esc(c.telegram_id) : ""}</span></td>
    <td>${stTag(c.status, { new: "Ждёт проверки", verified: "Подтверждена", rejected: "Отказ" })}</td>
    <td>${(c.reference_photos || []).map(p => `<a href="/files/${esc(p.file)}" target="_blank"><img src="/files/${esc(p.file)}" style="height:46px;border-radius:4px;margin:2px"></a>`).join("") || '<span class="muted">нет эталонов</span>'}</td>
    <td class="small">${esc(c.verification_note)} ${c.verification_score != null ? "· score " + c.verification_score : ""}</td>
    <td>${c.orders_count} · <span class="muted small">${dt(c.created_at)}</span></td>
    <td class="row">
      <button class="btn sm" onclick="verify(${c.id},'verified')">Подтвердить</button>
      <button class="btn sm ghost" onclick="verify(${c.id},'retry')">Новый кружок</button>
      <button class="btn sm ghost" onclick="verify(${c.id},'rejected')">Отказ</button>
      <button class="btn sm ghost" onclick="uploadPhoto(${c.id})">+ фото</button>
      ${c.circle_file ? `<a class="btn sm ghost" href="/files/${esc(c.circle_file)}" target="_blank">кружок</a><button class="btn sm ghost" onclick="api('POST','/api/clients/${c.id}/auto-verify').then(r=>{toast('Авто: '+r.decision+(r.score!=null?' ('+r.score+')':'')+' '+(r.reason||''));route()})">авто-сверка</button>` : ""}
      <button class="btn sm ghost" onclick="newOrder(${c.id})">+ заказ</button>
    </td></tr>`).join("");
  $("#view").innerHTML = `<h1>Заявки и верификация (SOP-3)</h1>
    <div class="toolbar"><button class="btn" onclick="newClient()">+ Клиентка вручную</button>
    <span class="muted small">Проверить: живой человек на кружке, то же лицо, что на эталонах, нет экрана/фото. Отказ — одним сообщением без причин.</span></div>
    <table><tr><th>Код</th><th>Статус</th><th>Эталоны</th><th>Заметка</th><th>Заказов</th><th></th></tr>${rows || '<tr><td colspan=6 class="empty">Пусто</td></tr>'}</table>`;
};
async function verify(id, decision) {
  const note = decision === "retry" ? prompt("Жест для нового кружка:", "коснись носа") : decision === "rejected" ? (prompt("Внутренняя причина (клиентке не отправляется):") || "") : "";
  if (note === null) return;
  await api("POST", `/api/clients/${id}/verify`, { decision, note }); toast("Статус обновлён"); route();
}
async function newClient() {
  const display = prompt("Внутренний ярлык (не имя!):"); if (display === null) return;
  const telegram_id = prompt("Telegram chat id (если известен):") || "";
  await api("POST", "/api/clients", { display, telegram_id }); route();
}
function uploadPhoto(id) {
  const i = document.createElement("input"); i.type = "file"; i.accept = "image/*"; i.multiple = true;
  i.onchange = async () => { for (const f of i.files) { const fd = new FormData(); fd.append("file", f); await api("POST", `/api/clients/${id}/photos`, fd, true); } toast("Фото загружены"); route(); };
  i.click();
}

/* ---------------- заказы ---------------- */
function ordersTable(list) {
  return `<table><tr><th>#</th><th>Клиентка</th><th>Услуга</th><th>Статус</th><th>Находки</th><th>Оплата</th><th>Срок</th><th></th></tr>
  ${list.map(o => `<tr class="${o.priority === 1 ? "sos" : ""}">
    <td>${o.id}</td><td>${esc(o.client_code)} ${o.client_status !== "verified" ? tag("не верифицирована", "warn") : ""}</td>
    <td>${esc(o.service_name)}</td><td>${stTag(o.status, META.order_statuses)}</td>
    <td>${o.findings_total} ${o.findings_by_status.new ? tag(o.findings_by_status.new + " новых", "warn") : ""} ${o.findings_by_status.removed ? tag(o.findings_by_status.removed + " снято", "ok") : ""}</td>
    <td>${o.paid_usd}/${o.price_usd} $</td>
    <td class="${overdue(o.due_at) && !["done", "cancelled"].includes(o.status) ? "" : "muted"}" style="${overdue(o.due_at) ? "color:var(--bad)" : ""}">${dt(o.due_at)}</td>
    <td><a href="#order/${o.id}" class="btn sm ghost">Открыть</a></td></tr>`).join("") || '<tr><td colspan=8 class="empty">Пусто</td></tr>'}</table>`;
}
views.orders = async (arg) => {
  let url = "/api/orders";
  if (arg === "sos") url += "?service=sos_consult"; else if (arg) url += "?status=" + arg;
  let list = await api("GET", url);
  if (arg === "sos") list = [...list, ...(await api("GET", "/api/orders?service=sos"))];
  const opts = Object.entries(META.order_statuses).map(([k, v]) => `<option value="${k}" ${arg === k ? "selected" : ""}>${v}</option>`).join("");
  $("#view").innerHTML = `<h1>Заказы</h1>
    <div class="toolbar"><select onchange="location.hash='#orders/'+this.value"><option value="">Все статусы</option>${opts}</select>
    <button class="btn" onclick="newOrder()">+ Заказ</button></div>${ordersTable(list)}`;
};
async function newOrder(clientId) {
  if (!clientId) { clientId = prompt("ID клиентки:"); if (!clientId) return; }
  const opts = Object.entries(META.services).map(([k, v]) => `<option value="${k}">${v.name} — $${v.price}</option>`).join("");
  modal(`<h2>Новый заказ</h2>
    <div class="field"><label>Услуга</label><select id="f-service">${opts}</select></div>
    <div class="field"><label>Рабочие ники (через запятую)</label><input id="f-nicks"></div>
    <div class="field"><label>Телефоны</label><input id="f-phones"></div>
    <div class="field"><label>Почты</label><input id="f-emails"></div>
    <div class="field"><label>Характерные фразы анкеты</label><input id="f-phrases"></div>
    <div class="field"><label>Город (для поиска по семействам клонов)</label><input id="f-city" placeholder="Киев, Москва, Дубай…"></div>
    <div class="field"><label>Заметки</label><textarea id="f-notes"></textarea></div>
    <div class="row"><button class="btn" id="f-ok">Создать</button><button class="btn ghost" onclick="closeModal()">Отмена</button></div>`);
  const split = id => $(id).value.split(",").map(s => s.trim()).filter(Boolean);
  $("#f-ok").onclick = async () => {
    const o = await api("POST", "/api/orders", { client_id: +clientId, service: $("#f-service").value, notes: $("#f-notes").value,
      queries: { nicks: split("#f-nicks"), phones: split("#f-phones"), emails: split("#f-emails"), phrases: split("#f-phrases"), city: $("#f-city").value.trim() } });
    closeModal(); location.hash = "#order/" + o.id;
  };
}

/* ---------------- карточка заказа ---------------- */
views.order = async (id) => {
  const o = await api("GET", `/api/orders/${id}`);
  const q = o.queries || {};
  const kindOpts = f => Object.entries(META.finding_kinds).map(([k, v]) => `<option value="${k}" ${f.kind === k ? "selected" : ""}>${v}</option>`).join("");
  const stOpts = f => Object.entries(META.finding_statuses).map(([k, v]) => `<option value="${k}" ${f.status === k ? "selected" : ""}>${v}</option>`).join("");
  const frows = o.findings.map(f => `<tr>
    <td><input type="checkbox" class="fsel" value="${f.id}"></td>
    <td class="url"><a href="${esc(f.url)}" target="_blank" rel="noopener">${esc(f.url)}</a><br><span class="muted small">${esc(f.title)}</span></td>
    <td>${tag(f.source)}<br><span class="muted small">${esc(f.query_type)}: ${esc(f.query_value)}</span>${f.auto_note ? `<br><span class="small" style="color:var(--accent2)">${esc(f.auto_note)}</span>` : ""}${f.verify && f.verify.signals && f.verify.signals.length ? `<br><span class="muted small">на странице: ${esc(f.verify.signals.join(", "))}</span>` : ""}</td>
    <td><select class="inline" onchange="patchF(${f.id},{kind:this.value})">${kindOpts(f)}</select><br>
        <select class="inline" onchange="patchF(${f.id},{risk:+this.value})"><option value="0" ${!f.risk ? "selected" : ""}>риск ?</option><option value="1" ${f.risk === 1 ? "selected" : ""}>риск 1</option><option value="2" ${f.risk === 2 ? "selected" : ""}>риск 2</option><option value="3" ${f.risk === 3 ? "selected" : ""}>риск 3</option></select></td>
    <td><select class="inline" onchange="patchF(${f.id},{status:this.value})">${stOpts(f)}</select></td>
    <td class="small">${f.evidence.length ? f.evidence.map(e => `${e.sha256 ? "sha " + e.sha256.slice(0, 10) : ""} ${e.archive_url ? `<a href="${esc(e.archive_url)}" target="_blank">archive</a>` : ""} ${e.screenshot_file ? `<a href="/files/${esc(e.screenshot_file)}" target="_blank">скрин</a>` : ""}<br><span class="muted">${esc(e.note)}</span>`).join("<br>") : '<span class="muted">нет</span>'}</td>
    <td class="small">${f.takedowns.map(t => `${stTag(t.status, META.takedown_statuses)} <span class="wave">w${t.wave}</span> ${esc(t.platform)}`).join("<br>") || "—"}</td>
    <td class="row"><button class="btn sm ghost" onclick="capture(${f.id})">Зафиксировать</button><button class="btn sm ghost" onclick="takedownDialog([${f.id}])">Жалоба</button><button class="btn sm ghost" title="Спросить в боте: это её размещение?" onclick="api('POST','/api/findings/${f.id}/ask-client').then(r=>toast(r.sent?'Вопрос отправлен клиентке':'Бот не настроен'))">Это твоё?</button></td></tr>`).join("");
  const inc = o.incident;
  const incident = inc ? `<h2>Карточка SOS-инцидента (SOP-4)</h2><div class="card"><div class="two">
    ${[["extortionist", "Кто (ник/номер, без пробива)"], ["what_they_have", "Что у него есть"], ["demand", "Что требует"], ["deadline", "Сроки"], ["already_sent_to", "Что уже отправлено и куда"], ["relatives_at_risk", "Кто из близких под риском"], ["stopncii_case", "Кейс StopNCII (SOP-5)"]].map(([k, l]) => `<div class="field"><label>${l}</label><input id="inc-${k}" value="${esc(inc[k])}"></div>`).join("")}
    <div class="field"><label>Физическая угроза прямо сейчас?</label><select id="inc-physical_danger"><option value="false" ${!inc.physical_danger ? "selected" : ""}>нет</option><option value="true" ${inc.physical_danger ? "selected" : ""}>ДА — экстренная служба и юрист</option></select></div>
    </div>
    <div class="field"><label>План на 72 часа (уйдёт клиентке PDF/текстом)</label><textarea id="inc-plan_72h">${esc(inc.plan_72h || "1. Не платить. 2. Не удалять переписку. 3. Заблокировать по чек-листу. 4. Что сказать родным: … 5. Юрист: …")}</textarea></div>
    <h2>Чек-лист блокировки (SOP-6)</h2>
    ${["Telegram: блок, приватность «мои контакты», номер «никто», чужие сеансы", "Instagram/VK/TikTok: закрыть, убрать номер и почту из поиска, 2FA через приложение", "Рабочая сим-карта: без звонков, не отключать", "Почта: переадресации и сеансы", "Родные: закрытые профили, ЛС от незнакомых отключены", "Хеши в StopNCII созданы"].map((t, i) => `<label class="check"><input type="checkbox" class="inc-chk" data-i="${i}" ${inc.checklist?.[i] ? "checked" : ""}> ${t}</label>`).join("")}
    <div class="row" style="margin-top:10px"><button class="btn" onclick="saveIncident(${o.id})">Сохранить карточку</button></div></div>` : "";
  $("#view").innerHTML = `<h1>Заказ #${o.id} · ${esc(o.service_name)} ${o.priority === 1 ? tag("SOS", "bad") : ""} ${stTag(o.status, META.order_statuses)}</h1>
    <div class="three">
      <div class="card"><div class="kv">
        <b>Клиентка</b><span>${esc(o.client.code)} ${o.client.status !== "verified" ? tag("не верифицирована", "warn") : tag("верифицирована", "ok")} · <a href="#verify">заявки</a></span>
        <b>Цена / оплачено</b><span>$${o.price_usd} / $<input class="inline" style="width:70px" value="${o.paid_usd}" onchange="api('PATCH','/api/orders/${o.id}',{paid_usd:+this.value}).then(()=>toast('Оплата сохранена'))">
          ${o.payment_claimed_at ? tag("клиентка: «оплатила» " + dt(o.payment_claimed_at), "warn") : ""}
          ${o.paid_usd < o.price_usd ? `<button class="btn sm" onclick="api('POST','/api/orders/${o.id}/confirm-payment').then(()=>{toast('Оплата подтверждена, клиентке написано');route()})">Подтвердить оплату</button>` : ""}</span>
        <b>Срок</b><span style="${overdue(o.due_at) ? "color:var(--bad)" : ""}">${dt(o.due_at)}</span>
        <b>Расход API</b><span>$${o.api_cost_usd}</span>
        <b>Контроль до</b><span>${dt(o.monitoring_until)} ${o.next_scan_at ? "· след. скан " + dt(o.next_scan_at) : ""}</span>
        <b>Статус</b><span><select class="inline" onchange="api('PATCH','/api/orders/${o.id}',{status:this.value}).then(route)">${Object.entries(META.order_statuses).map(([k, v]) => `<option value="${k}" ${o.status === k ? "selected" : ""}>${v}</option>`).join("")}</select></span>
      </div></div>
      <div class="card"><b class="muted small">ЧТО ИЩЕМ</b>
        ${[["nicks", "Ники"], ["phones", "Телефоны"], ["emails", "Почты"], ["phrases", "Фразы"]].map(([k, l]) => `<div class="field"><label>${l}</label><input id="q-${k}" value="${esc((q[k] || []).join(", "))}"></div>`).join("")}
        <div class="field"><label>Город</label><input id="q-city" value="${esc(q.city || "")}"></div>
        <div class="field"><label>Реальное имя (необязательно; только чтобы поймать связку с личным, в отчёт не попадает)</label><input id="q-real_name" value="${esc(q.real_name || "")}"></div>
        <div class="row"><button class="btn sm ghost" onclick="saveQueries(${o.id})">Сохранить</button>
        <button class="btn sm" onclick="runScan(${o.id})" ${o.client.status !== "verified" ? "disabled title='сначала верификация'" : ""}>▶ Запустить скан (M3)</button></div>
        <p class="muted small">Скан идёт по настроенным провайдерам. Поиск по лицу — PimEyes/Lens по SOP-1, результат импортируется CSV ниже.</p></div>
      <div class="card"><b class="muted small">ОТЧЁТ КЛИЕНТКЕ</b><pre style="max-height:160px">${esc(o.report_preview.replace(/<\/?b>/g, ""))}</pre>
        <div class="row"><button class="btn sm" onclick="api('POST','/api/orders/${o.id}/report').then(r=>toast(r.sent?'Отчёт отправлен в бот':'Бот не настроен — отчёт не ушёл (см. предпросмотр)'))">Текст в Telegram</button>
        <a class="btn sm ghost" href="/api/orders/${o.id}/pdf/client" target="_blank">PDF клиентке</a>
        <button class="btn sm ghost" onclick="api('POST','/api/orders/${o.id}/send-pdf').then(r=>toast(r.sent?'PDF отправлен в бот':'Бот не настроен'))">PDF в Telegram</button>
        <a class="btn sm ghost" href="/api/orders/${o.id}/pdf/lawyer" target="_blank">Пакет юристу (PDF)</a></div>
        <div class="row" style="margin-top:6px"><label class="btn sm ghost" title="PDF-отчёт PimEyes, сохранённая HTML-страница или текст со ссылками">Импорт отчёта PimEyes<input type="file" accept=".pdf,.html,.htm,.txt" hidden onchange="importReport(${o.id},this.files[0],'pimeyes')"></label>
        <label class="btn sm ghost">Импорт CSV<input type="file" accept=".csv" hidden onchange="importCsv(${o.id},this.files[0])"></label>
        <button class="btn sm ghost" onclick="pickRefs(${o.client_id})">Выбрать 3 эталона</button>
        <button class="btn sm ghost" onclick="addFinding(${o.id})">+ ссылка вручную</button></div>
        <div class="field" style="margin-top:8px"><label>Заметки</label><textarea id="o-notes" style="min-height:60px">${esc(o.notes)}</textarea><button class="btn sm ghost" onclick="api('PATCH','/api/orders/${o.id}',{notes:$('#o-notes').value}).then(()=>toast('Сохранено'))">Сохранить заметки</button></div></div>
    </div>
    ${incident}
    <h2>Находки (${o.findings.length})</h2>
    <div class="toolbar"><label><input type="checkbox" onchange="document.querySelectorAll('.fsel').forEach(c=>c.checked=this.checked)"> все</label>
      <button class="btn sm ghost" onclick="bulk({status:'confirmed'})">Подтвердить</button>
      <button class="btn sm ghost" onclick="bulk({status:'ignored'})">Не её</button>
      <button class="btn sm ghost" onclick="selected().forEach(capture)">Зафиксировать выбранные</button>
      <button class="btn sm" onclick="enrichOrder(${o.id})" title="Скачать страницы, проверить ник/телефон/город, сверить фото, выставить тип и риск по правилам, зафиксировать подтверждённые">Верифицировать и классифицировать</button>
      <button class="btn sm ghost" onclick="matchPhotos(${o.id})">Сверить фото с эталонами (M4)</button>
      <button class="btn sm ghost" title="FaceCheck + Search4faces по 3 эталонам" onclick="api('POST','/api/orders/${o.id}/face-search').then(()=>{toast('Поиск по лицу запущен (FaceCheck, Search4faces)');setTimeout(route,8000)})">Поиск по лицу (API)</button>
      <button class="btn sm" onclick="takedownDialog(selected())">Жалобы на выбранные (пакетно)</button></div>
    <table><tr><th></th><th>URL</th><th>Источник</th><th>Тип / риск</th><th>Статус</th><th>Доказательства</th><th>Снятия</th><th></th></tr>${frows || '<tr><td colspan=8 class="empty">Находок нет — запустите скан или импортируйте CSV</td></tr>'}</table>
    <h2>Задачи конвейера</h2>
    <table><tr><th>#</th><th>Тип</th><th>Статус</th><th>Результат</th><th>Когда</th></tr>${(o.jobs || []).map(j => `<tr><td>${j.id}</td><td>${j.type}</td><td>${stTag(j.status, { queued: "в очереди", running: "выполняется", done: "готово", error: "ошибка" })}</td><td class="small mono">${esc(JSON.stringify(j.result))} ${esc(j.error)}</td><td class="muted small">${dt(j.created_at)}</td></tr>`).join("") || '<tr><td colspan=5 class="muted">нет</td></tr>'}</table>`;
};
const selected = () => [...document.querySelectorAll(".fsel:checked")].map(c => +c.value);
async function patchF(id, patch) { await api("PATCH", `/api/findings/${id}`, patch); badges(); }
async function bulk(patch) { const ids = selected(); if (!ids.length) return toast("Ничего не выбрано", true); await api("POST", "/api/findings/bulk", { ids, patch }); route(); }
async function enrichOrder(oid) { const ids = selected(); await api("POST", `/api/orders/${oid}/enrich`, ids.length ? ids : null); toast("Верификация запущена — страницы скачиваются, правила применяются"); setTimeout(route, 6000); }
async function matchPhotos(oid) { const ids = selected(); await api("POST", `/api/orders/${oid}/match-photos`, ids.length ? ids : null); toast("Сверка запущена: pHash по страницам находок"); setTimeout(route, 4000); }
async function capture(id) { await api("POST", `/api/findings/${id}/capture`); toast("Фиксация запущена"); setTimeout(route, 2500); }
async function saveQueries(id) {
  const split = k => $("#q-" + k).value.split(",").map(s => s.trim()).filter(Boolean);
  await api("PATCH", `/api/orders/${id}`, { queries: { nicks: split("nicks"), phones: split("phones"), emails: split("emails"), phrases: split("phrases"), city: $("#q-city").value.trim(), real_name: $("#q-real_name").value.trim() } }); toast("Сохранено");
}
async function runScan(id) { await saveQueries(id); await api("POST", `/api/orders/${id}/scan`); toast("Скан запущен"); setTimeout(route, 3000); }
async function importCsv(id, file) { const fd = new FormData(); fd.append("file", file); const r = await api("POST", `/api/orders/${id}/import`, fd, true); toast(`Импорт: +${r.added}, дублей ${r.skipped_duplicates}`); route(); }
async function importReport(id, file, source) { const fd = new FormData(); fd.append("file", file); const r = await api("POST", `/api/orders/${id}/import-report?source=${source}`, fd, true); toast(`Ссылок в отчёте: ${r.urls_found}, добавлено ${r.added}, дублей ${r.skipped_duplicates}`); route(); }
async function pickRefs(cid) {
  const r = await api("POST", `/api/clients/${cid}/pick-references`);
  const rows = r.details.map(d => `<tr style="${r.picked.includes(d.file) ? "background:#1f2a1f" : ""}"><td><a href="/files/${esc(d.file)}" target="_blank"><img src="/files/${esc(d.file)}" style="height:70px;border-radius:4px"></a></td><td>${r.picked.includes(d.file) ? tag("выбран", "ok") : ""}</td><td>${d.ok ? "ок" : tag(d.reason, "bad")}</td><td>${esc(d.pose)}</td><td>${d.face_px}px</td><td>${d.sharpness}</td><td>${d.consistency ?? "—"}</td><td>${d.score}</td></tr>`).join("");
  modal(`<h2>Эталоны для внешнего поиска</h2>${r.warning ? `<p style="color:var(--warn)">${esc(r.warning)}</p>` : ""}
    <p class="muted small">Критерии: одно лицо, ≥80 px, резкость ≥40, похожесть на остальные ≥0,25; по одному на ракурс (анфас / влево / вправо), затем лучшие по баллу. Эти файлы загружаем в PimEyes и Lenso по SOP-1.</p>
    <table><tr><th>Фото</th><th></th><th>Пригодно</th><th>Ракурс</th><th>Лицо</th><th>Резкость</th><th>Схожесть</th><th>Балл</th></tr>${rows || '<tr><td colspan=8 class="muted">нет данных</td></tr>'}</table>
    <div class="row" style="margin-top:10px"><button class="btn" onclick="closeModal()">Закрыть</button></div>`);
}
async function addFinding(id) { const url = prompt("URL:"); if (!url) return; await api("POST", `/api/orders/${id}/findings`, { url, source: "manual" }); route(); }
async function saveIncident(id) {
  const body = {}; ["extortionist", "what_they_have", "demand", "deadline", "already_sent_to", "relatives_at_risk", "stopncii_case", "plan_72h"].forEach(k => body[k] = $("#inc-" + k).value);
  body.physical_danger = $("#inc-physical_danger").value === "true";
  body.checklist = {}; document.querySelectorAll(".inc-chk").forEach(c => body.checklist[c.dataset.i] = c.checked);
  await api("PATCH", `/api/orders/${id}/incident`, body); toast("Карточка сохранена");
}
function takedownDialog(ids) {
  if (!ids.length) return toast("Ничего не выбрано", true);
  modal(`<h2>Жалобы на ${ids.length} ссыл.</h2>
    <div class="field"><label>Шаблон (пусто — по справочнику площадки)</label><select id="td-tpl"><option value="">авто по площадке</option>${META.templates.map(t => `<option>${t}</option>`).join("")}</select></div>
    <div class="field"><label>Номер кейса StopNCII (для шаблона ncii)</label><input id="td-case"></div>
    <div class="field"><label>Волна</label><select id="td-wave"><option value="1">1 — площадка</option><option value="2">2 — хостер / поисковики / платёжка</option></select></div>
    <div class="row"><button class="btn" id="td-ok">Создать черновики</button><button class="btn ghost" onclick="closeModal()">Отмена</button></div>`);
  $("#td-ok").onclick = async () => {
    const data = { template: $("#td-tpl").value || null, stopncii_case: $("#td-case").value, wave: +$("#td-wave").value };
    const r = await api("POST", "/api/findings/bulk-takedown", { ids, data });
    closeModal(); toast(`Черновиков создано: ${r.created.length}`); location.hash = "#takedowns/draft";
  };
}

/* ---------------- находки (все) ---------------- */
views.findings = async () => {
  const list = await api("GET", "/api/findings?status=new");
  $("#view").innerHTML = `<h1>Находки на разбор</h1><p class="muted">Свежие результаты сканов по всем заказам. Разбор делается в карточке заказа.</p>
    <table><tr><th>Заказ</th><th>URL</th><th>Источник</th><th>Тип (авто)</th><th>Найдено</th></tr>
    ${list.map(f => `<tr><td><a href="#order/${f.order_id}">#${f.order_id}</a></td><td class="url"><a href="${esc(f.url)}" target="_blank" rel="noopener">${esc(f.url)}</a></td><td>${tag(f.source)}</td><td>${esc(f.kind_ru)}</td><td class="muted small">${dt(f.found_at)}</td></tr>`).join("") || '<tr><td colspan=5 class="empty">Всё разобрано</td></tr>'}</table>`;
};

/* ---------------- снятия ---------------- */
views.takedowns = async (arg) => {
  const list = await api("GET", "/api/takedowns" + (arg ? "?status=" + arg : ""));
  const opts = Object.entries(META.takedown_statuses).map(([k, v]) => `<option value="${k}" ${arg === k ? "selected" : ""}>${v}</option>`).join("");
  $("#view").innerHTML = `<h1>Снятия (M6)</h1>
    <div class="toolbar"><select onchange="location.hash='#takedowns/'+this.value"><option value="">Все</option>${opts}</select>
    <span class="muted small">Email-канал уходит через Postmark/Mailgun автоматически; веб-формы подаёт оператор и жмёт «Отправлено». Повторная проверка — автоматически через ${7} дн. или кнопкой.</span></div>
    <table><tr><th>#</th><th>Заказ</th><th>Площадка · канал</th><th>URL</th><th>Статус</th><th>Проверка</th><th></th></tr>
    ${list.map(t => `<tr><td>${t.id} <span class="wave">w${t.wave}</span></td><td><a href="#order/${t.order_id}">#${t.order_id}</a><br><span class="muted small">${esc(t.client_code)}</span></td>
      <td>${esc(t.platform)}<br>${tag(t.channel)} ${tag(t.template)}</td><td class="url"><a href="${esc(t.url)}" target="_blank" rel="noopener">${esc(t.url)}</a></td>
      <td>${stTag(t.status, META.takedown_statuses)}<br><span class="muted small">${t.sent_at ? "отпр. " + dt(t.sent_at) : ""}</span></td>
      <td class="small" style="${overdue(t.next_check_at) ? "color:var(--warn)" : ""}">${dt(t.next_check_at)}</td>
      <td><button class="btn sm ghost" onclick="openTakedown(${t.id})">Открыть</button></td></tr>`).join("") || '<tr><td colspan=7 class="empty">Пусто</td></tr>'}</table>`;
};
async function openTakedown(id) {
  const t = (await api("GET", "/api/takedowns")).find(x => x.id === id);
  modal(`<h2>Жалоба #${t.id} · ${esc(t.platform)} ${stTag(t.status, META.takedown_statuses)}</h2>
    <div class="kv"><b>URL</b><a href="${esc(t.url)}" target="_blank">${esc(t.url)}</a><b>Канал</b><span>${t.channel} ${t.form_url ? `· <a href="${esc(t.form_url)}" target="_blank">открыть форму площадки</a> · <span class="mono">python -m scripts.form_assist ${t.id}</span> — откроет форму с заполненными полями` : ""}</span></div>
    <div class="field"><label>Кому (email)</label><input id="td-to" value="${esc(t.to_address)}" placeholder="abuse@hoster.example — найти через whois/RDAP"></div>
    <div class="field"><label>Тема</label><input id="td-subj" value="${esc(t.subject)}"></div>
    <div class="field"><label>Текст (копировать в форму или отправить письмом)</label><textarea id="td-body" style="min-height:220px">${esc(t.body)}</textarea></div>
    <div class="row">
      <button class="btn ghost" onclick="saveTd(${t.id})">Сохранить</button>
      <button class="btn" onclick="saveTd(${t.id}).then(()=>api('POST','/api/takedowns/${t.id}/send')).then(r=>{toast(r.sent?'Письмо отправлено':'Не отправлено: '+r.reason);closeModal();route()})">Отправить письмом</button>
      <button class="btn ghost" onclick="api('POST','/api/takedowns/${t.id}/mark-sent').then(()=>{closeModal();route()})">Отмечено отправленным (форма/вручную)</button>
      <button class="btn ghost" onclick="api('POST','/api/takedowns/${t.id}/recheck').then(r=>{toast(r.up===null?'Проверка не удалась':r.up?'Страница на месте':'Снято');closeModal();route()})">Проверить сейчас</button>
      <button class="btn ghost" onclick="api('PATCH','/api/takedowns/${t.id}',{status:'removed'}).then(()=>{closeModal();route()})">Снято</button>
      <button class="btn ghost" onclick="api('PATCH','/api/takedowns/${t.id}',{status:'escalated',note:'вручную'}).then(()=>{closeModal();route()})">Эскалация</button>
      <button class="btn ghost" onclick="closeModal()">Закрыть</button></div>
    <h2>Журнал</h2><div class="log">${t.log.map(l => `<div>${dt(l.at)} — ${esc(l.text)}</div>`).join("")}</div>`);
}
async function saveTd(id) { await api("PATCH", `/api/takedowns/${id}`, { to_address: $("#td-to").value, subject: $("#td-subj").value, body: $("#td-body").value }); toast("Сохранено"); }

/* ---------------- источники (закрытый список M3) ---------------- */
let SOURCES = [];
views.sources = async () => {
  SOURCES = await api("GET", "/api/sources");
  const conf = { high: "ok", medium: "warn", low: "bad" };
  const rows = SOURCES.map((s, i) => `<tr style="${s.enabled ? "" : "opacity:.5"}">
    <td><input type="checkbox" ${s.enabled ? "checked" : ""} onchange="SOURCES[${i}].enabled=this.checked;saveSources()"></td>
    <td><b>${esc(s.domain)}</b>${(s.region_sites || []).map(d => `<br><span class="muted small">+ ${esc(d)}</span>`).join("")}<br><span class="muted small">${esc(s.name)}</span></td>
    <td>${esc(s.region)}</td><td>${tag(s.type)}</td><td>${tag(s.method)}</td>
    <td>${tag(s.confidence, conf[s.confidence])}</td>
    <td class="small muted">${esc(s.notes)}</td>
    <td><button class="btn sm ghost" onclick="editSource(${i})">Изменить</button></td></tr>`).join("");
  $("#view").innerHTML = `<h1>Закрытый список источников (M3)</h1>
    <p class="muted">Скан прогоняет каждый ник и телефон по этим площадкам отдельными запросами <span class="mono">site:домен "ник"</span> через настроенные поисковики, семейства клонов — по названию. Каналы Telegram и сайты сливов сюда заранее не вносятся: добавляются по реальным находкам. Уверенность «low» — домен уточнить, затем включить.</p>
    <div class="toolbar"><button class="btn" onclick="editSource(-1)">+ Источник</button>
      <button class="btn ghost" onclick="api('POST','/api/sources/preview?nick=demo_nick').then(r=>modal('<h2>Запросы по списку (${'демо-ник'})</h2><p class=muted>'+r.count+' запросов за один скан по одному нику</p><pre>'+r.queries.map(esc).join('\\n')+'</pre><button class=btn onclick=closeModal()>Закрыть</button>'))">Показать запросы</button>
      <span class="muted small">Включено: ${SOURCES.filter(s => s.enabled).length} из ${SOURCES.length}</span></div>
    <table><tr><th></th><th>Домен</th><th>Регион</th><th>Тип</th><th>Метод</th><th>Уверенность</th><th>Заметки</th><th></th></tr>${rows}</table>`;
};
async function saveSources() { await api("PUT", "/api/sources", SOURCES); toast("Список сохранён"); }
function editSource(i) {
  const s = i >= 0 ? SOURCES[i] : { domain: "", name: "", region: "", type: "каталог анкет", method: "site_search", confidence: "medium", enabled: true, notes: "", region_sites: [] };
  const opt = (arr, v) => arr.map(x => `<option ${x === v ? "selected" : ""}>${x}</option>`).join("");
  modal(`<h2>${i >= 0 ? esc(s.domain) : "Новый источник"}</h2>
    <div class="two">
      <div class="field"><label>Домен (семейство — с *)</label><input id="s-domain" value="${esc(s.domain)}"></div>
      <div class="field"><label>Название</label><input id="s-name" value="${esc(s.name)}"></div>
      <div class="field"><label>Регион</label><input id="s-region" value="${esc(s.region)}"></div>
      <div class="field"><label>Зеркала / доп. домены (через запятую)</label><input id="s-mirrors" value="${esc((s.region_sites || []).join(", "))}"></div>
      <div class="field"><label>Тип</label><select id="s-type">${opt(["каталог анкет", "клоны-каталоги", "доска объявлений", "отзывы", "форум", "соцсеть", "платформа", "сливы / копии анкет", "хостинг изображений", "веб-архив"], s.type)}</select></div>
      <div class="field"><label>Метод</label><select id="s-method">${opt(["site_search", "url_search", "manual"], s.method)}</select></div>
      <div class="field"><label>URL внутреннего поиска (для url_search, {q} = запрос)</label><input id="s-url" value="${esc(s.search_url || "")}"></div>
      <div class="field"><label>Уверенность</label><select id="s-conf">${opt(["high", "medium", "low"], s.confidence)}</select></div>
    </div>
    <div class="field"><label>Заметки</label><textarea id="s-notes">${esc(s.notes)}</textarea></div>
    <div class="row"><button class="btn" id="s-ok">Сохранить</button>${i >= 0 ? `<button class="btn ghost" onclick="SOURCES.splice(${i},1);saveSources().then(()=>{closeModal();route()})">Удалить</button>` : ""}<button class="btn ghost" onclick="closeModal()">Отмена</button></div>`);
  $("#s-ok").onclick = async () => {
    const n = { ...s, domain: $("#s-domain").value.trim(), name: $("#s-name").value, region: $("#s-region").value, type: $("#s-type").value, method: $("#s-method").value,
      search_url: $("#s-url").value || undefined, confidence: $("#s-conf").value, notes: $("#s-notes").value, region_sites: $("#s-mirrors").value.split(",").map(x => x.trim()).filter(Boolean) };
    if (!n.domain) return;
    if (i >= 0) SOURCES[i] = n; else SOURCES.push(n);
    await saveSources(); closeModal(); route();
  };
}

/* ---------------- площадки ---------------- */
views.platforms = async () => {
  const p = await api("GET", "/api/platforms");
  const rows = Object.entries(p).filter(([k, v]) => !v.alias).map(([k, v]) => `<tr><td><b>${esc(k)}</b><br><span class="muted small">${esc(v.name || "")}</span></td><td>${tag(v.channel)} ${tag(v.template)}</td><td class="small">${esc(v.to || "")} ${v.form ? `<a href="${esc(v.form)}" target="_blank">форма</a>` : ""}</td><td class="small muted">${esc(v.notes || "")}</td><td><button class="btn sm ghost" onclick='editPlatform(${JSON.stringify(k)},${JSON.stringify(v)})'>Изменить</button></td></tr>`).join("");
  $("#view").innerHTML = `<h1>Справочник площадок</h1><p class="muted">Домен → канал подачи и шаблон. Ключ «*» — правило по умолчанию (хостер по whois). Пополняется по находкам первых заказов.</p>
    <div class="toolbar"><button class="btn" onclick="editPlatform('', {channel:'email',template:'dmca'})">+ Площадка</button></div>
    <table><tr><th>Домен</th><th>Канал · шаблон</th><th>Адрес / форма</th><th>Заметки</th><th></th></tr>${rows}</table>`;
};
function editPlatform(domain, v) {
  modal(`<h2>${domain || "Новая площадка"}</h2>
    <div class="field"><label>Домен (без www)</label><input id="p-dom" value="${esc(domain)}" ${domain ? "readonly" : ""}></div>
    <div class="field"><label>Название</label><input id="p-name" value="${esc(v.name || "")}"></div>
    <div class="two"><div class="field"><label>Канал</label><select id="p-ch"><option ${v.channel === "email" ? "selected" : ""}>email</option><option ${v.channel === "form" ? "selected" : ""}>form</option><option ${v.channel === "manual" ? "selected" : ""}>manual</option></select></div>
    <div class="field"><label>Шаблон</label><select id="p-tpl">${META.templates.map(t => `<option ${v.template === t ? "selected" : ""}>${t}</option>`).join("")}</select></div></div>
    <div class="field"><label>Email abuse</label><input id="p-to" value="${esc(v.to || "")}"></div>
    <div class="field"><label>URL формы</label><input id="p-form" value="${esc(v.form || "")}"></div>
    <div class="field"><label>Заметки оператору</label><textarea id="p-notes">${esc(v.notes || "")}</textarea></div>
    <div class="row"><button class="btn" id="p-ok">Сохранить</button><button class="btn ghost" onclick="closeModal()">Отмена</button></div>`);
  $("#p-ok").onclick = async () => {
    const d = $("#p-dom").value.trim(); if (!d) return;
    await api("PUT", `/api/platforms/${encodeURIComponent(d)}`, { name: $("#p-name").value, channel: $("#p-ch").value, template: $("#p-tpl").value, to: $("#p-to").value, form: $("#p-form").value, notes: $("#p-notes").value });
    closeModal(); route();
  };
}

/* ---------------- интеграции ---------------- */
views.integrations = async () => {
  const d = await api("GET", "/api/integrations");
  const s = d.status;
  const row = (name, ok, what, test, how) => `<tr><td><span class="${ok ? "ok-dot" : "no-dot"}"></span><b>${name}</b></td><td>${what}</td><td>${ok ? tag("настроено", "ok") : tag("не настроено", "bad")}</td><td class="small muted">${how}</td><td>${test ? `<button class="btn sm ghost" onclick="testInt('${test}')">Тест</button>` : ""}</td></tr>`;
  $("#view").innerHTML = `<h1>Интеграции</h1><p class="muted small">Ключи задаются в файле <span class="mono">${esc(d.env_file)}</span> (образец — .env.example), после правки перезапустить сервер. Ненастроенные шаги конвейер пропускает и пишет это в задачу.</p>
    <table><tr><th>Сервис</th><th>Для чего</th><th>Статус</th><th>Переменные</th><th></th></tr>
    ${row("Google Custom Search", s.search.google_cse, "M3: поиск по никам/телефонам (WL)", "google_cse", "GOOGLE_CSE_KEY, GOOGLE_CSE_CX")}
    ${row("Brave Search API", s.search.brave, "M3: второй поисковик (WL)", "brave", "BRAVE_SEARCH_KEY")}
    ${row("SerpAPI (Yandex, Lens)", s.search.serpapi, "M3: Yandex для СНГ, Google Lens по изображению (WL)", "serpapi_yandex", "SERPAPI_KEY")}
    ${row("Have I Been Pwned", s.leaks.hibp, "Утечки по почте (WL)", "hibp", "HIBP_API_KEY")}
    ${row("DeHashed", s.leaks.dehashed, "Утечки по телефону/нику (WL)", "", "DEHASHED_EMAIL, DEHASHED_API_KEY")}
    ${row("Почта (" + (s.takedown.mail_provider || "—") + ")", s.takedown.mail_configured, "M6: отправка жалоб с корпоративного домена (WL)", "mail", "MAIL_PROVIDER=postmark|mailgun, POSTMARK_TOKEN / MAILGUN_API_KEY+MAILGUN_DOMAIN, MAIL_FROM")}
    ${row("Telegram-бот", s.telegram.bot, "M1: приём заявок, кружки, отчёты, SOS-эскалация", "telegram", "TELEGRAM_BOT_TOKEN, TELEGRAM_OPERATOR_CHAT_ID, TELEGRAM_WEBHOOK_SECRET, PUBLIC_BASE_URL")}
    ${row("web.archive.org / archive.today", true, "M5: копии страниц с датой (ключи SPN необязательны)", "archive", "ARCHIVE_ORG_ACCESS_KEY, ARCHIVE_ORG_SECRET_KEY")}
    ${row("Playwright (скриншоты)", s.evidence.playwright, "M5: полноэкранный скриншот", "", "ENABLE_PLAYWRIGHT=true + pip install playwright && playwright install chromium")}
    ${row("OpenTimestamps", s.evidence.ots_client, "M5: метка времени хешей для юриста", "", "pip install opentimestamps-client")}
    ${row("FaceCheck.ID API", s.face.facecheck, "M4: поиск по лицу — соцсети, объявления (WL)", "facecheck", "FACECHECK_API_TOKEN; ~$0.30 за поиск, оплата криптой")}
    ${row("Search4faces API", s.face.search4faces, "M4: поиск по лицу в VK / OK / TikTok — главный для СНГ (WL); имя и город из ответа не сохраняются", "search4faces", "SEARCH4FACES_API_KEY; от 1 900 ₽ за 5 000 запросов")}
    ${row("InsightFace + ffmpeg", s.face.insightface && s.face.ffmpeg, "M1: авто-сверка кружка; M4: поиск лица на страницах (обрезанные фото, другой ракурс)", "", "pip install insightface onnxruntime opencv-python-headless; ffmpeg в системе")}
    </table>
    <div class="row" style="margin:12px 0"><button class="btn ghost" onclick="api('POST','/api/telegram/set-webhook').then(r=>toast(JSON.stringify(r)))">Установить Telegram webhook</button></div>
    <h2>Инструменты</h2>
    <div class="row"><label class="btn ghost">Очистить метаданные фото (EXIF/GPS) — «Невидимка», шаг 2<input type="file" accept="image/*" hidden onchange="stripMeta(this.files[0])"></label>
    <span class="muted small">Файл не сохраняется в реестре; результат скачивается сразу.</span></div>
    <h2>Только как пользователь (без API) — результат импортируется CSV</h2>
    <ul class="steps">
      <li><b>PimEyes</b> — SOP-1: 3 эталона → экспорт URL → CSV → «Импорт CSV» в заказе.</li>
      <li><b>FaceCheck.ID, Google Lens, Yandex Images</b> — SOP-2, тот же CSV.</li>
      <li><b>StopNCII</b> — только клиентка сама; номер кейса в карточку инцидента.</li>
      <li><b>Google «удалить результаты о себе», Yandex, формы площадок</b> — черновик текста готовит панель, оператор подаёт форму и жмёт «Отмечено отправленным».</li>
      <li><b>GetContact / Truecaller / NumBuster, PimEyes opt-out</b> — инструкция клиентке (SOP-10), контроль через 7–14 дней задачей в заказе.</li>
    </ul>
    <h2>Журнал вызовов</h2>
    <table><tr><th>Когда</th><th>Сервис</th><th>Действие</th><th>Результат</th></tr>${d.log.map(l => `<tr><td class="muted small">${dt(l.created_at)}</td><td>${esc(l.service)}</td><td class="small">${esc(l.action)}</td><td class="small ${l.ok ? "" : ""}" style="${l.ok ? "" : "color:var(--bad)"}">${esc(l.detail)}</td></tr>`).join("") || '<tr><td colspan=4 class="muted">пусто</td></tr>'}</table>`;
};
async function stripMeta(file) {
  const fd = new FormData(); fd.append("file", file);
  const r = await fetch("/api/tools/strip-metadata", { method: "POST", body: fd });
  if (!r.ok) return toast("Не удалось обработать", true);
  const a = document.createElement("a"); a.href = URL.createObjectURL(await r.blob()); a.download = "clean_" + file.name; a.click();
}
async function testInt(name) { const r = await api("POST", `/api/integrations/test/${name}`); toast(r.ok ? "OK: " + JSON.stringify(r).slice(0, 120) : "Ошибка: " + r.error, !r.ok); }

/* ---------------- команда и журнал ---------------- */
views.team = async () => {
  if (META.role !== "admin" && META.role !== "manager") { $("#view").innerHTML = '<p class="empty">Доступно менеджеру и администратору</p>'; return; }
  const audit = await api("GET", "/api/audit");
  let users = [];
  if (META.role === "admin") users = await api("GET", "/api/users");
  $("#view").innerHTML = `<h1>Команда</h1>
    ${META.role === "admin" ? `<div class="toolbar"><button class="btn" onclick="editUser()">+ Оператор</button><span class="muted small">Роли: viewer — только чтение; duty — заявки, SOS, находки, снятия; manager — плюс справочники и интеграции; admin — плюс пользователи.</span></div>
    <table><tr><th>Логин</th><th>Роль</th><th>Активен</th><th>Создан</th><th></th></tr>${users.map(u => `<tr><td>${esc(u.username)}</td><td>${tag(u.role, "acc")}</td><td>${u.active ? "да" : "нет"}</td><td class="muted small">${dt(u.created_at)}</td><td><button class="btn sm ghost" onclick='editUser(${JSON.stringify(u)})'>Изменить</button></td></tr>`).join("")}</table>` : ""}
    <h2>Журнал действий</h2>
    <table><tr><th>Когда</th><th>Кто</th><th>Действие</th><th>Код</th></tr>${audit.map(a => `<tr><td class="muted small">${dt(a.created_at)}</td><td>${esc(a.user)}</td><td class="mono">${a.method} ${esc(a.path)}</td><td>${a.status}</td></tr>`).join("") || '<tr><td colspan=4 class="muted">пусто</td></tr>'}</table>`;
};
function editUser(u) {
  u = u || { username: "", role: "duty", active: true };
  modal(`<h2>${u.username || "Новый оператор"}</h2>
    <div class="field"><label>Логин</label><input id="u-name" value="${esc(u.username)}" ${u.id ? "readonly" : ""}></div>
    <div class="field"><label>Пароль ${u.id ? "(пусто — не менять)" : ""}</label><input id="u-pw" type="password"></div>
    <div class="two"><div class="field"><label>Роль</label><select id="u-role">${["viewer", "duty", "manager", "admin"].map(r => `<option ${u.role === r ? "selected" : ""}>${r}</option>`).join("")}</select></div>
    <div class="field"><label>Активен</label><select id="u-active"><option value="true" ${u.active ? "selected" : ""}>да</option><option value="false" ${!u.active ? "selected" : ""}>нет</option></select></div></div>
    <div class="row"><button class="btn" id="u-ok">Сохранить</button><button class="btn ghost" onclick="closeModal()">Отмена</button></div>`);
  $("#u-ok").onclick = async () => { await api("POST", "/api/users", { username: $("#u-name").value.trim(), password: $("#u-pw").value, role: $("#u-role").value, active: $("#u-active").value === "true" }); closeModal(); route(); };
}

/* ---------------- старт ---------------- */
(async () => { META = await api("GET", "/api/meta"); $("#who").textContent = META.user; route(); })();
