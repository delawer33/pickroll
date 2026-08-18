"""Печатает OpenAPI-документ приложения в stdout.

Нужен фронтенду: из этого документа `openapi-typescript` генерирует TS-типы, и делать это
он обязан без поднятого сервера — иначе типы нельзя пересобрать ни в CI, ни на машине без
Postgres. Приложение здесь только собирается, ни к БД, ни к сети не ходит.
"""

import json
import sys

from court_vision.main import create_app


def main() -> None:
    json.dump(create_app().openapi(), sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
