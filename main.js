// ============================================================
// main.js — вход, меню, переключение страниц, запуск
// ============================================================

// Логины для быстрого входа на демо-странице
const DEMO_LOGINS = [
  ["student", "Студент"],
  ["teacher", "Преподаватель"],
  ["net1", "Специалист"],
  ["dispatcher", "Диспетчер"],
  ["manager", "Руководитель"],
  ["admin", "Админ"],
  ["student2", "Новый пользователь"],
];

const NAV_ICONS = {
  queue: '<path d="M4 5h16M4 12h16M4 19h16"/><path d="M8 5v14"/>',
  unassigned: '<circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/>',
  all: '<path d="M4 6h16M4 12h16M4 18h16"/><path d="M7 6v12"/>',
  mine: '<circle cx="12" cy="8" r="3"/><path d="M5 20c0-3.3 2.7-6 7-6s7 2.7 7 6"/>',
  new: '<path d="M12 5v14M5 12h14"/>',
  report: '<path d="M4 19V5M4 19h16"/><path d="m7 15 4-4 3 2 5-6"/>',
  kb: '<path d="M5 4h12a2 2 0 0 1 2 2v14H7a2 2 0 0 1-2-2z"/><path d="M5 17a2 2 0 0 1 2-2h12M9 8h6"/>',
  inbox: '<path d="M4 5h16v14H4z"/><path d="M4 12h4l2 3h4l2-3h4"/>',
  admin: '<circle cx="12" cy="12" r="3"/><path d="m19.4 15 .1.1 1.4 1.1-1.4 2.4-1.7-.7a8 8 0 0 1-1.7 1l-.3 1.8h-2.8l-.3-1.8a8 8 0 0 1-1.7-1l-1.7.7-1.4-2.4L7.3 15a8 8 0 0 1 0-2l-1.4-1.1 1.4-2.4 1.7.7a8 8 0 0 1 1.7-1l.3-1.8h2.8l.3 1.8a8 8 0 0 1 1.7 1l1.7-.7 1.4 2.4-1.4 1.1a8 8 0 0 1-.1 2Z"/>',
};

function initializeTheme() {
  const savedTheme = localStorage.getItem("helpdesk-theme");
  const systemTheme = matchMedia("(prefers-color-scheme: dark)");
  const hasSavedTheme = savedTheme === "dark" || savedTheme === "light";
  document.body.dataset.theme = hasSavedTheme ? savedTheme : (systemTheme.matches ? "dark" : "light");
  if (!hasSavedTheme) {
    const followSystemTheme = (event) => {
      if (localStorage.getItem("helpdesk-theme")) return;
      document.body.dataset.theme = event.matches ? "dark" : "light";
      updateThemeButton();
    };
    if (systemTheme.addEventListener) systemTheme.addEventListener("change", followSystemTheme);
    else systemTheme.addListener(followSystemTheme);
  }
}

function updateThemeButton() {
  const button = $("#theme-toggle");
  if (!button) return;
  const isDark = document.body.dataset.theme === "dark";
  button.setAttribute("aria-label", isDark ? "Включить светлую тему" : "Включить тёмную тему");
  button.title = isDark ? "Светлая тема" : "Тёмная тема";
  button.innerHTML = isDark
    ? '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M4.93 4.93l1.42 1.42m11.3 11.3 1.42 1.42M2 12h2m16 0h2M4.93 19.07l1.42-1.42m11.3-11.3 1.42-1.42"/></svg>'
    : '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20.5 15.5A8.5 8.5 0 0 1 8.5 3.5 8.5 8.5 0 1 0 20.5 15.5Z"/></svg>';
}

function toggleTheme() {
  document.body.dataset.theme = document.body.dataset.theme === "dark" ? "light" : "dark";
  localStorage.setItem("helpdesk-theme", document.body.dataset.theme);
  updateThemeButton();
}

initializeTheme();

// ---------- Страница входа ----------

function showLogin(errorText) {
  clearInterval(refreshTimer);
  delete document.body.dataset.role;
  document.title = "Вход — Helpdesk";

  const demoButtons = DEMO_LOGINS
    .map(([login, name]) => `<button class="btn" data-login="${login}">${name}</button>`)
    .join("");

  $("#root").innerHTML = `
    <div class="login-shell">
      <section class="login-brand" aria-label="О системе">
        <div class="brand-mark"><span class="brand-symbol">H</span> Helpdesk</div>
        <div class="brand-copy">
          <h1>Поддержка,<br>на которой можно сосредоточиться.</h1>
          <p>Единое рабочее пространство для обращения и решения IT-задач университета.</p>
        </div>
        <div class="brand-footer">IT Service Desk · University</div>
      </section>
      <main class="login-main">
        <div class="login-box">
          <div class="login-heading">
            <h2>Вход в систему</h2>
            <button class="theme-toggle login-theme-toggle" id="theme-toggle" type="button"></button>
          </div>
          <p>Используйте учётную запись университета</p>
          <form id="login-form">
            <label for="login">Логин</label>
            <input id="login" autocomplete="username" autofocus required placeholder="Введите логин">
            <div class="form-error" role="alert" id="login-error">${esc(errorText)}</div>
            <button class="btn btn-main login-submit">Войти</button>
          </form>
          <p class="small muted" style="margin: 20px 0 0">Демо-доступ:</p>
          <div class="demo-logins">${demoButtons}</div>
        </div>
      </main>
    </div>`;

  updateThemeButton();
  $("#theme-toggle").onclick = toggleTheme;

  async function logIn(login) {
    try {
      const data = await api("POST", "/login", { login: login });
      await startApp(data);
    } catch (error) {
      $("#login-error").textContent = error.message;
    }
  }

  $("#login-form").onsubmit = (event) => {
    event.preventDefault();
    logIn($("#login").value);
  };
  $$(".demo-logins button").forEach((button) => {
    button.onclick = () => logIn(button.dataset.login);
  });
}

// ---------- Запуск приложения после входа ----------

async function startApp(data) {
  info = data;
  user = data.user;
  selectedId = null;
  document.body.dataset.role = user.role;   // по этому атрибуту CSS красит интерфейс в цвет роли

  await showPage(menuItems()[0][0]);

  // Раз в 30 секунд обновляем список заявок, если пользователь ничего не вводит
  clearInterval(refreshTimer);
  refreshTimer = setInterval(refreshListInBackground, 30000);
}

function refreshListInBackground() {
  const listPages = ["queue", "unassigned", "all", "mine"];
  if (!user || !listPages.includes(currentPage)) return;
  if (document.querySelector("dialog[open]")) return;
  if (document.activeElement.matches("textarea, input, select")) return;

  loadTickets().catch(() => {});
}

// ---------- Меню ----------

// Какие пункты меню видит пользователь: [код страницы, название]
function menuItems() {
  const role = user.role;
  const items = [];

  if (role === "SPECIALIST" || role === "MANAGER") {
    items.push(["queue", "Моя очередь"]);
  }
  if (role === "DISPATCHER" || role === "MANAGER") {
    items.push(["unassigned", "Без исполнителя"]);
    items.push(["all", "Все заявки"]);
  }
  if (role !== "ADMIN") {
    items.push(["mine", "Мои обращения"]);
    items.push(["new", "Новая заявка"]);
  }
  if (role === "DISPATCHER" || role === "MANAGER" || role === "ADMIN") {
    items.push(["report", "Аналитика"]);
  }
  items.push(["kb", "База знаний"]);
  items.push(["inbox", "Уведомления"]);
  if (role === "ADMIN") {
    items.push(["admin", "Администрирование"]);
  }
  return items;
}

// Рисует меню слева и пустую область для содержимого
function drawLayout() {
  const links = menuItems()
    .map(([page, name]) => {
      const active = page === currentPage;
      return `<button class="menu-link ${active ? "active" : ""}" data-page="${page}" aria-current="${active ? "page" : "false"}">
        <svg viewBox="0 0 24 24" aria-hidden="true">${NAV_ICONS[page]}</svg><span>${name}</span>
      </button>`;
    })
    .join("");
  const initials = user.full_name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toUpperCase();

  // Плашка появляется, если есть закрытые заявки без оценки
  let notice = "";
  if (info.surveys.length > 0) {
    notice = `<div class="notice">Закрыто заявок без оценки: ${info.surveys.length}. <button id="rate-link">Оценить</button></div>`;
  }

  $("#root").innerHTML = `
    <div id="app">
      <nav class="menu" aria-label="Основное меню">
        <div class="menu-brand"><span class="brand-symbol">H</span><span>Helpdesk</span></div>
        <div class="menu-role">${label("role", user.role)}</div>
        <div class="menu-links">${links}</div>
        <div class="profile-card">
          <div class="profile-row">
            <div class="avatar" title="${esc(user.full_name)}">${esc(initials)}</div>
            <div class="profile-meta">
              <b>${esc(user.full_name)}</b>
              <span>${esc(label("role", user.role))}</span>
            </div>
          </div>
          <div class="profile-actions">
            <button class="theme-toggle" id="theme-toggle" type="button"></button>
            <button id="logout">Выйти из системы</button>
          </div>
        </div>
      </nav>
      <div class="app-main">
        <header class="topbar">
          <div class="topbar-title">IT-поддержка университета</div>
          <div class="topbar-actions">
            <button class="icon-button" id="inbox-button" title="Уведомления" aria-label="Уведомления">
              <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true">
                <path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9Z"/>
                <path d="M10 21h4"/>
              </svg>
            </button>
          </div>
        </header>
        <main>
          ${notice}
          <div id="content" tabindex="-1"></div>
        </main>
      </div>
    </div>`;

  $$(".menu-link").forEach((button) => {
    button.onclick = () => showPage(button.dataset.page);
  });
  updateThemeButton();
  $("#theme-toggle").onclick = toggleTheme;

  $("#logout").onclick = async () => {
    await api("POST", "/logout", {});
    user = null;
    showLogin();
  };
  if ($("#inbox-button")) {
    $("#inbox-button").onclick = () => showPage("inbox");
  }

  if ($("#rate-link")) {
    $("#rate-link").onclick = async () => {
      await showPage("mine");
      openTicket(info.surveys[0].id);
    };
  }
}

// ---------- Переключение страниц ----------

// Какая функция рисует какую страницу
const PAGES = {
  queue: showTicketList,
  unassigned: showTicketList,
  all: showTicketList,
  mine: showTicketList,
  new: showNewTicket,
  report: showReport,
  kb: showKnowledgeBase,
  inbox: showInbox,
  admin: showAdmin,
};

async function showPage(page) {
  currentPage = page;
  selectedId = null;
  searchText = "";

  await reloadInfo();   // свежие данные: категории, сотрудники, заявки без оценки
  if (!user) return;    // сессия закончилась, уже показана страница входа
  drawLayout();

  const pageName = menuItems().find((item) => item[0] === page);
  document.title = (pageName ? pageName[1] : "Helpdesk") + " — Helpdesk";

  // Пока страница грузится, показываем серые полоски
  const content = $("#content");
  content.innerHTML = loadingHtml();
  content.setAttribute("aria-busy", "true");

  try {
    await PAGES[page]();
  } catch (error) {
    content.innerHTML = errorHtml(error.message);
    $(".retry-button").onclick = () => showPage(page);
  }
  content.removeAttribute("aria-busy");

  // Переносим фокус на заголовок, чтобы экранный диктор прочитал, где мы
  const heading = $("#content h2");
  if (heading) {
    content.classList.remove("page-enter");
    void content.offsetWidth;
    content.classList.add("page-enter");
    heading.tabIndex = -1;
    heading.focus({ preventScroll: true });
  }
}

// ---------- Старт ----------

async function start() {
  try {
    await startApp(await api("GET", "/me"));
  } catch (error) {
    // Не вошли — показываем форму входа. Текст ошибки нужен только если нет связи с сервером.
    const connectionProblem = /сервер|файл/.test(error.message);
    showLogin(connectionProblem ? error.message : "");
  }
}

start();
