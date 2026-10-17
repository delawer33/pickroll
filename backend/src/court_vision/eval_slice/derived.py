"""Производные данные, которые пересчитываются из загруженных: хук загрузчика среза.

Срез хранит первичные данные (матчи, box score, чанки), а то, что считается из них, на срез не
выгружается — загрузчик пересчитывает это теми же командами, что и на полной базе. Иначе срез
содержал бы вторую копию логики расчёта и расходился бы с ней при каждой правке формулы.

Сейчас пересчитывать нечего: Перцентили считаются запросом по `box_scores` на лету. Спринт
3.5 добавит сюда пересборку Статистических отпечатков — одной строкой в `REBUILDS`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Final

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

log = structlog.get_logger(__name__)

SessionFactory = async_sessionmaker[AsyncSession]
Rebuild = Callable[[SessionFactory], Awaitable[None]]

# (имя для лога, пересборка). Пересборка идемпотентна: её зовут и после seed на полной базе,
# и после загрузки среза, и повторно, если срез уже лежит в базе.
REBUILDS: Final[tuple[tuple[str, Rebuild], ...]] = ()


async def rebuild_derived(
    session_factory: SessionFactory,
    rebuilds: tuple[tuple[str, Rebuild], ...] | None = None,
) -> list[str]:
    """Прогоняет пересборки по порядку; возвращает имена выполненных."""
    done: list[str] = []
    for name, rebuild in REBUILDS if rebuilds is None else rebuilds:
        log.info("eval_slice.rebuild", name=name)
        await rebuild(session_factory)
        done.append(name)
    return done
