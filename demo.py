"""Демо: полный путь заявки от регистрации до оценки качества.

Запуск:  python3 demo.py
"""
from helpdesk import HelpdeskSystem, Impact, ManualClock, Urgency
from helpdesk.seed import seed_demo


def title(text: str) -> None:
    print(f"\n{'=' * 72}\n{text}\n{'=' * 72}")


def show_timeline(hd, actor_id, ticket_id) -> None:
    for e in hd.workflow.timeline(actor_id, ticket_id):
        print(f"  {e.at:%d.%m %H:%M}  [{e.kind:<11}] {e.author}: {e.text}")


def main() -> None:
    clock = ManualClock()
    hd = HelpdeskSystem(clock=clock)
    d = seed_demo(hd)

    # ------------------------------------------------------------------ 1. Создание
    title("1. Создание заявки (заявитель)")
    hints = hd.kb.suggest("Не работает Wi-Fi", "В аудитории 305 не подключается к eduroam", d.cat_network.id)
    print("Подсказки из базы знаний:", [a.title for a in hints])
    t = hd.intake.create_ticket(
        d.student.id, "Не работает Wi-Fi в аудитории 305",
        "Ноутбук не подключается к eduroam, у всей группы та же проблема. Идёт зачёт.",
        d.cat_network.id, Impact.MEDIUM, Urgency.HIGH,
        attachments=[("screenshot.png", b"\x89PNG fake image bytes")],
    )
    print(f"{t.number}: приоритет «{t.priority.label}», статус «{t.status.label}», "
          f"исполнитель: {hd.accounts.name_of(t.assignee_id)}, срок решения: {t.resolve_due_at:%d.%m %H:%M}")

    # ------------------------------------------------------------------ 2. Работа
    title("2. Работа специалиста, запрос сведений и ответ заявителя")
    clock.advance(minutes=10)
    hd.workflow.start_work(t.assignee_id, t.id)
    clock.advance(minutes=20)
    hd.workflow.request_info(t.assignee_id, t.id, "Какая именно ошибка отображается и в каких аудиториях?")
    print("Статус:", hd.workflow.get_ticket(d.student.id, t.id).status.label)
    clock.advance(hours=1)
    hd.workflow.add_comment(d.student.id, t.id, "Ошибка «Не удалось подключиться», только в корпусе Б.")
    print("После ответа заявителя:", hd.workflow.get_ticket(d.student.id, t.id).status.label)

    # ------------------------------------------------------------------ 3. Эскалация
    title("3. Эскалация руководителю")
    clock.advance(minutes=30)
    hd.sla.escalate(t.assignee_id, t.id, "Нужен доступ к контроллеру точек доступа корпуса Б")
    t = hd.workflow.get_ticket(d.manager.id, t.id)
    print(f"Статус: «{t.status.label}», исполнитель: {hd.accounts.name_of(t.assignee_id)}")
    hd.workflow.start_work(d.manager.id, t.id, "Руководитель взял заявку")

    # ------------------------------------------------------------------ 4. Решение и закрытие
    title("4. Решение, подтверждение, оценка")
    clock.advance(minutes=45)
    hd.workflow.resolve(d.manager.id, t.id, "Перезапущен контроллер точек доступа в корпусе Б, обновлена прошивка.")
    t = hd.workflow.confirm_resolution(d.student.id, t.id)
    print("Статус:", t.status.label)
    print("Ожидают оценки:", [x.number for x in hd.feedback.pending_surveys(d.student.id)])
    hd.feedback.rate(d.student.id, t.id, 5, "Быстро решили, спасибо!")

    title("История заявки (автор, время, причина)")
    show_timeline(hd, d.manager.id, t.id)

    # ------------------------------------------------------------------ 5. Повторное открытие и автозакрытие
    title("5. Заявка без правила маршрутизации + повторное открытие + автозакрытие")
    t2 = hd.intake.create_ticket(
        d.teacher.id, "Не открывается проектор в лекционной",
        "Проектор в аудитории 101 не включается после замены лампы.",
        d.cat_other.id, Impact.LOW, Urgency.MEDIUM)
    print(f"{t2.number}: «{t2.status.label}» (правила нет), в очереди диспетчера:",
          [x.number for x in hd.workflow.unassigned_queue(d.dispatcher.id)])
    hd.routing.reclassify(d.dispatcher.id, t2.id, "Лекция начинается через час", urgency=Urgency.HIGH)
    t2 = hd.routing.assign(d.dispatcher.id, t2.id, d.group_pc.id, reason="Оборудование аудиторий")
    print(f"Назначена: {hd.accounts.name_of(t2.assignee_id)}, приоритет «{t2.priority.label}»")
    hd.workflow.start_work(t2.assignee_id, t2.id)
    hd.workflow.resolve(t2.assignee_id, t2.id, "Лампа была установлена неправильно, переустановлена.")
    hd.workflow.reopen(d.teacher.id, t2.id, "Проектор снова отключился через 10 минут")
    t2 = hd.workflow.get_ticket(d.teacher.id, t2.id)
    print(f"После повторного открытия: «{t2.status.label}», повторных открытий: {t2.reopen_count}")
    hd.workflow.start_work(t2.assignee_id, t2.id)
    hd.workflow.resolve(t2.assignee_id, t2.id, "Заменён блок питания проектора, проверено в течение часа.")
    clock.advance(days=4)
    closed = hd.workflow.close_expired()
    print("Автозакрыты:", [x.number for x in closed])

    # ------------------------------------------------------------------ 6. Сроки
    title("6. Предупреждения о сроках")
    t3 = hd.intake.create_ticket(
        d.student.id, "Не могу войти в личный кабинет", "Пишет «неверный пароль», хотя пароль не менялся.",
        d.cat_account.id, Impact.HIGH, Urgency.HIGH)
    print(f"{t3.number}: «{t3.priority.label}», срок решения {t3.resolve_due_at:%d.%m %H:%M}")
    clock.advance(hours=3, minutes=30)
    print("Через 3,5 ч:", [(x.number, kind) for x, kind in hd.sla.check_deadlines()])
    clock.advance(hours=1)
    print("Через 4,5 ч:", [(x.number, kind) for x, kind in hd.sla.check_deadlines()])
    print("Повторная проверка:", hd.sla.check_deadlines())

    # ------------------------------------------------------------------ 7. Отчёты
    title("7. Отчёты руководителя")
    q = hd.analytics.queue_summary(d.manager.id)
    print("По статусам:", q.by_status)
    print("Открытые по группам:", q.by_group, "| без исполнителя:", q.unassigned)
    print("Просрочены:", [x.number for x in hd.analytics.overdue(d.manager.id)])
    print("Нагрузка:", [(u.full_name, n) for u, n in hd.analytics.workload(d.manager.id)])
    print("Качество:", hd.analytics.quality(d.manager.id))

    title("Уведомления студента")
    for n in hd.notifier.inbox(d.student.id)[::-1]:
        print(f"  [{n.channel.label}] {n.subject}")


if __name__ == "__main__":
    main()
