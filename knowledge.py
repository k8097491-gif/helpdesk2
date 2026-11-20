"""База знаний: типовые инструкции и поиск по ним."""
from __future__ import annotations

import re
from typing import Optional

from .base import Subsystem
from .enums import Role
from .errors import ValidationError
from .models import Article

_WORD = re.compile(r"\w+", re.UNICODE)


def _tokens(text: str) -> set[str]:
    """Слова в нижнем регистре, обрезанные до 5 символов — грубый «стемминг», чтобы
    «принтер», «принтера» и «принтеры» считались одним словом."""
    return {word[:5] for word in _WORD.findall((text or "").lower()) if len(word) >= 3}


class KnowledgeBase(Subsystem):
    def add_article(self, actor_id: int, title: str, body: str, keywords: str = "",
                    category_id: Optional[int] = None) -> Article:
        actor = self.hd.accounts.require(actor_id, Role.SPECIALIST, Role.MANAGER, Role.ADMIN)
        title, body = (title or "").strip(), (body or "").strip()
        if not title or not body:
            raise ValidationError("У статьи должны быть заголовок и текст")
        if category_id is not None:
            self.hd.routing.get_category(category_id)
        with self.db.transaction():
            article_id = self.db.insert(
                "kb_articles", title=title, body=body, keywords=keywords.strip(),
                category_id=category_id, author_id=actor.id, created_at=self.now(),
            )
        return self.db.one(Article, "SELECT * FROM kb_articles WHERE id = ?", (article_id,))

    def search(self, query: str, category_id: Optional[int] = None, limit: int = 5) -> list[Article]:
        """Поиск по заголовку (вес 3), ключевым словам (2) и тексту (1); лучшие — первыми."""
        wanted = _tokens(query)
        if not wanted:
            return []
        scored = []
        for article in self.db.query(Article, "SELECT * FROM kb_articles ORDER BY id"):
            if category_id is not None and article.category_id not in (None, category_id):
                continue
            score = (3 * len(wanted & _tokens(article.title))
                     + 2 * len(wanted & _tokens(article.keywords))
                     + len(wanted & _tokens(article.body)))
            if score:
                scored.append((score, article))
        scored.sort(key=lambda pair: (-pair[0], pair[1].id))
        return [article for _, article in scored[:limit]]

    def suggest(self, title: str, description: str, category_id: Optional[int] = None) -> list[Article]:
        """Подсказки заявителю при заполнении формы — вдруг проблема решается по инструкции."""
        return self.search(f"{title} {description}", category_id)
