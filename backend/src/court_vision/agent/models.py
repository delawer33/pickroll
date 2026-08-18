from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, Identity, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from court_vision.db import Base


class LlmUsage(Base):
    """Строка учёта на каждый состоявшийся вызов LLM: токены, стоимость, латентность.

    Пишется с первого спринта, хотя дашборд стоимости приезжает в спринте 5: включить
    учёт задним числом — значит открыть дашборд с пустой таблицей вместо истории.

    Своя таблица при живом Langfuse не дублирование: Langfuse — инструмент отладки с
    собственным сроком хранения, а продуктовые агрегаты (сколько стоит пользователь,
    сколько стоит инструмент) считаются по своим данным и переживают отключение трейсинга.
    """

    __tablename__ = "llm_usage"

    # Идентификаторы остальных таблиц приезжают из данных NBA.com; здесь строка своя,
    # поэтому ключ генерирует БД — identity, а не serial, как предписывает стандарт.
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    # Что просили и что ответило: запрошенная env-строка litellm (`anthropic/claude-haiku-4-5`)
    # и модель из ответа провайдера. Они расходятся на алиасах и на снапшотах версий, и
    # для разбора счёта нужна вторая.
    model: Mapped[str] = mapped_column(String(128))
    response_model: Mapped[str | None] = mapped_column(String(128))
    # Место вызова (`agent.step`): без него в таблице нельзя отделить ответ пользователю от
    # фоновой генерации дайджеста, а стоимость этих вызовов спрашивают по отдельности.
    operation: Mapped[str] = mapped_column(String(64), index=True)

    prompt_tokens: Mapped[int] = mapped_column(Integer)
    completion_tokens: Mapped[int] = mapped_column(Integer)
    total_tokens: Mapped[int] = mapped_column(Integer)

    # NULL, а не ноль: у модели без прайслиста в litellm стоимость неизвестна, и ноль в
    # этой колонке занизил бы любую сумму по таблице.
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    latency_ms: Mapped[int] = mapped_column(Integer)
