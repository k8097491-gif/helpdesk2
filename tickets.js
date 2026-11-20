// ============================================================
// tickets.js — списки заявок и карточка заявки
// ============================================================

// ---------- Данные страницы со списком ----------

let tickets = [];           // заявки, загруженные с сервера
let selectedId = null;      // номер (id) открытой заявки
let searchText = "";        // что набрано в поиске
let quickFilter = "";       // выбранная быстрая кнопка: "", "late", "WAITING" ...
let sortBy = "priority";    // как сортировать: priority | new | due

const LIST_TITLES = {
  queue: "Моя очередь",
  unassigned: "Заявки без исполнителя",
  all: "Все заявки",
  mine: "Мои обращения",
};

const LIST_HINTS = {
  queue: "Заявки, назначенные вам",
  unassigned: "Назначьте группу и исполнителя",
  all: "Все обращения университета",
  mine: "Заявки, которые вы создали",
};

const QUICK_FILTERS = [
  ["", "Все"],
  ["late", "Просроченные"],
  ["WAITING", "Ждут ответа"],
  ["ESCALATED", "Эскалированные"],
  ["RESOLVED", "Решённые"],
];

// Кнопки в карточке заявки: [действие, подпись, стиль].
// Показываются только те, которые сервер разрешил для этой заявки (поле ticket.actions).
const ACTION_BUTTONS = [
  ["start", "Взять в работу", "btn-main"],
  ["request_info", "Запросить сведения", ""],
  ["resolve", "Решить", "btn-main"],
  ["escalate", "Эскалировать", "btn-danger"],
  ["assign", "Назначить", ""],
  ["reclassify", "Изменить приоритет", ""],
  ["confirm", "Решение подходит — закрыть", "btn-main"],
  ["reopen", "Проблема осталась", "btn-danger"],
  ["rate", "Оценить поддержку", "btn-main"],
];

// Что показать в сообщении после успешного действия
const ACTION_DONE_TEXT = {
  start: "Заявка взята в работу",
  confirm: "Заявка закрыта. Спасибо!",
  request_info: "Вопрос отправлен заявителю",
  resolve: "Решение отправлено заявителю",
  reopen: "Заявка открыта повторно",
  escalate: "Заявка эскалирована",
  assign: "Исполнитель назначен",
  reclassify: "Приоритет и категория обновлены",
  rate: "Спасибо за оценку!",
};

// Класс для точки в истории заявки
const HISTORY_KIND_CLASS = {
  "Комментарий": "kind-comment",
  "Эскалация": "kind-escalation",
  "Статус": "kind-status",
};

const NOTHING_SELECTED_HTML = `
  <div class="empty">
    <svg class="empty-icon" viewBox="0 0 48 48" aria-hidden="true">
      <rect x="9" y="7" width="30" height="34" rx="4"/><path d="M16 17h16m-16 7h16m-16 7h10"/>
    </svg>
    <p class="empty-title">Карточка заявки</p>
    <p class="empty-copy">Выберите обращение из списка, чтобы увидеть детали и доступные действия.</p>
  </div>`;

// ---------- Страница со списком ----------

async function showTicketList() {
  quickFilter = "";
  sortBy = "priority";

  // Выпадающий список статусов нужен только диспетчеру на странице «Все заявки»
  let statusSelect = "";
  if (currentPage === "all") {
    const options = Object.entries(info.labels.status)
      .map(([key, text]) => `<option value="${key}">${text}</option>`)
      .join("");
    statusSelect = `
      <label class="visually-hidden" for="status-filter">Статус</label>
      <select id="status-filter"><option value="">Все статусы</option>${options}</select>`;
  }

  $("#content").innerHTML = `
    <div class="list-tools">
      <div class="page-head">
        <div>
          <h2>${LIST_TITLES[currentPage]}</h2>
          <div class="small muted">${LIST_HINTS[currentPage]}</div>
        </div>
        <button class="btn btn-main" data-go="new">Новая заявка</button>
      </div>

      <div class="filters">
        <label class="visually-hidden" for="search">Поиск</label>
        <input id="search" type="search" placeholder="Поиск по теме, номеру, автору">
        ${statusSelect}
        <label class="visually-hidden" for="sort">Сортировка</label>
        <select id="sort">
          <option value="priority">Сначала важные</option>
          <option value="new">Сначала новые</option>
          <option value="due">По сроку решения</option>
        </select>
      </div>

      <div class="quick-filters" id="quick-filters" role="group" aria-label="Быстрые фильтры"></div>
    </div>

    <div class="two-columns">
      <div class="ticket-list" id="ticket-list" aria-live="polite">${loadingHtml()}</div>
      <div id="ticket-details">${NOTHING_SELECTED_HTML}</div>
    </div>`;

  $("#search").oninput = () => {
    searchText = $("#search").value;
    drawTicketList();
  };
  $("#sort").onchange = () => {
    sortBy = $("#sort").value;
    drawTicketList();
  };
  if ($("#status-filter")) {
    $("#status-filter").onchange = loadTickets;
  }

  await loadTickets();
}

// Загружает заявки с сервера и перерисовывает список
async function loadTickets() {
  const status = $("#status-filter") ? $("#status-filter").value : "";
  let path = "/tickets?scope=" + currentPage;
  if (status) path += "&status=" + status;

  tickets = await api("GET", path);
  drawTicketList();
}

// Подходит ли заявка под быструю кнопку
function matchesQuickFilter(ticket, key) {
  if (key === "") return true;
  if (key === "late") return ticket.overdue;
  return ticket.status === key;
}

// Заявки, которые нужно показать: с учётом кнопки, поиска и сортировки
function visibleTickets() {
  const text = searchText.toLowerCase().trim();

  let result = tickets.filter((ticket) => {
    if (!matchesQuickFilter(ticket, quickFilter)) return false;
    if (text === "") return true;
    const haystack = ticket.title + ticket.number + (ticket.requester || "");
    return haystack.toLowerCase().includes(text);
  });

  if (sortBy === "new") {
    result = [...result].sort((a, b) => b.created_at.localeCompare(a.created_at));
  } else if (sortBy === "due") {
    result = [...result].sort((a, b) => (a.resolve_due_at || "").localeCompare(b.resolve_due_at || ""));
  }
  return result;
}

function drawTicketList() {
  drawQuickFilters();

  const shown = visibleTickets();
  if (shown.length > 0) {
    $("#ticket-list").innerHTML = shown.map(ticketRowHtml).join("");
  } else {
    $("#ticket-list").innerHTML = tickets.length
      ? emptyStateHtml("Ничего не найдено", "Измените запрос или сбросьте быстрый фильтр.")
      : emptyListHtml();
  }

  // Кнопки перехода на другую страницу («Новая заявка», «Создать заявку»)
  $$("[data-go]").forEach((button) => {
    button.onclick = () => showPage(button.dataset.go);
  });

  // Клик или Enter/пробел по строке открывает заявку
  $$(".ticket-row").forEach((row) => {
    const id = Number(row.dataset.id);
    row.onclick = () => openTicket(id);
    row.onkeydown = (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        openTicket(id);
      }
    };
  });
}

function drawQuickFilters() {
  $("#quick-filters").innerHTML = QUICK_FILTERS.map(([key, text]) => {
    const count = tickets.filter((ticket) => matchesQuickFilter(ticket, key)).length;
    return `<button class="quick-filter" data-key="${key}" aria-pressed="${quickFilter === key}">${text}<span>${count}</span></button>`;
  }).join("");

  $$(".quick-filter").forEach((button) => {
    button.onclick = () => {
      quickFilter = button.dataset.key;
      drawTicketList();
    };
  });
}

// Текст, когда в списке нечего показывать
function emptyListHtml() {
  const messages = {
    mine: "У вас пока нет обращений.",
    queue: "Очередь пуста — новых назначений нет.",
    unassigned: "Все заявки распределены.",
  };
  const text = messages[currentPage] || "Заявок пока нет.";

  let button = "";
  if (currentPage === "mine") {
    button = '<br><button class="btn btn-main" style="margin-top: 10px" data-go="new">Создать заявку</button>';
  }
  return `
    <div class="empty">
      <svg class="empty-icon" viewBox="0 0 48 48" aria-hidden="true">
        <rect x="7" y="10" width="34" height="28" rx="4"/><path d="M7 18h34m-25 8h16m-16 6h10"/>
      </svg>
      <p class="empty-title">${esc(text)}</p>
      ${button}
    </div>`;
}

function ticketRowHtml(ticket) {
  const isSelected = ticket.id === selectedId;
  const isOpen = OPEN_STATUSES.includes(ticket.status);

  const deadline = isOpen
    ? `<div class="ticket-deadline ${ticket.overdue ? "overdue" : ""}">
         <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8"/><path d="M12 7v5l3 2"/></svg>
         <span>${ticket.overdue ? "Просрочена" : "Срок решения"} · ${formatDate(ticket.resolve_due_at)}</span>
       </div>`
    : "";

  return `
    <div class="ticket-row priority-${ticket.priority} ${isSelected ? "selected" : ""}"
         data-id="${ticket.id}" tabindex="0" role="button" aria-pressed="${isSelected}">
      <div class="ticket-heading">
        <span class="ticket-number">${esc(ticket.number)}</span>
        ${statusBadge(ticket.status)}
        <span class="priority-badge priority-label-${ticket.priority}">${esc(label("priority", ticket.priority))}</span>
      </div>
      <div class="ticket-title">${esc(ticket.title)}</div>
      <div class="ticket-meta">
        <span>${esc(ticket.category)}</span><span aria-hidden="true">·</span>
        <span>${esc(ticket.assignee || "не назначена")}</span>
      </div>
      ${deadline}
    </div>`;
}

function statusBadge(status) {
  return `<span class="badge status-${status}">${esc(label("status", status))}</span>`;
}

// ---------- Карточка заявки ----------

async function openTicket(id) {
  selectedId = id;
  if ($("#ticket-list")) drawTicketList();   // подсветить выбранную строку

  const details = $("#ticket-details");
  if (details) details.innerHTML = `<div class="card">${loadingHtml()}</div>`;
  $("#content").classList.add("ticket-open");   // на телефоне прячет список
  window.scrollTo(0, 0);

  let ticket;
  try {
    ticket = await api("GET", "/tickets/" + id);
  } catch (error) {
    if ($("#ticket-details")) {
      $("#ticket-details").innerHTML = errorHtml(error.message);
      $(".retry-button").onclick = () => openTicket(id);
    }
    return;
  }

  // Пока заявка грузилась, пользователь мог уйти на другую страницу
  if (!$("#ticket-details")) return;

  $("#ticket-details").innerHTML = ticketCardHtml(ticket);

  $("#back-button").onclick = closeTicket;
  $$("#ticket-details [data-action]").forEach((button) => {
    button.onclick = () => doAction(ticket, button.dataset.action);
  });
  if ($("#send-comment")) {
    $("#send-comment").onclick = () => sendComment(ticket);
  }
}

// Кнопка «← К списку» (на телефоне)
function closeTicket() {
  selectedId = null;
  $("#content").classList.remove("ticket-open");
  $("#ticket-details").innerHTML = NOTHING_SELECTED_HTML;
  if ($("#ticket-list")) drawTicketList();
}

function ticketCardHtml(t) {
  const reopened = t.reopen_count
    ? `<span class="badge">открывалась повторно: ${t.reopen_count}</span>`
    : "";

  const resolution = t.resolution_text
    ? `<div class="info-box"><b>Решение</b><pre>${esc(t.resolution_text)}</pre></div>`
    : "";

  let feedback = "";
  if (t.feedback) {
    const stars = "★".repeat(t.feedback.rating) + "☆".repeat(5 - t.feedback.rating);
    feedback = `<div class="info-box">Оценка заявителя: ${stars} ${esc(t.feedback.comment || "")}</div>`;
  }

  const articles = t.kb.map((a) => `<pre><b>${esc(a.title)}</b>\n${esc(a.body)}</pre>`).join("");
  const kb = t.kb.length ? `<div class="info-box"><b>Похожие инструкции</b>${articles}</div>` : "";

  const links = t.attachments
    .map((a) => `<a href="/api/attachments/${a.id}">${esc(a.filename)}</a>`)
    .join(", ");
  const files = t.attachments.length ? `<div class="small" style="margin-bottom: 8px">Файлы: ${links}</div>` : "";

  return `
    <div class="card">
      <button class="btn back-button" id="back-button">← К списку</button>
      <div class="ticket-detail-heading">
        <div class="ticket-number">${esc(t.number)}</div>
        <div class="small muted">Создана ${formatDate(t.created_at)}</div>
      </div>
      <h2 class="ticket-detail-title">${esc(t.title)}</h2>
      <div class="ticket-detail-badges">
        ${statusBadge(t.status)}
        <span class="priority-badge priority-label-${t.priority}">${esc(label("priority", t.priority))}</span>
        ${reopened}
      </div>
      <p style="white-space: pre-wrap; overflow-wrap: anywhere">${esc(t.description)}</p>

      ${ticketFactsHtml(t)}
      ${deadlineHtml(t)}
      ${resolution}
      ${feedback}
      ${kb}

      <div class="ticket-actions">${actionButtonsHtml(t)}</div>
      ${files}

      <h3>История</h3>
      ${historyHtml(t)}
      ${t.actions.includes("comment") ? commentFormHtml() : ""}
    </div>`;
}

// Таблица «заявитель, исполнитель, группа, сроки...»
function ticketFactsHtml(t) {
  const responseNote = t.first_response_at ? ' <span class="small muted">(получен)</span>' : "";
  const waitingNote = t.status === "WAITING" ? ' <span class="small muted">(ожидание заявителя)</span>' : "";

  return `
    <dl class="ticket-facts">
      <div><dt>Заявитель</dt><dd>${esc(t.requester)}</dd></div>
      <div><dt>Исполнитель</dt><dd>${esc(t.assignee || "не назначен")}</dd></div>
      <div><dt>Группа</dt><dd>${esc(t.group || "—")}</dd></div>
      <div><dt>Категория</dt><dd>${esc(t.category)}</dd></div>
      <div>
        <dt>Влияние / срочность</dt>
        <dd>${label("impact", t.impact)}<br><span class="small muted">${label("urgency", t.urgency)}</span></dd>
      </div>
      <div><dt>Первый отклик до</dt><dd>${formatDate(t.response_due_at)}${responseNote}</dd></div>
      <div>
        <dt>Срок решения</dt>
        <dd class="${t.overdue ? "overdue" : ""}">${formatDate(t.resolve_due_at)}${waitingNote}</dd>
      </div>
    </dl>`;
}

// Шкала: сколько процентов срока решения уже прошло
function deadlineHtml(t) {
  if (!OPEN_STATUSES.includes(t.status)) return "";

  if (t.status === "WAITING") {
    return '<div class="deadline small muted">Срок решения приостановлен: ждём ответа заявителя</div>';
  }

  // Время, когда заявка ждала ответа заявителя, в срок не входит
  const waitedMs = t.waited_seconds * 1000;
  const startMs = new Date(t.sla_start_at).getTime();
  const totalMs = new Date(t.resolve_due_at).getTime() - startMs - waitedMs;
  const passedMs = Date.now() - startMs - waitedMs;

  const percent = Math.max(0, Math.round((passedMs / totalMs) * 100));
  const shownPercent = Math.min(percent, 100);

  let colorClass = "deadline-ok";
  if (percent >= 100) colorClass = "deadline-expired";
  else if (percent >= 80) colorClass = "deadline-warning";

  const expiredText = percent >= 100 ? " — срок нарушен" : "";

  return `
    <div class="deadline">
      <div class="small ${percent >= 100 ? "overdue" : "muted"}">Срок решения: израсходовано ${percent}%${expiredText}</div>
      <div class="deadline-bar" role="progressbar" aria-label="Расход срока решения"
           aria-valuemin="0" aria-valuemax="100" aria-valuenow="${shownPercent}">
        <div class="${colorClass}" style="width: ${shownPercent}%"></div>
      </div>
    </div>`;
}

function actionButtonsHtml(t) {
  return ACTION_BUTTONS
    .filter(([action]) => t.actions.includes(action))
    .map(([action, text, style]) => `<button class="btn ${style}" data-action="${action}">${text}</button>`)
    .join("");
}

function historyHtml(t) {
  const items = t.timeline.map((event) => `
    <li class="${HISTORY_KIND_CLASS[event.kind] || ""}">
      <div class="small muted">${formatDate(event.at)} · ${event.kind} · ${esc(event.author)}</div>
      <div class="history-text">${esc(event.text)}</div>
    </li>`);
  return `<ul class="history">${items.join("")}</ul>`;
}

function commentFormHtml() {
  return `
    <label for="comment-text">Комментарий</label>
    <textarea id="comment-text" style="min-height: 64px"></textarea>
    <div class="comment-form-row">
      <button class="btn btn-main" id="send-comment">Отправить</button>
      <input type="file" id="comment-file" accept="${info.exts.join(",")}">
      <span class="small muted">до ${info.max_mb} МБ</span>
    </div>
    <div class="form-error" role="alert" id="comment-error"></div>`;
}

async function sendComment(ticket) {
  const text = $("#comment-text").value.trim();
  const file = $("#comment-file").files[0];

  try {
    if (!text && !file) {
      throw new Error("Введите комментарий или выберите файл");
    }
    if (file) {
      await api("POST", `/tickets/${ticket.id}/attach`, { file: await readFile(file) });
    }
    if (text) {
      await api("POST", `/tickets/${ticket.id}/comment`, { text: text });
    }
    toast("Комментарий отправлен");
    openTicket(ticket.id);
    if ($("#ticket-list")) loadTickets();
  } catch (error) {
    $("#comment-error").textContent = error.message;
  }
}

// ---------- Действия над заявкой ----------

// Поле «текст» для окон с формой
function textField(labelText) {
  return { key: "text", label: labelText, type: "text" };
}

// Описание окон: [заголовок, поля, текст кнопки]
function dialogFor(action, t) {
  const groups = info.groups.map((g) => [g.id, g.name]);

  if (action === "request_info") {
    return ["Запрос сведений у заявителя", [textField("Что нужно уточнить")], "Отправить"];
  }
  if (action === "resolve") {
    return ["Описание решения", [textField("Что было сделано (не менее 10 символов)")], "Решить"];
  }
  if (action === "reopen") {
    return ["Проблема осталась", [textField("Что именно не работает")], "Открыть повторно"];
  }
  if (action === "escalate") {
    const otherGroups = groups
      .filter(([id]) => id !== t.group_id)
      .map(([id, name]) => [id, "В группу: " + name]);
    return [
      "Эскалация",
      [
        { key: "group_id", label: "Кому передать", type: "select", options: [["", "Руководителю"], ...otherGroups] },
        textField("Причина"),
      ],
      "Эскалировать",
    ];
  }
  if (action === "assign") {
    const staff = info.staff.map((s) => [s.id, s.name, s.group_id ?? ""]);
    return [
      "Назначение",
      [
        { key: "group_id", label: "Группа", type: "select", value: t.group_id, options: groups },
        { key: "assignee_id", label: "Исполнитель", type: "select", options: [["", "Автоматически — по нагрузке"], ...staff] },
        textField("Причина (необязательно)"),
      ],
      "Назначить",
    ];
  }
  if (action === "reclassify") {
    const categories = info.categories.map((c) => [c.id, c.name]);
    return [
      "Приоритет и категория",
      [
        { key: "category_id", label: "Категория", type: "select", value: t.category_id, options: categories },
        { key: "impact", label: "Влияние", type: "select", value: t.impact, options: Object.entries(info.labels.impact) },
        { key: "urgency", label: "Срочность", type: "select", value: t.urgency, options: Object.entries(info.labels.urgency) },
        { key: "priority", label: "Приоритет", type: "select", options: [["", "Рассчитать по матрице"], ...Object.entries(info.labels.priority)] },
        textField("Причина изменения"),
      ],
      "Сохранить",
    ];
  }
  if (action === "rate") {
    return [
      "Оценка поддержки",
      [{ key: "rating", label: "Оценка", type: "stars" }, textField("Комментарий (необязательно)")],
      "Отправить",
    ];
  }
}

async function doAction(ticket, action) {
  // Отправляет действие на сервер и обновляет экран
  const send = async (body) => {
    await api("POST", `/tickets/${ticket.id}/${action}`, body || {});
    toast(ACTION_DONE_TEXT[action] || "Готово");
    openTicket(ticket.id);
    if ($("#ticket-list")) loadTickets();
    reloadInfo();
  };

  // Эти два действия не требуют ввода — выполняем сразу
  if (action === "start" || action === "confirm") {
    try {
      await send();
    } catch (error) {
      toast(error.message, true);
    }
    return;
  }

  const [title, fields, buttonText] = dialogFor(action, ticket);
  openDialog(title, fields, buttonText, send);
}
