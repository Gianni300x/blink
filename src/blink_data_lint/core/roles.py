"""Indicios conservadores por nombre; nunca afirman semántica de negocio."""

import re

CODE_TOKENS = {"id", "code", "codigo", "código", "type", "tipo", "status", "estado"}


def code_like(name: str) -> bool:
    separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", name)
    return bool(CODE_TOKENS.intersection(re.split(r"[\W_]+", separated.casefold())))
