"""Округление публикуемых чисел.

Единое правило на весь API: встроенный `round()` округляет половину к чётному, и два
числовых потока одного ответа расходились бы в третьем знаке без всякой причины.
"""

from decimal import ROUND_HALF_UP, Decimal


def round_half_up(value: Decimal | float, places: int) -> float:
    """Половина округляется вверх, как публикуют NBA: 551 передача за 76 матчей — это 7.3
    за игру, а встроенный `round()` дал бы 7.2."""
    quantized = Decimal(str(value)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    return float(quantized)
