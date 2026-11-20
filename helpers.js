// ============================================================
// helpers.js — общие переменные и вспомогательные функции
// ============================================================

// Короткие названия для поиска элементов на странице
function $(selector) {
  return document.querySelector(selector);
}
function $$(selector) {
  return document.querySelectorAll(selector);
}

// ---------- Данные, которые нужны всем страницам ----------

let user = null;          // вошедший пользователь (null, пока не вошли)
let info = {};            // ответ сервера /api/me: подписи, категории, группы, сотрудники...
let currentPage = null;   // какая страница меню открыта сейчас
let refreshTimer = null;  // таймер, который раз в 30 секунд обновляет список заявок

// Статусы, при которых заявка ещё не закрыта и не решена
const OPEN_STATUSES = ["NEW", "ASSIGNED", "IN_PROGRESS", "WAITING", "ESCALATED", "REOPENED"];

// Подписи (русские названия статусов, ролей и т.д.) приходят с сервера
function label(group, key) {
  return info.labels[group][key];
}

// ---------- Работа с текстом и датами ----------

// Защита от вставки HTML: «<script>» в тексте заявки станет безобидным текстом
function esc(text) {
  const replacements = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  return String(text ?? "").replace(/[&<>"']/g, (char) => replacements[char]);
}

function formatDate(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" });
}

// ---------- Запросы к серверу ----------

// Отправляет запрос на /api/... и возвращает ответ.
// Если что-то пошло не так — бросает ошибку с понятным текстом.
async function api(method, path, body) {
  if (location.protocol === "file:") {
    throw new Error("Страница открыта как файл. Запустите python3 server.py и откройте http://127.0.0.1:8000 в браузере.");
  }

  let response;
  try {
    response = await fetch("/api" + path, {
      method: method,
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (error) {
    throw new Error("Нет связи с сервером. Проверьте, что python3 server.py запущен.");
  }

  const data = await response.json().catch(() => ({}));

  // Сессия закончилась — возвращаем пользователя на страницу входа
  if (response.status === 401 && user) {
    user = null;
    showLogin();
    throw new Error(data.error);
  }
  if (!response.ok) {
    throw new Error(data.error || "Ошибка " + response.status);
  }
  return data;
}

// Обновляет info (категории, группы, сотрудники, неоценённые заявки)
async function reloadInfo() {
  try {
    info = await api("GET", "/me");
  } catch (error) {
    // не страшно: останутся старые данные
  }
}

// Читает файл и превращает его в текст base64, чтобы отправить на сервер в JSON
function readFile(file) {
  return new Promise((resolve, reject) => {
    if (file.size > info.max_mb * 1024 * 1024) {
      reject(new Error("Файл «" + file.name + "» больше " + info.max_mb + " МБ"));
      return;
    }
    const reader = new FileReader();
    reader.onload = () => resolve({ name: file.name, data: reader.result.split(",")[1] });
    reader.onerror = () => reject(new Error("Не удалось прочитать файл «" + file.name + "»"));
    reader.readAsDataURL(file);
  });
}

// ---------- Что видит пользователь ----------

// Серые полоски, пока данные загружаются
function loadingHtml() {
  return `
    <div class="loading-state" role="status" aria-label="Загрузка">
      <span class="visually-hidden">Загрузка…</span>
      <div class="loading-line loading-heading"></div>
      <div class="loading-line loading-card"></div>
      <div class="loading-line loading-card"></div>
      <div class="loading-line loading-card"></div>
    </div>`;
}

// Сообщение с кнопкой «Повторить» — показываем, если запрос не удался
function errorHtml(message) {
  return `
    <div class="empty" role="alert">
      <svg class="empty-icon error-icon" viewBox="0 0 48 48" aria-hidden="true">
        <circle cx="24" cy="24" r="18"/><path d="M24 14v12m0 7h.01"/>
      </svg>
      <p class="empty-title">Не удалось загрузить данные</p>
      <p class="empty-copy">${esc(message)}</p>
      <button class="btn retry-button">Повторить</button>
    </div>`;
}

function emptyStateHtml(title, description = "") {
    const copy = description ? `<p class="empty-copy">${esc(description)}</p>` : "";
    return `
      <div class="empty">
        <svg class="empty-icon" viewBox="0 0 48 48" aria-hidden="true">
          <rect x="7" y="10" width="34" height="28" rx="4"/><path d="M7 18h34m-25 8h16m-16 6h10"/>
        </svg>
        <p class="empty-title">${esc(title)}</p>${copy}
      </div>`;
}

// Короткое сообщение в углу экрана
function toast(message, isError) {
  const box = document.createElement("div");
  box.className = isError ? "toast error" : "toast";
  box.textContent = message;
  $("#toasts").appendChild(box);
  setTimeout(() => box.remove(), isError ? 7000 : 3500);
}

// Окно с полями. fields — список полей, onSubmit вызывается при нажатии на главную кнопку.
// Поле: { key, label, type: "text" | "select" | "stars", options, value }
function openDialog(title, fields, buttonText, onSubmit) {
  const dialog = $("#dialog");

  dialog.innerHTML = `
    <form method="dialog">
      <h3>${title}</h3>
      ${fields.map(dialogFieldHtml).join("")}
      <div class="form-error" role="alert" id="dialog-error"></div>
      <div class="dialog-buttons">
        <button class="btn" value="cancel" formnovalidate>Отмена</button>
        <button class="btn btn-main" id="dialog-ok">${buttonText}</button>
      </div>
    </form>`;

  // Звёзды: запоминаем, какую нажали
  let stars = 0;
  $$(".stars button").forEach((button) => {
    button.onclick = () => {
      stars = Number(button.dataset.n);
      $$(".stars button").forEach((other) => {
        other.classList.toggle("on", Number(other.dataset.n) <= stars);
      });
    };
  });

  // Если в окне есть и группа, и исполнитель — показываем только сотрудников выбранной группы
  const groupSelect = $("#field-group_id");
  const assigneeSelect = $("#field-assignee_id");
  if (groupSelect && assigneeSelect) {
    const filterAssignees = () => {
      assigneeSelect.querySelectorAll("option[data-group]").forEach((option) => {
        option.hidden = option.dataset.group !== groupSelect.value;
        if (option.hidden && option.selected) assigneeSelect.value = "";
      });
    };
    groupSelect.onchange = filterAssignees;
    filterAssignees();
  }

  $("#dialog-ok").onclick = async (event) => {
    event.preventDefault();

    // Собираем значения всех полей
    const values = {};
    fields.forEach((field) => {
      values[field.key] = field.type === "stars" ? stars : $("#field-" + field.key).value;
    });

    const button = $("#dialog-ok");
    const oldText = button.textContent;
    button.disabled = true;
    button.textContent = "Сохраняем…";
    try {
      await onSubmit(values);
      dialog.close();
    } catch (error) {
      $("#dialog-error").textContent = error.message;
      button.disabled = false;
      button.textContent = oldText;
    }
  };

  dialog.showModal();
}

// HTML одного поля для окна
function dialogFieldHtml(field) {
  const id = "field-" + field.key;
  let input;

  if (field.type === "text") {
    input = `<textarea id="${id}"></textarea>`;
  } else if (field.type === "stars") {
    const buttons = [1, 2, 3, 4, 5]
      .map((n) => `<button type="button" data-n="${n}" aria-label="Оценка ${n}">★</button>`)
      .join("");
    input = `<div class="stars" id="${id}">${buttons}</div>`;
  } else {
    // select. Вариант: [значение, подпись, группа] — группа нужна только для списка исполнителей
    const options = field.options
      .map(([value, text, group]) => {
        const groupAttr = group !== undefined ? `data-group="${group}"` : "";
        const selected = value == field.value ? "selected" : "";
        return `<option value="${value}" ${groupAttr} ${selected}>${esc(text)}</option>`;
      })
      .join("");
    input = `<select id="${id}">${options}</select>`;
  }

  return `<label for="${id}">${field.label}</label>${input}`;
}
