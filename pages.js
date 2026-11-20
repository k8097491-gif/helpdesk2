// ============================================================
// pages.js — остальные страницы:
// новая заявка, аналитика, база знаний, уведомления, администрирование
// ============================================================

// Выпадающий список из объекта {значение: подпись}
function selectHtml(id, options, selectedKey) {
  const items = Object.entries(options)
    .map(([key, text]) => `<option value="${key}" ${key === selectedKey ? "selected" : ""}>${text}</option>`)
    .join("");
  return `<select id="${id}">${items}</select>`;
}

// ---------- Новая заявка ----------

function showNewTicket() {
  const categories = info.categories
    .map((c) => `<option value="${c.id}">${esc(c.name)}</option>`)
    .join("");

  $("#content").innerHTML = `
    <div class="page-head">
      <div>
        <h2>Новая заявка</h2>
        <p class="page-description">Опишите проблему — обращение попадёт в нужную команду поддержки.</p>
      </div>
    </div>
    <div class="two-columns new-ticket-layout">
      <form class="card" id="new-ticket-form">
        <div class="form-section-heading">
          <h3>Что случилось?</h3>
          <span class="small muted">Укажите тему, описание и категорию</span>
        </div>
        <label for="new-title">Тема</label>
        <input id="new-title" maxlength="200" required placeholder="Коротко: что не работает">

        <label for="new-description">Описание</label>
        <textarea id="new-description" required placeholder="Что произошло, где и когда, что уже пробовали"></textarea>

        <label for="new-category">Категория</label>
        <select id="new-category">${categories}</select>

        <label for="new-impact">Кого затрагивает</label>
        ${selectHtml("new-impact", info.labels.impact, "LOW")}

        <label for="new-urgency">Насколько срочно</label>
        ${selectHtml("new-urgency", info.labels.urgency, "MEDIUM")}

        <label for="new-files">Файлы (необязательно)</label>
        <input type="file" id="new-files" multiple accept="${info.exts.join(",")}">

        <div class="form-error" role="alert" id="new-error"></div>
        <button class="btn btn-main">Отправить заявку</button>
      </form>

      <div id="suggestions">
        ${emptyStateHtml("Подсказки появятся здесь", "Заполните тему и описание — возможно, в базе знаний уже есть решение.")}
      </div>
    </div>`;

  // Подсказки из базы знаний: ищем через 350 мс после того, как человек перестал печатать
  let typingTimer;
  const loadSuggestions = () => {
    clearTimeout(typingTimer);
    typingTimer = setTimeout(async () => {
      if (!$("#new-title")) return;   // страницу уже закрыли

      const query = new URLSearchParams({
        title: $("#new-title").value,
        description: $("#new-description").value,
        category_id: $("#new-category").value,
      });
      let articles = [];
      try {
        articles = await api("GET", "/kb/suggest?" + query);
      } catch (error) {
        // подсказки — необязательная часть, ошибку не показываем
      }

      if (articles.length > 0 && $("#suggestions")) {
        const items = articles
          .map((a) => `<div class="info-box"><b>${esc(a.title)}</b><pre>${esc(a.body)}</pre></div>`)
          .join("");
        $("#suggestions").innerHTML = `<div class="card"><h3>Возможно, поможет</h3>${items}</div>`;
      }
    }, 350);
  };
  $("#new-title").oninput = loadSuggestions;
  $("#new-description").oninput = loadSuggestions;
  $("#new-category").oninput = loadSuggestions;

  $("#new-ticket-form").onsubmit = async (event) => {
    event.preventDefault();
    try {
      const files = await Promise.all([...$("#new-files").files].map(readFile));
      const ticket = await api("POST", "/tickets", {
        title: $("#new-title").value,
        description: $("#new-description").value,
        category_id: $("#new-category").value,
        impact: $("#new-impact").value,
        urgency: $("#new-urgency").value,
        files: files,
      });
      await showPage("mine");
      toast("Заявка " + ticket.number + " создана");
      openTicket(ticket.id);
    } catch (error) {
      $("#new-error").textContent = error.message;
    }
  };
}

// ---------- Аналитика ----------

// Строки таблицы из объекта {название: число}
function tableRows(data, emptyText, translate) {
  const rows = Object.entries(data).map(([name, count]) => {
    const shownName = translate ? translate(name) : esc(name);
    return `<tr><td>${shownName}</td><td>${count}</td></tr>`;
  });
  return rows.join("") || `<tr><td class="muted">${emptyText}</td></tr>`;
}

async function showReport() {
  const report = await api("GET", "/report");
  const quality = report.quality;
  const maxLoad = Math.max(1, ...report.workload.map((w) => w.n));
  const statuses = Object.entries(report.queue.by_status);
  const maxStatusCount = Math.max(1, ...statuses.map(([, count]) => Number(count) || 0));
  const percent = (value) => (value == null ? "—" : value + "%");

  const statusChart = statuses.length
    ? statuses.map(([key, count]) => `
        <div class="chart-row" role="listitem">
          <div class="chart-label"><span>${esc(label("status", key))}</span><b>${Number(count) || 0}</b></div>
          <div class="chart-track" aria-hidden="true"><span class="chart-bar status-${esc(key)}" style="width: ${((Number(count) || 0) / maxStatusCount) * 100}%"></span></div>
        </div>`).join("")
    : `<div role="listitem">${emptyStateHtml("Заявок пока нет")}</div>`;

  const workloadRows = report.workload
    .map((w) => `
      <div class="chart-row" role="listitem">
        <div class="chart-label"><span>${esc(w.name)}</span><b>${Number(w.n) || 0}</b></div>
        <div class="chart-track" aria-hidden="true"><span class="chart-bar workload-bar" style="width: ${(Math.max(0, Number(w.n) || 0) / maxLoad) * 100}%"></span></div>
      </div>`)
    .join("");

  const overdueBlock = report.overdue.length
    ? `<h3 class="section-gap">Просроченные заявки</h3>
       <div class="ticket-list" style="margin-top: 8px">${report.overdue.map(ticketRowHtml).join("")}</div>`
    : "";

  const canRunChecks = ["MANAGER", "ADMIN"].includes(user.role);
  const checkButton = canRunChecks
    ? `<p><button class="btn" id="run-checks">Проверить сроки сейчас</button>
       <span class="small muted">Сервер делает это сам раз в минуту.</span></p>`
    : "";

  $("#content").innerHTML = `
    <div class="page-head"><h2>Аналитика</h2></div>

    <div class="stat-grid">
      <div class="stat"><b>${report.queue.unassigned}</b>без исполнителя</div>
      <div class="stat"><b class="${report.overdue.length ? "overdue" : ""}">${report.overdue.length}</b>просрочено</div>
      <div class="stat"><b>${quality.average_rating ?? "—"}</b>средняя оценка (${quality.rated})</div>
      <div class="stat"><b>${percent(quality.in_time_percent)}</b>решено в срок</div>
      <div class="stat"><b>${quality.average_resolution_hours ?? "—"}</b>часов до решения</div>
      <div class="stat"><b>${percent(quality.reopened_percent)}</b>открывались повторно</div>
    </div>

    <div class="two-columns section-gap">
      <div class="card table-card">
        <h3>Заявки по статусам</h3>
        <div class="chart-list" role="list" aria-label="Количество заявок по статусам">${statusChart}</div>
        <h3 style="margin-top: 16px">Открытые по группам</h3>
        <table>${tableRows(report.queue.by_group, "—")}</table>
      </div>
      <div class="card table-card">
        <h3>Нагрузка исполнителей</h3>
        <div class="chart-list" role="list" aria-label="Нагрузка исполнителей">${workloadRows || `<div role="listitem">${emptyStateHtml("Нет данных по нагрузке")}</div>`}</div>
        <h3 style="margin-top: 16px">Оценка по группам</h3>
        <table>${tableRows(quality.average_rating_by_group, "Оценок пока нет")}</table>
      </div>
    </div>

    ${overdueBlock}
    ${checkButton}`;

  // Клик по просроченной заявке открывает её в общем списке
  $$(".ticket-row").forEach((row) => {
    row.onclick = async () => {
      await showPage("all");
      openTicket(Number(row.dataset.id));
    };
  });

  if ($("#run-checks")) {
    $("#run-checks").onclick = async () => {
      const result = await api("POST", "/maintenance", {});
      toast(`Предупреждений: ${result.alerts}. Автозакрыто: ${result.closed}. Повторно отправлено: ${result.resent}.`);
      showReport();
    };
  }
}

// ---------- База знаний ----------

async function showKnowledgeBase() {
  // Добавлять статьи могут сотрудники поддержки
  const canAdd = ["SPECIALIST", "MANAGER", "ADMIN"].includes(user.role);

  const categories = info.categories
    .map((c) => `<option value="${c.id}">${esc(c.name)}</option>`)
    .join("");

  const addForm = canAdd
    ? `<form class="card" id="article-form">
         <h3>Добавить статью</h3>
         <label for="article-title">Заголовок</label>
         <input id="article-title" required>
         <label for="article-body">Текст</label>
         <textarea id="article-body" required></textarea>
         <label for="article-keywords">Ключевые слова</label>
         <input id="article-keywords">
         <label for="article-category">Категория</label>
         <select id="article-category"><option value="">Любая</option>${categories}</select>
         <div class="form-error" role="alert" id="article-error"></div>
         <button class="btn btn-main">Опубликовать</button>
       </form>`
    : "";

  $("#content").innerHTML = `
    <div class="page-head">
      <div>
        <h2>База знаний</h2>
        <p class="page-description">Краткие инструкции по типичным вопросам IT-поддержки.</p>
      </div>
      <input class="page-search" id="kb-search" type="search" aria-label="Поиск по базе знаний" placeholder="Принтер, Wi-Fi, пароль">
    </div>
    <div class="two-columns knowledge-layout">
      <div id="articles" class="ticket-list"></div>
      ${addForm}
    </div>`;

  async function loadArticles() {
    const query = $("#kb-search").value;
    const articles = await api("GET", "/kb?q=" + encodeURIComponent(query));

    if (articles.length === 0) {
      const text = query
        ? "Ничего не найдено. Создайте заявку — специалисты помогут."
        : "Введите запрос, чтобы найти инструкцию.";
      $("#articles").innerHTML = emptyStateHtml(text);
      return;
    }
    $("#articles").innerHTML = articles
      .map((a) => `<div class="card"><h3>${esc(a.title)}</h3><p style="white-space: pre-wrap; margin: 6px 0 0">${esc(a.body)}</p></div>`)
      .join("");
  }

  let typingTimer;
  $("#kb-search").oninput = () => {
    clearTimeout(typingTimer);
    typingTimer = setTimeout(loadArticles, 250);
  };
  loadArticles();

  if (canAdd) {
    $("#article-form").onsubmit = async (event) => {
      event.preventDefault();
      try {
        await api("POST", "/kb", {
          title: $("#article-title").value,
          body: $("#article-body").value,
          keywords: $("#article-keywords").value,
          category_id: $("#article-category").value,
        });
        showKnowledgeBase();
      } catch (error) {
        $("#article-error").textContent = error.message;
      }
    };
  }
}

// ---------- Уведомления ----------

async function showInbox() {
  const messages = await api("GET", "/inbox");

  const items = messages.map((m) => `
    <div class="card">
      <div class="small muted">${formatDate(m.created_at)} · ${m.delivered ? "доставлено" : "в очереди на отправку"}</div>
      <b>${esc(m.subject)}</b>
      <div style="white-space: pre-wrap">${esc(m.body)}</div>
    </div>`);

  $("#content").innerHTML = `
    <div class="page-head">
      <div>
        <h2>Уведомления</h2>
        <p class="page-description">Обновления по вашим обращениям и системным событиям.</p>
      </div>
      <span class="muted small">Канал доставки: ${label("channel", user.notify_channel)}</span>
    </div>
    <div class="ticket-list">${items.join("") || emptyStateHtml("Уведомлений пока нет")}</div>`;
}

// ---------- Администрирование ----------

async function showAdmin() {
  const data = await api("GET", "/admin");
  const activeGroups = data.groups.filter((g) => g.active);

  const groupName = (id) => {
    const group = data.groups.find((g) => g.id === id);
    return group ? group.name : "—";
  };

  // <option> для списка групп; emptyText — подпись пустого варианта
  const groupOptions = (selectedId, emptyText) => {
    const items = activeGroups
      .map((g) => `<option value="${g.id}" ${g.id === selectedId ? "selected" : ""}>${esc(g.name)}</option>`)
      .join("");
    return `<option value="">${emptyText}</option>${items}`;
  };

  const roleOptions = Object.entries(info.labels.role)
    .map(([key, text]) => `<option value="${key}" ${key === "REQUESTER" ? "selected" : ""}>${text}</option>`)
    .join("");

  // --- таблица пользователей ---
  const userRows = data.users.map((u) => {
    const disabledMark = u.active ? "" : ' <span class="muted">(отключён)</span>';
    const buttons = u.active && u.id !== user.id
      ? `<button class="btn btn-small" data-change-role="${u.id}">Роль</button>
         <button class="btn btn-small btn-danger" data-deactivate="${u.id}">Отключить</button>`
      : "";
    return `
      <tr>
        <td>${esc(u.login)}</td>
        <td>${esc(u.full_name)}${disabledMark}</td>
        <td>${label("role", u.role)}</td>
        <td>${esc(groupName(u.group_id))}</td>
        <td>${buttons}</td>
      </tr>`;
  }).join("");

  // --- маршрутизация: какая категория в какую группу попадает ---
  const routingRows = data.categories.map((c) => `
    <tr>
      <td>${esc(c.name)}</td>
      <td><select data-routing="${c.id}">${groupOptions(c.group_id, "Вручную (диспетчер)")}</select></td>
    </tr>`).join("");

  // --- сроки по приоритетам ---
  const slaRows = data.sla.map((s) => `
    <tr>
      <td>${label("priority", s.priority)}</td>
      <td><input type="number" min="1" data-sla="${s.priority}" data-field="response" value="${s.response_minutes}"></td>
      <td><input type="number" min="1" data-sla="${s.priority}" data-field="resolve" value="${s.resolve_minutes}"></td>
      <td><button class="btn btn-small" data-save-sla="${s.priority}">Сохранить</button></td>
    </tr>`).join("");

  // --- настройки ---
  const settingNames = [
    ["auto_close_days", "Автозакрытие решённых, дней"],
    ["risk_ratio", "Предупреждать о риске при доле срока (0–1)"],
    ["auto_assign", "Автоназначение по правилам (1 — да, 0 — нет)"],
  ];
  const settingRows = settingNames.map(([key, name]) => `
    <tr>
      <td>${name}</td>
      <td style="width: 90px"><input data-setting="${key}" value="${data.settings[key]}"></td>
    </tr>`).join("");

  $("#content").innerHTML = `
    <div class="page-head"><h2>Администрирование</h2></div>

    <div class="card table-card">
      <h3>Пользователи</h3>
      <table>
        <tr><th>Логин</th><th>Имя</th><th>Роль</th><th>Группа</th><th></th></tr>
        ${userRows}
      </table>
      <form id="user-form" class="user-form">
        <input id="user-login" placeholder="Логин" required>
        <input id="user-name" placeholder="Имя" required>
        <input id="user-email" type="email" placeholder="E-mail" required>
        <select id="user-role">${roleOptions}</select>
        <select id="user-group">${groupOptions(null, "Без группы")}</select>
        <button class="btn btn-main">Создать</button>
      </form>
      <div class="form-error" role="alert" id="admin-error"></div>
    </div>

    <div class="two-columns section-gap">
      <div class="card table-card">
        <h3>Категории и маршрутизация</h3>
        <table>${routingRows}</table>
        <form id="category-form" class="inline-form">
          <input id="category-name" placeholder="Новая категория" required>
          <select id="category-group">${groupOptions(null, "Вручную")}</select>
          <button class="btn btn-main">Добавить</button>
        </form>
        <form id="group-form" class="inline-form">
          <input id="group-name" placeholder="Новая группа поддержки" required>
          <button class="btn btn-main">Добавить</button>
        </form>
      </div>

      <div class="card table-card">
        <h3>Сроки по приоритетам (минуты)</h3>
        <table>
          <tr><th></th><th>Отклик</th><th>Решение</th><th></th></tr>
          ${slaRows}
        </table>
        <h3 style="margin-top: 16px">Настройки</h3>
        <table>${settingRows}</table>
        <button class="btn btn-small" id="save-settings" style="margin-top: 8px">Сохранить настройки</button>
      </div>
    </div>`;

  // Отправляет изменение на сервер и перерисовывает страницу
  async function adminAction(operation, body) {
    try {
      await api("POST", "/admin/" + operation, body);
      toast("Сохранено");
      showAdmin();
    } catch (error) {
      $("#admin-error").textContent = error.message;
      window.scrollTo(0, 0);
    }
  }

  $("#user-form").onsubmit = (event) => {
    event.preventDefault();
    adminAction("user", {
      login: $("#user-login").value,
      full_name: $("#user-name").value,
      email: $("#user-email").value,
      role: $("#user-role").value,
      group_id: $("#user-group").value,
    });
  };

  $("#category-form").onsubmit = (event) => {
    event.preventDefault();
    adminAction("category", { name: $("#category-name").value, group_id: $("#category-group").value });
  };

  $("#group-form").onsubmit = (event) => {
    event.preventDefault();
    adminAction("group", { name: $("#group-name").value });
  };

  // Смена группы у категории — сразу при выборе
  $$("[data-routing]").forEach((select) => {
    select.onchange = () => adminAction("routing", { category_id: select.dataset.routing, group_id: select.value });
  });

  $$("[data-deactivate]").forEach((button) => {
    button.onclick = () => {
      if (confirm("Отключить пользователя?")) {
        adminAction("deactivate", { user_id: button.dataset.deactivate });
      }
    };
  });

  $$("[data-change-role]").forEach((button) => {
    button.onclick = () => {
      const fields = [
        { key: "role", label: "Роль", type: "select", options: Object.entries(info.labels.role) },
        { key: "group_id", label: "Группа", type: "select", options: [["", "Не менять"], ...activeGroups.map((g) => [g.id, g.name])] },
      ];
      openDialog("Роль пользователя", fields, "Сохранить", async (values) => {
        await api("POST", "/admin/role", { user_id: button.dataset.changeRole, role: values.role, group_id: values.group_id });
        showAdmin();
      });
    };
  });

  $$("[data-save-sla]").forEach((button) => {
    button.onclick = () => {
      const priority = button.dataset.saveSla;
      const value = (field) => document.querySelector(`[data-sla="${priority}"][data-field="${field}"]`).value;
      adminAction("sla", { priority: priority, response: value("response"), resolve: value("resolve") });
    };
  });

  $("#save-settings").onclick = async () => {
    try {
      for (const input of $$("[data-setting]")) {
        await api("POST", "/admin/setting", { key: input.dataset.setting, value: input.value });
      }
      showAdmin();
    } catch (error) {
      $("#admin-error").textContent = error.message;
      window.scrollTo(0, 0);
    }
  };
}
