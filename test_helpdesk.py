"""Тесты правил из документа «Практическая работа 7».

Запуск (из корня проекта):  python3 -m unittest discover -s tests -v
"""
import sqlite3
import sys
import unittest
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from helpdesk import (  # noqa: E402
    Channel, DirectoryEntry, HelpdeskSystem, Impact, InMemoryDirectory, InvalidTransition, ManualClock,
    NotFound, PermissionDenied, Priority, Role, Status, Urgency, ValidationError,
)
from helpdesk.rules import PRIORITY_MATRIX, TRANSITIONS  # noqa: E402
from helpdesk.seed import seed_demo  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.clock = ManualClock()
        self.hd = HelpdeskSystem(clock=self.clock)
        self.d = seed_demo(self.hd)
        self.addCleanup(self.hd.close)

    def assertSameTime(self, first, second):
        """Часы системы строго возрастают (+1 мкс на обращение), поэтому сравниваем с точностью до секунды."""
        self.assertLess(abs(first - second), timedelta(seconds=1))

    def new_ticket(self, requester=None, category=None, impact=Impact.MEDIUM, urgency=Urgency.MEDIUM, **kw):
        return self.hd.intake.create_ticket(
            (requester or self.d.student).id, kw.get("title", "Не работает Wi-Fi"),
            kw.get("description", "Описание проблемы"), (category or self.d.cat_network).id, impact, urgency)

    def in_progress(self, ticket):
        self.hd.workflow.start_work(ticket.assignee_id, ticket.id)
        return self.hd.store.get(ticket.id)

    def resolved(self, ticket):
        self.in_progress(ticket)
        return self.hd.workflow.resolve(ticket.assignee_id, ticket.id, "Причина устранена, проверено.")


class CreationAndPriority(Base):
    def test_ticket_gets_number_time_and_confirmation(self):
        t = self.new_ticket()
        self.assertEqual(t.number, f"HD-{t.id:06d}")
        self.assertSameTime(t.created_at, self.clock.now())
        self.assertEqual(t.requester_id, self.d.student.id)
        inbox = self.hd.notifier.inbox(self.d.student.id)
        self.assertTrue(any("Заявка принята" in n.subject and t.number in n.subject for n in inbox))

    def test_priority_follows_matrix_for_all_combinations(self):
        for (impact, urgency), expected in PRIORITY_MATRIX.items():
            t = self.new_ticket(impact=impact, urgency=urgency)
            self.assertEqual(t.priority, expected, (impact, urgency))
        self.assertEqual(len(PRIORITY_MATRIX), len(Impact) * len(Urgency))

    def test_validation(self):
        with self.assertRaises(ValidationError):
            self.hd.intake.create_ticket(self.d.student.id, "ab", "x", self.d.cat_network.id, Impact.LOW, Urgency.LOW)
        with self.assertRaises(ValidationError):
            self.hd.intake.create_ticket(self.d.student.id, "Тема", "   ", self.d.cat_network.id, Impact.LOW, Urgency.LOW)
        with self.assertRaises(NotFound):
            self.hd.intake.create_ticket(self.d.student.id, "Тема", "Описание", 9999, Impact.LOW, Urgency.LOW)

    def test_targets_depend_on_priority(self):
        critical = self.new_ticket(impact=Impact.HIGH, urgency=Urgency.HIGH)
        low = self.new_ticket(impact=Impact.LOW, urgency=Urgency.LOW)
        self.assertEqual(critical.resolve_due_at - critical.created_at, timedelta(hours=4))
        self.assertEqual(low.resolve_due_at - low.created_at, timedelta(days=5))

    def test_attachments_validated(self):
        t = self.new_ticket()
        s = self.d.student.id
        self.hd.intake.add_attachment(s, t.id, "log.txt", b"data")
        for bad in (("virus.exe", b"x"), ("empty.txt", b""), ("big.zip", b"0" * (10 * 1024 * 1024 + 1))):
            with self.assertRaises(ValidationError):
                self.hd.intake.add_attachment(s, t.id, *bad)
        stored = self.hd.intake.add_attachment(s, t.id, "../../etc/shot.png", b"x")
        self.assertEqual(stored.filename, "shot.png")           # путь отбрасывается
        self.assertEqual(len(self.hd.store.attachments(t.id)), 2)


class Routing(Base):
    def test_auto_assign_by_category_rule(self):
        t = self.new_ticket()
        self.assertEqual(t.status, Status.ASSIGNED)
        self.assertEqual(t.group_id, self.d.group_net.id)
        self.assertEqual(t.assignee_id, self.d.specialist_net.id)

    def test_assignee_chosen_by_current_load(self):
        first = self.new_ticket()
        second = self.new_ticket()
        third = self.new_ticket()
        self.assertEqual({first.assignee_id, second.assignee_id},
                         {self.d.specialist_net.id, self.d.specialist_net2.id})
        self.assertEqual(third.assignee_id, first.assignee_id)  # снова менее нагруженный, при равенстве — меньший id

    def test_no_rule_leaves_ticket_for_dispatcher(self):
        t = self.new_ticket(category=self.d.cat_other)
        self.assertEqual(t.status, Status.NEW)
        self.assertIsNone(t.assignee_id)
        queue = self.hd.workflow.unassigned_queue(self.d.dispatcher.id)
        self.assertEqual([x.id for x in queue], [t.id])
        t = self.hd.routing.assign(self.d.dispatcher.id, t.id, self.d.group_pc.id)
        self.assertEqual((t.status, t.assignee_id), (Status.ASSIGNED, self.d.specialist_pc.id))

    def test_auto_assign_can_be_disabled(self):
        self.hd.admin.set_setting(self.d.admin.id, "auto_assign", False)
        self.assertEqual(self.new_ticket().status, Status.NEW)

    def test_assign_validates_membership_and_role(self):
        t = self.new_ticket(category=self.d.cat_other)
        d = self.d.dispatcher.id
        with self.assertRaises(ValidationError):    # специалист другой группы
            self.hd.routing.assign(d, t.id, self.d.group_net.id, self.d.specialist_pc.id)
        with self.assertRaises(ValidationError):    # заявитель не может быть исполнителем
            self.hd.routing.assign(d, t.id, self.d.group_net.id, self.d.student.id)
        with self.assertRaises(PermissionDenied):   # заявитель не назначает
            self.hd.routing.assign(self.d.student.id, t.id, self.d.group_net.id)

    def test_reassignment_keeps_history(self):
        t = self.new_ticket()
        old = t.assignee_id
        new = self.d.specialist_net2.id if old == self.d.specialist_net.id else self.d.specialist_net.id
        t = self.hd.routing.assign(self.d.manager.id, t.id, self.d.group_net.id, new, "Выравнивание нагрузки")
        self.assertEqual(t.assignee_id, new)
        history = self.hd.store.assignments(t.id)
        self.assertEqual([a.assignee_id for a in history], [old, new])
        self.assertEqual(history[-1].author_id, self.d.manager.id)
        self.assertEqual(history[-1].reason, "Выравнивание нагрузки")

    def test_dispatcher_reclassifies_and_deadlines_follow(self):
        t = self.new_ticket(impact=Impact.LOW, urgency=Urgency.LOW)
        self.assertEqual(t.priority, Priority.LOW)
        t = self.hd.routing.reclassify(self.d.dispatcher.id, t.id, "Зачёт у группы",
                                       impact=Impact.HIGH, urgency=Urgency.HIGH)
        self.assertEqual(t.priority, Priority.CRITICAL)
        self.assertEqual(t.resolve_due_at - t.sla_start_at, timedelta(hours=4))
        with self.assertRaises(ValidationError):    # без причины нельзя
            self.hd.routing.reclassify(self.d.dispatcher.id, t.id, " ", priority=Priority.LOW)
        t = self.hd.routing.reclassify(self.d.dispatcher.id, t.id, "Проблема локальная", priority=Priority.MEDIUM)
        self.assertEqual(t.priority, Priority.MEDIUM)
        changed = [e.text for e in self.hd.store.timeline(t.id) if e.kind == "Изменение"]
        self.assertEqual(len(changed), 4)           # влияние, срочность и два изменения приоритета
        self.assertTrue(all("Причина:" in text for text in changed))


class Lifecycle(Base):
    def test_full_happy_path_with_history(self):
        t = self.new_ticket()
        spec = t.assignee_id
        self.clock.advance(minutes=5)
        t = self.in_progress(t)
        self.assertEqual(t.status, Status.IN_PROGRESS)
        t = self.hd.workflow.resolve(spec, t.id, "Перезапущена точка доступа в аудитории.")
        self.assertEqual(t.status, Status.RESOLVED)
        self.assertIsNotNone(t.resolved_at)
        t = self.hd.workflow.confirm_resolution(self.d.student.id, t.id)
        self.assertEqual(t.status, Status.CLOSED)
        self.assertIsNotNone(t.closed_at)

        events = self.hd.store.status_events(t.id)
        self.assertEqual([e.to_status for e in events],
                         [Status.NEW, Status.ASSIGNED, Status.IN_PROGRESS, Status.RESOLVED, Status.CLOSED])
        self.assertTrue(all(e.reason for e in events))                     # причина есть всегда
        self.assertIsNone(events[1].author_id)                              # автоназначение — «Система»
        self.assertEqual(events[-1].author_id, self.d.student.id)

    def test_invalid_transitions_rejected(self):
        t = self.new_ticket()
        with self.assertRaises(InvalidTransition):                          # решить, не начав работу
            self.hd.workflow.resolve(t.assignee_id, t.id, "Описание решения достаточной длины")
        with self.assertRaises(InvalidTransition):                          # закрыть нерешённую
            self.hd.workflow.confirm_resolution(self.d.student.id, t.id)
        with self.assertRaises(InvalidTransition):                          # переоткрыть нерешённую
            self.hd.workflow.reopen(self.d.student.id, t.id, "Не работает")
        self.assertEqual(self.hd.store.get(t.id).status, Status.ASSIGNED)   # ничего не изменилось
        self.assertEqual(TRANSITIONS[Status.CLOSED], set())

    def test_resolution_text_required(self):
        t = self.in_progress(self.new_ticket())
        for bad in ("", "   ", "ок"):
            with self.assertRaises(ValidationError):
                self.hd.workflow.resolve(t.assignee_id, t.id, bad)
        self.assertEqual(self.hd.store.get(t.id).status, Status.IN_PROGRESS)

    def test_only_assignee_works_on_ticket(self):
        t = self.new_ticket()
        other = (self.d.specialist_net2 if t.assignee_id == self.d.specialist_net.id else self.d.specialist_net).id
        with self.assertRaises(PermissionDenied):
            self.hd.workflow.start_work(other, t.id)
        with self.assertRaises(PermissionDenied):
            self.hd.workflow.start_work(self.d.student.id, t.id)

    def test_waiting_and_requester_reply_resumes_work(self):
        t = self.in_progress(self.new_ticket())
        t = self.hd.workflow.request_info(t.assignee_id, t.id, "Укажите текст ошибки")
        self.assertEqual(t.status, Status.WAITING)
        self.hd.workflow.add_comment(self.d.student.id, t.id, "Ошибка 651")
        t = self.hd.store.get(t.id)
        self.assertEqual(t.status, Status.IN_PROGRESS)
        self.assertEqual(len(self.hd.store.comments(t.id)), 2)

    def test_reopen_restarts_sla_and_counts(self):
        t = self.resolved(self.new_ticket())
        self.clock.advance(hours=3)
        t = self.hd.workflow.reopen(self.d.student.id, t.id, "Снова пропал интернет")
        self.assertEqual(t.status, Status.REOPENED)
        self.assertEqual(t.reopen_count, 1)
        self.assertIsNone(t.resolved_at)
        self.assertSameTime(t.sla_start_at, self.clock.now())
        self.assertGreater(t.resolve_due_at, self.clock.now())
        self.assertEqual(t.assignee_id, self.hd.store.assignments(t.id)[-1].assignee_id)
        t = self.in_progress(t)                                             # «возвращают в работу»
        self.assertEqual(t.status, Status.IN_PROGRESS)
        with self.assertRaises(ValidationError):
            t2 = self.resolved(self.new_ticket())
            self.hd.workflow.reopen(self.d.student.id, t2.id, "  ")

    def test_confirm_only_by_requester(self):
        t = self.resolved(self.new_ticket())
        with self.assertRaises(PermissionDenied):
            self.hd.workflow.confirm_resolution(self.d.teacher.id, t.id)
        with self.assertRaises(PermissionDenied):
            self.hd.workflow.confirm_resolution(t.assignee_id, t.id)

    def test_auto_close_after_configured_period(self):
        t = self.resolved(self.new_ticket())
        self.clock.advance(days=2, hours=23)
        self.assertEqual(self.hd.workflow.close_expired(), [])
        self.clock.advance(hours=2)
        closed = self.hd.workflow.close_expired()
        self.assertEqual([x.id for x in closed], [t.id])
        t = self.hd.store.get(t.id)
        self.assertEqual(t.status, Status.CLOSED)
        last = self.hd.store.status_events(t.id)[-1]
        self.assertIsNone(last.author_id)
        self.assertIn("Автозакрытие", last.reason)

    def test_auto_close_period_is_configurable(self):
        self.hd.admin.set_setting(self.d.admin.id, "auto_close_days", 1)
        t = self.resolved(self.new_ticket())
        self.clock.advance(days=1, minutes=1)
        self.assertEqual(len(self.hd.workflow.close_expired()), 1)
        self.assertEqual(self.hd.store.get(t.id).status, Status.CLOSED)

    def test_closed_ticket_is_read_only(self):
        t = self.resolved(self.new_ticket())
        self.hd.workflow.confirm_resolution(self.d.student.id, t.id)
        with self.assertRaises(ValidationError):
            self.hd.workflow.add_comment(self.d.student.id, t.id, "Ещё вопрос")
        with self.assertRaises(ValidationError):
            self.hd.intake.add_attachment(self.d.student.id, t.id, "a.txt", b"x")
        with self.assertRaises(InvalidTransition):
            self.hd.workflow.reopen(self.d.student.id, t.id, "Не работает")

    def test_failed_operation_rolls_back_everything(self):
        t = self.new_ticket()
        before_events = len(self.hd.store.status_events(t.id))
        before_inbox = len(self.hd.notifier.inbox(self.d.student.id))
        with self.assertRaises(InvalidTransition):
            self.hd.workflow.confirm_resolution(self.d.student.id, t.id)
        self.assertEqual(len(self.hd.store.status_events(t.id)), before_events)
        self.assertEqual(len(self.hd.notifier.inbox(self.d.student.id)), before_inbox)


class Escalation(Base):
    def test_escalate_to_manager_keeps_history(self):
        t = self.in_progress(self.new_ticket())
        specialist = t.assignee_id
        t = self.hd.sla.escalate(specialist, t.id, "Не хватает полномочий")
        self.assertEqual(t.status, Status.ESCALATED)
        self.assertEqual(t.assignee_id, self.d.manager.id)
        escalation = self.hd.store.escalations(t.id)[0]
        self.assertEqual((escalation.author_id, escalation.reason, escalation.from_assignee_id, escalation.to_assignee_id),
                         (specialist, "Не хватает полномочий", specialist, self.d.manager.id))
        # история назначений сохранена: автоназначение -> эскалация
        self.assertEqual([a.assignee_id for a in self.hd.store.assignments(t.id)], [specialist, self.d.manager.id])
        self.assertTrue(any("эскалированная" in n.subject for n in self.hd.notifier.inbox(self.d.manager.id)))

    def test_escalate_to_other_group(self):
        t = self.in_progress(self.new_ticket())
        t = self.hd.sla.escalate(t.assignee_id, t.id, "Проблема в ПО на ПК", to_group_id=self.d.group_sw.id)
        self.assertEqual((t.status, t.group_id, t.assignee_id), (Status.ESCALATED, self.d.group_sw.id, self.d.specialist_sw.id))
        t = self.hd.workflow.start_work(self.d.specialist_sw.id, t.id)
        self.assertEqual(t.status, Status.IN_PROGRESS)

    def test_escalation_requires_reason_and_assignee(self):
        t = self.in_progress(self.new_ticket())
        with self.assertRaises(ValidationError):
            self.hd.sla.escalate(t.assignee_id, t.id, "  ")
        with self.assertRaises(PermissionDenied):
            self.hd.sla.escalate(self.d.specialist_pc.id, t.id, "Хочу")
        with self.assertRaises(ValidationError):
            self.hd.sla.escalate(t.assignee_id, t.id, "Причина", to_group_id=t.group_id)   # в ту же группу
        self.assertEqual(self.hd.store.get(t.id).status, Status.IN_PROGRESS)

    def test_manager_can_reassign_escalated_ticket(self):
        t = self.in_progress(self.new_ticket())
        t = self.hd.sla.escalate(t.assignee_id, t.id, "Сложный случай")
        t = self.hd.routing.assign(self.d.manager.id, t.id, self.d.group_pc.id, self.d.specialist_pc.id, "Нужен выезд")
        self.assertEqual((t.status, t.assignee_id), (Status.ASSIGNED, self.d.specialist_pc.id))
        self.assertEqual(len(self.hd.store.escalations(t.id)), 1)


class Sla(Base):
    def test_risk_then_breach_each_reported_once(self):
        t = self.new_ticket(impact=Impact.HIGH, urgency=Urgency.HIGH)         # 4 часа
        self.clock.advance(hours=2)
        self.assertEqual(self.hd.sla.check_deadlines(), [])
        self.clock.advance(hours=1, minutes=20)                               # >80% срока
        self.assertEqual([(x.id, k) for x, k in self.hd.sla.check_deadlines()], [(t.id, "risk")])
        self.assertEqual(self.hd.sla.check_deadlines(), [])
        self.clock.advance(hours=1)
        self.assertEqual([(x.id, k) for x, k in self.hd.sla.check_deadlines()], [(t.id, "breach")])
        self.assertEqual(self.hd.sla.check_deadlines(), [])
        self.assertEqual([x.id for x in self.hd.sla.overdue_tickets()], [t.id])
        subjects = [n.subject for n in self.hd.notifier.inbox(t.assignee_id)]
        self.assertTrue(any("Риск просрочки" in s for s in subjects))
        self.assertTrue(any("нарушен" in s for s in subjects))
        self.assertTrue(any("Риск просрочки" in n.subject for n in self.hd.notifier.inbox(self.d.manager.id)))

    def test_clock_stops_while_waiting_for_requester(self):
        t = self.in_progress(self.new_ticket(impact=Impact.HIGH, urgency=Urgency.HIGH))   # срок 4 ч
        due = t.resolve_due_at
        t = self.hd.workflow.request_info(t.assignee_id, t.id, "Уточните корпус")
        self.clock.advance(hours=10)
        self.assertEqual(self.hd.sla.check_deadlines(), [])                   # на паузе
        self.assertFalse(self.hd.sla.is_overdue(self.hd.store.get(t.id)))
        self.hd.workflow.add_comment(self.d.student.id, t.id, "Корпус Б")
        t = self.hd.store.get(t.id)
        self.assertEqual(t.resolve_due_at - due, timedelta(hours=10))
        self.assertEqual(t.waited_seconds, 10 * 3600)

    def test_changed_sla_applies_to_new_tickets(self):
        self.hd.admin.set_sla(self.d.admin.id, Priority.MEDIUM, 30, 120)
        t = self.new_ticket()
        self.assertEqual(t.resolve_due_at - t.created_at, timedelta(minutes=120))
        with self.assertRaises(ValidationError):
            self.hd.admin.set_sla(self.d.admin.id, Priority.MEDIUM, 200, 100)

    def test_late_resolution_counts_as_overdue_in_quality(self):
        t = self.in_progress(self.new_ticket(impact=Impact.HIGH, urgency=Urgency.HIGH))
        self.clock.advance(hours=5)
        self.hd.workflow.resolve(t.assignee_id, t.id, "Решено, но с опозданием.")
        report = self.hd.analytics.quality(self.d.manager.id)
        self.assertEqual(report.in_time_percent, 0.0)


class Access(Base):
    def test_requester_sees_only_own_tickets(self):
        mine = self.new_ticket(requester=self.d.student)
        theirs = self.new_ticket(requester=self.d.teacher)
        self.assertEqual([t.id for t in self.hd.intake.my_tickets(self.d.student.id)], [mine.id])
        self.assertEqual(self.hd.workflow.get_ticket(self.d.student.id, mine.id).id, mine.id)
        with self.assertRaises(PermissionDenied):
            self.hd.workflow.get_ticket(self.d.student.id, theirs.id)
        with self.assertRaises(PermissionDenied):
            self.hd.workflow.add_comment(self.d.student.id, theirs.id, "Подсматриваю")
        with self.assertRaises(PermissionDenied):
            self.hd.workflow.timeline(self.d.student.id, theirs.id)

    def test_specialist_sees_only_assigned_queue(self):
        t = self.new_ticket()
        other = self.new_ticket(category=self.d.cat_hardware)
        queue = self.hd.workflow.my_queue(t.assignee_id)
        self.assertEqual([x.id for x in queue], [t.id])
        with self.assertRaises(PermissionDenied):
            self.hd.workflow.get_ticket(t.assignee_id, other.id)
        with self.assertRaises(PermissionDenied):
            self.hd.workflow.my_queue(self.d.student.id)

    def test_dispatcher_and_manager_see_everything(self):
        t = self.new_ticket()
        for user in (self.d.dispatcher, self.d.manager):
            self.assertEqual(self.hd.workflow.get_ticket(user.id, t.id).id, t.id)
            self.assertEqual(len(self.hd.workflow.all_tickets(user.id)), 1)
        with self.assertRaises(PermissionDenied):
            self.hd.workflow.all_tickets(self.d.student.id)

    def test_attachment_download_checks_access(self):
        t = self.new_ticket(requester=self.d.student)
        a = self.hd.intake.add_attachment(self.d.student.id, t.id, "log.txt", b"hello")
        self.assertEqual(self.hd.intake.get_attachment(t.assignee_id, a.id).data, b"hello")
        with self.assertRaises(PermissionDenied):
            self.hd.intake.get_attachment(self.d.teacher.id, a.id)

    def test_only_admin_configures_system(self):
        for actor in (self.d.student, self.d.dispatcher, self.d.manager, self.d.specialist_net):
            with self.assertRaises(PermissionDenied):
                self.hd.admin.create_category(actor.id, "Новая")
            with self.assertRaises(PermissionDenied):
                self.hd.admin.set_sla(actor.id, Priority.LOW, 10, 20)
        cat = self.hd.admin.create_category(self.d.admin.id, "Новая", self.d.group_pc.id)
        self.assertEqual(cat.group_id, self.d.group_pc.id)

    def test_reports_restricted(self):
        with self.assertRaises(PermissionDenied):
            self.hd.analytics.quality(self.d.student.id)
        with self.assertRaises(PermissionDenied):
            self.hd.analytics.queue_summary(self.d.specialist_net.id)

    def test_deactivated_user_cannot_act_and_cannot_be_deactivated_with_load(self):
        t = self.new_ticket()
        with self.assertRaises(ValidationError):
            self.hd.admin.deactivate_user(self.d.admin.id, t.assignee_id)
        self.hd.admin.deactivate_user(self.d.admin.id, self.d.teacher.id)
        with self.assertRaises(PermissionDenied):
            self.new_ticket(requester=self.d.teacher)


class Feedback(Base):
    def closed(self):
        t = self.resolved(self.new_ticket())
        return self.hd.workflow.confirm_resolution(self.d.student.id, t.id)

    def test_rating_only_after_closure(self):
        t = self.resolved(self.new_ticket())
        with self.assertRaises(ValidationError):
            self.hd.feedback.rate(self.d.student.id, t.id, 5)
        self.hd.workflow.confirm_resolution(self.d.student.id, t.id)
        fb = self.hd.feedback.rate(self.d.student.id, t.id, 4, "  Хорошо  ")
        self.assertEqual((fb.rating, fb.comment, fb.ticket_id), (4, "Хорошо", t.id))

    def test_rating_range_and_single_answer(self):
        t = self.closed()
        for bad in (0, 6, -1, 3.5, "5", True, None):
            with self.assertRaises(ValidationError, msg=repr(bad)):
                self.hd.feedback.rate(self.d.student.id, t.id, bad)
        self.hd.feedback.rate(self.d.student.id, t.id, 5)
        with self.assertRaises(ValidationError):
            self.hd.feedback.rate(self.d.student.id, t.id, 1)

    def test_comment_optional_and_only_requester_rates(self):
        t = self.closed()
        with self.assertRaises(PermissionDenied):
            self.hd.feedback.rate(self.d.teacher.id, t.id, 5)
        self.assertIsNone(self.hd.feedback.rate(self.d.student.id, t.id, 3).comment)

    def test_pending_surveys_and_quality_report(self):
        t1, t2 = self.closed(), self.closed()
        self.assertEqual({t.id for t in self.hd.feedback.pending_surveys(self.d.student.id)}, {t1.id, t2.id})
        self.hd.feedback.rate(self.d.student.id, t1.id, 5)
        self.hd.feedback.rate(self.d.student.id, t2.id, 4)
        self.assertEqual(self.hd.feedback.pending_surveys(self.d.student.id), [])
        report = self.hd.analytics.quality(self.d.manager.id)
        self.assertEqual((report.rated, report.average_rating), (2, 4.5))
        self.assertEqual(report.average_rating_by_group, {"Сеть": 4.5})
        self.assertEqual(report.in_time_percent, 100.0)

    def test_survey_invitation_sent_on_close(self):
        self.closed()
        self.assertTrue(any("оцените" in n.subject for n in self.hd.notifier.inbox(self.d.student.id)))


class Reports(Base):
    def test_queue_and_workload(self):
        self.new_ticket()
        self.new_ticket(category=self.d.cat_other)
        q = self.hd.analytics.queue_summary(self.d.manager.id)
        self.assertEqual(q.by_status, {"Новая": 1, "Назначена": 1})
        self.assertEqual(q.by_group, {"Без группы": 1, "Сеть": 1})
        self.assertEqual(q.unassigned, 1)
        workload = dict((u.id, n) for u, n in self.hd.analytics.workload(self.d.manager.id))
        self.assertEqual(workload[self.d.specialist_net.id], 1)


class Notifications(Base):
    def test_delivery_uses_user_preferred_channel(self):
        t = self.new_ticket(requester=self.d.teacher)       # преподаватель выбрал push
        push, email = self.hd.notifier.channels[Channel.PUSH], self.hd.notifier.channels[Channel.EMAIL]
        self.assertTrue(any(t.number in subject for _, subject, _ in push.sent))
        self.assertFalse(any("Заявка принята" in subject and "teacher@" in addr for addr, subject, _ in email.sent))
        self.assertTrue(all(n.delivered for n in self.hd.notifier.inbox(self.d.teacher.id)))

    def test_no_notifications_when_operation_rolled_back(self):
        t = self.new_ticket()
        email = self.hd.notifier.channels[Channel.EMAIL]
        sent_before = len(email.sent)
        with self.assertRaises(InvalidTransition):
            self.hd.workflow.confirm_resolution(self.d.student.id, t.id)
        self.assertEqual(len(email.sent), sent_before)

    def test_failed_delivery_can_be_retried(self):
        class Flaky:
            def __init__(self):
                self.fail, self.sent = True, []

            def send(self, user, subject, body):
                if self.fail:
                    raise ConnectionError("SMTP недоступен")
                self.sent.append(subject)

        flaky = Flaky()
        hd = HelpdeskSystem(clock=ManualClock(), channels={Channel.EMAIL: flaky, Channel.PUSH: flaky})
        self.addCleanup(hd.close)
        d = seed_demo(hd)
        with self.assertLogs("helpdesk.notifications", level="ERROR"):      # сбой доставки логируется
            hd.intake.create_ticket(d.student.id, "Тема заявки", "Описание", d.cat_network.id, Impact.LOW, Urgency.LOW)
        self.assertTrue(hd.notifier.inbox(d.student.id))
        self.assertFalse(any(n.delivered for n in hd.notifier.inbox(d.student.id)))     # заявка создана, письма — нет
        flaky.fail = False
        self.assertGreater(hd.notifier.retry_undelivered(), 0)
        self.assertTrue(all(n.delivered for n in hd.notifier.inbox(d.student.id)))
        self.assertTrue(flaky.sent)


class KnowledgeBase(Base):
    def test_search_finds_by_word_forms_and_ranks_title_first(self):
        found = self.hd.kb.search("принтеры не печатают")
        self.assertEqual(found[0].title, "Принтер не печатает")
        self.assertEqual(self.hd.kb.search("wi-fi подключение eduroam")[0].title, "Не подключается Wi-Fi eduroam")
        self.assertEqual(self.hd.kb.search("абракадабра"), [])
        self.assertEqual(self.hd.kb.search("   "), [])

    def test_category_filter_and_permissions(self):
        self.assertEqual(self.hd.kb.search("принтер", category_id=self.d.cat_network.id), [])
        self.assertEqual(len(self.hd.kb.search("принтер", category_id=self.d.cat_hardware.id)), 1)
        with self.assertRaises(PermissionDenied):
            self.hd.kb.add_article(self.d.student.id, "Заголовок", "Текст")
        with self.assertRaises(ValidationError):
            self.hd.kb.add_article(self.d.specialist_sw.id, "", "Текст")


class Directory(unittest.TestCase):
    def test_sign_in_provisions_user_from_directory(self):
        directory = InMemoryDirectory([DirectoryEntry("ivanov", "Иван Иванов", "ivanov@univ.example")])
        hd = HelpdeskSystem(directory=directory)
        self.addCleanup(hd.close)
        user = hd.sign_in("ivanov")
        self.assertEqual((user.role, user.full_name), (Role.REQUESTER, "Иван Иванов"))
        directory.add(DirectoryEntry("ivanov", "Иван Петрович Иванов", "ivanov@univ.example"))
        self.assertEqual(hd.sign_in("ivanov").full_name, "Иван Петрович Иванов")
        self.assertEqual(hd.sign_in("ivanov").id, user.id)
        with self.assertRaises(NotFound):
            hd.sign_in("nobody")


class Persistence(unittest.TestCase):
    def test_data_survives_restart(self):
        import os
        import tempfile
        path = os.path.join(tempfile.mkdtemp(), "helpdesk.db")
        hd = HelpdeskSystem(path)
        d = seed_demo(hd)
        t = hd.intake.create_ticket(d.student.id, "Тема заявки", "Описание", d.cat_network.id, Impact.LOW, Urgency.LOW)
        hd.close()

        again = HelpdeskSystem(path)                      # SLA-политики не дублируются, данные на месте
        self.addCleanup(again.close)
        self.assertEqual(again.store.get(t.id).number, t.number)
        self.assertEqual(again.db.scalar("SELECT COUNT(*) FROM sla_policies"), 4)

    def test_database_constraints_protect_data(self):
        hd = HelpdeskSystem()
        self.addCleanup(hd.close)
        d = seed_demo(hd)
        t = hd.intake.create_ticket(d.student.id, "Тема заявки", "Описание", d.cat_network.id, Impact.LOW, Urgency.LOW)
        with self.assertRaises(sqlite3.IntegrityError):
            hd.db.execute("INSERT INTO feedback (ticket_id, requester_id, rating, created_at) VALUES (?, ?, 9, 'x')",
                          (t.id, d.student.id))


if __name__ == "__main__":
    unittest.main()
