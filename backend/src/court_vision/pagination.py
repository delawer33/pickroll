"""Cursor-пагинация растущих списков.

Курсор, а не offset: Диалоги и Реплики дописываются сверху и снизу, а на offset'е любая
новая запись между двумя запросами сдвигает окно — страницы дублируют и теряют элементы.
Курсор — это позиция в порядке сортировки, и она не зависит от того, сколько записей
появилось рядом.

Наружу курсор опаковый: base64 от собранных частей. Смысл частей — внутреннее дело
эндпоинта, и клиент, который попробует его разобрать, сломается при первой смене порядка
сортировки. Именно поэтому он не читается глазами.
"""

from base64 import urlsafe_b64decode, urlsafe_b64encode
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from court_vision.errors import DomainError
from court_vision.problems import PROBLEM_TYPE_PREFIX

# Разделитель частей. Управляющий символ, а не запятая: в части может оказаться что угодно,
# включая метку времени с двоеточиями и знаками препинания.
_SEPARATOR = "\x1f"

# Потолок страницы: без него `limit=100000` превращает список в выгрузку всей таблицы.
MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 20


class InvalidCursor(DomainError):
    """Курсор не разбирается: обрезан, перепутан с курсором другого списка, придуман руками.

    Это 400, а не пустая страница: молча отдать первую страницу вместо запрошенной значит
    зациклить клиента, который листает до конца.
    """

    status: ClassVar[int] = 400
    problem_type: ClassVar[str] = f"{PROBLEM_TYPE_PREFIX}invalid-cursor"
    title: ClassVar[str] = "Invalid cursor"

    def __init__(self) -> None:
        super().__init__("Курсор пагинации нечитаем. Запросите список с начала.")


class Page[ItemT](BaseModel):
    """Страница списка. `next_cursor` пуст, когда дальше ничего нет."""

    items: list[ItemT]
    next_cursor: str | None = Field(
        default=None,
        description="Курсор следующей страницы; `null` — страница последняя. Опаковый.",
    )


def encode_cursor(*parts: Any) -> str:
    """Собирает курсор из частей ключа сортировки в том же порядке, в каком они сравниваются."""
    joined = _SEPARATOR.join(str(part) for part in parts)
    return urlsafe_b64encode(joined.encode()).decode()


def decode_cursor(cursor: str, *, parts: int) -> tuple[str, ...]:
    """Разбирает курсор, ожидая ровно `parts` частей.

    Число частей проверяется, потому что курсор одного списка легко доедет до эндпоинта
    другого — например из сохранённой ссылки; без проверки он развалился бы уже в SQL.
    """
    try:
        decoded = urlsafe_b64decode(cursor.encode()).decode()
    except (ValueError, UnicodeDecodeError) as error:
        raise InvalidCursor() from error
    values = tuple(decoded.split(_SEPARATOR))
    if len(values) != parts:
        raise InvalidCursor()
    return values
