"""Демонстрационные данные: группы, сотрудники, категории с правилами маршрутизации, статьи базы знаний."""
from __future__ import annotations

from dataclasses import dataclass

from .enums import Channel, Role
from .models import Article, Category, Group
from .system import HelpdeskSystem


@dataclass
class Demo:
    """Ссылки на созданные объекты, чтобы демо и тесты не искали их по имени."""
    admin: object
    dispatcher: object
    specialist_net: object       # группа «Сеть»
    specialist_net2: object
    specialist_pc: object        # группа «Рабочие места»
    specialist_sw: object        # группа «ПО и учётные записи»
    manager: object
    student: object
    teacher: object
    group_net: object
    group_pc: object
    group_sw: object
    cat_network: object
    cat_hardware: object
    cat_software: object
    cat_account: object
    cat_other: object            # без правила маршрутизации — разбирает диспетчер


def seed_demo(hd: HelpdeskSystem) -> Demo:
    """Создать недостающую демо-конфигурацию, не меняя существующие записи."""
    admin = hd.accounts.find_by_login("admin")
    if admin is None:
        admin = hd.bootstrap_admin("admin", "Администратор Системы", "admin@univ.example")
    if admin.role is not Role.ADMIN:
        raise ValueError("Для создания демо-данных пользователь admin должен быть администратором")
    a = admin.id

    def group(name):
        found = hd.db.one(Group, "SELECT * FROM groups WHERE name = ?", (name,))
        return found or hd.admin.create_group(a, name)

    net = group("Сеть")
    pc = group("Рабочие места")
    sw = group("ПО и учётные записи")

    def user(login, name, email, role=Role.REQUESTER, group_id=None, channel=Channel.EMAIL):
        return hd.accounts.find_by_login(login) or hd.admin.create_user(
            a, login, name, email, role, group_id, channel)

    dispatcher = user("dispatcher", "Дарья Диспетчерова", "dispatcher@univ.example", Role.DISPATCHER)
    manager = user("manager", "Михаил Руководов", "manager@univ.example", Role.MANAGER, net.id)
    s_net = user("net1", "Нурлан Сетевой", "net1@univ.example", Role.SPECIALIST, net.id)
    s_net2 = user("net2", "Наталья Сетевая", "net2@univ.example", Role.SPECIALIST, net.id)
    s_pc = user("pc1", "Павел Железнов", "pc1@univ.example", Role.SPECIALIST, pc.id, Channel.PUSH)
    s_sw = user("sw1", "Софья Программова", "sw1@univ.example", Role.SPECIALIST, sw.id)
    student = user("student", "Асель Студентова", "student@univ.example")
    teacher = user("teacher", "Тимур Преподавалов", "teacher@univ.example", channel=Channel.PUSH)

    def category(name, group_id=None):
        found = hd.db.one(Category, "SELECT * FROM categories WHERE name = ?", (name,))
        return found or hd.admin.create_category(a, name, group_id)

    c_net = category("Сеть и Wi-Fi", net.id)
    c_hw = category("Компьютер и периферия", pc.id)
    c_sw = category("Программное обеспечение", sw.id)
    c_acc = category("Учётная запись и доступ", sw.id)
    c_other = category("Другое")

    def article(author, title, body, keywords, category_id):
        if hd.db.one(Article, "SELECT * FROM kb_articles WHERE title = ?", (title,)) is None:
            hd.kb.add_article(author.id, title, body, keywords=keywords, category_id=category_id)

    article(s_net, "Не подключается Wi-Fi eduroam",
            "1. Забудьте сеть и подключитесь заново.\n2. Логин вводите полностью, вместе с @univ.example.\n"
            "3. Проверьте, что срок действия пароля не истёк.",
            "wifi вайфай сеть интернет eduroam", c_net.id)
    article(s_pc, "Принтер не печатает",
            "1. Проверьте питание и бумагу.\n2. Очистите очередь печати.\n3. Переустановите драйвер с портала университета.",
            "принтер печать драйвер", c_hw.id)
    article(s_sw, "Как сбросить пароль от учётной записи",
            "Откройте страницу восстановления доступа, укажите логин и подтвердите личность через почту.",
            "пароль сброс вход учётка аккаунт", c_acc.id)

    return Demo(admin, dispatcher, s_net, s_net2, s_pc, s_sw, manager, student, teacher,
                net, pc, sw, c_net, c_hw, c_sw, c_acc, c_other)
