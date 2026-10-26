"""Intérprete de guardas de transición (Apéndice D, RF-051).

Transcribe la gramática de guardas a Lark y la convierte en un AST de
*dataclasses* de datos puros. La evaluación recorre ese AST con lógica
trivaluada (``True``/``False``/``None``) y **nunca** usa ``eval`` ni ejecuta
código arbitrario (RNF-013: las guardas son datos, no código).

Gramática (Apéndice D)::

    guarda      := condicion ( ("and"|"or") condicion )*
    condicion   := referencia operador valor
                 | referencia "between" valor "and" valor
                 | "stale(" referencia "," segundos ")"
    operador    := ">" | ">=" | "<" | "<=" | "==" | "!="
    referencia  := nombre (value property o canal)

Precedencia: ``or`` enlaza más débil que ``and`` (convención habitual).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from lark import Lark, Transformer, v_args
from lark.exceptions import LarkError

# --- Operadores de comparación soportados ---
COMPARADORES: dict[str, object] = {
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


class GuardParseError(ValueError):
    """La guarda no se ajusta a la gramática del Apéndice D."""


# --------------------------------------------------------------------------
# AST de datos (sin lógica ejecutable)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Comparacion:
    """``referencia operador valor`` (p.ej. ``co2_ppm > 1000``)."""

    ref: str
    op: str
    valor: float


@dataclass(frozen=True)
class Entre:
    """``referencia between lo and hi`` (intervalo cerrado)."""

    ref: str
    lo: float
    hi: float


@dataclass(frozen=True)
class Stale:
    """``stale(referencia, segundos)`` — el canal no reporta desde hace N s."""

    ref: str
    segundos: float


@dataclass(frozen=True)
class OpBooleana:
    """Conjunción/disyunción de subcondiciones (``and``/``or``)."""

    op: str  # "and" | "or"
    terminos: tuple["GuardNode", ...]


GuardNode = Comparacion | Entre | Stale | OpBooleana
GuardAST = GuardNode


# --------------------------------------------------------------------------
# Gramática y parser Lark
# --------------------------------------------------------------------------
_GRAMMAR = r"""
?start: or_test

?or_test: and_test (_OR and_test)*
?and_test: condicion (_AND condicion)*

?condicion: referencia COMP valor                        -> comparacion
          | referencia _BETWEEN valor _AND valor          -> entre
          | _STALE "(" referencia "," valor ")"           -> stale
          | "(" or_test ")"

COMP: ">=" | "<=" | "==" | "!=" | ">" | "<"

_AND.2:     "and"
_OR.2:      "or"
_BETWEEN.2: "between"
_STALE.2:   "stale"

referencia: CNAME
valor: SIGNED_NUMBER

%import common.CNAME
%import common.SIGNED_NUMBER
%import common.WS
%ignore WS
"""


@v_args(inline=True)
class _ToAST(Transformer):
    """Convierte el árbol de Lark en el AST de dataclasses."""

    def referencia(self, token):
        return str(token)

    def valor(self, token):
        return float(token)

    def comparacion(self, ref, comp, valor):
        op = str(comp)
        return Comparacion(ref=ref, op=op, valor=valor)

    def entre(self, ref, lo, hi):
        return Entre(ref=ref, lo=lo, hi=hi)

    def stale(self, ref, segundos):
        return Stale(ref=ref, segundos=segundos)

    def and_test(self, *condiciones):
        if len(condiciones) == 1:
            return condiciones[0]
        return OpBooleana(op="and", terminos=tuple(condiciones))

    def or_test(self, *condiciones):
        if len(condiciones) == 1:
            return condiciones[0]
        return OpBooleana(op="or", terminos=tuple(condiciones))


# El parser Earley maneja bien los keywords contextuales; se construye una vez.
_PARSER = Lark(_GRAMMAR, parser="earley", maybe_placeholders=False)
_TRANSFORMER = _ToAST()


def parse_guard(text: str) -> GuardAST:
    """Convierte el texto de una guarda en su AST. Lanza ``GuardParseError``."""
    if text is None or not str(text).strip():
        raise GuardParseError("guarda vacía")
    try:
        tree = _PARSER.parse(text)
        return _TRANSFORMER.transform(tree)
    except LarkError as exc:  # pragma: no cover - mensaje de error
        raise GuardParseError(f"guarda inválida: {text!r} ({exc})") from exc


# --------------------------------------------------------------------------
# Introspección y evaluación
# --------------------------------------------------------------------------
def referenced_names(ast: GuardNode) -> set[str]:
    """Conjunto de referencias (value properties/canales) que usa la guarda."""
    if isinstance(ast, OpBooleana):
        nombres: set[str] = set()
        for t in ast.terminos:
            nombres |= referenced_names(t)
        return nombres
    if isinstance(ast, (Comparacion, Entre, Stale)):
        return {ast.ref}
    return set()  # pragma: no cover - defensivo


def evaluate(
    ast: GuardNode,
    values: dict[str, float],
    stale: dict[str, bool] | None = None,
) -> bool | None:
    """Evalúa la guarda con lógica trivaluada.

    Devuelve ``None`` (no resoluble) si alguna referencia necesaria no está
    disponible en ``values``. ``stale(ref, N)`` se cumple sii
    ``stale.get(ref)`` es verdadero (el parámetro N se calcula fuera).
    """
    stale = stale or {}
    return _eval(ast, values, stale)


def _eval(node: GuardNode, values: dict[str, float], stale: dict[str, bool]) -> bool | None:
    if isinstance(node, OpBooleana):
        resultados = [_eval(t, values, stale) for t in node.terminos]
        if node.op == "and":
            # Kleene: un False domina; si no, cualquier None deja no resoluble.
            if any(r is False for r in resultados):
                return False
            if any(r is None for r in resultados):
                return None
            return True
        # or: un True domina; si no, cualquier None deja no resoluble.
        if any(r is True for r in resultados):
            return True
        if any(r is None for r in resultados):
            return None
        return False

    if isinstance(node, Stale):
        return bool(stale.get(node.ref, False))

    if isinstance(node, Comparacion):
        if node.ref not in values:
            return None
        return bool(COMPARADORES[node.op](values[node.ref], node.valor))

    if isinstance(node, Entre):
        if node.ref not in values:
            return None
        v = values[node.ref]
        return bool(node.lo <= v <= node.hi)

    return None  # pragma: no cover - defensivo


def stale_map(
    ast: GuardNode,
    now: datetime,
    last_ts: dict[str, datetime],
) -> dict[str, bool]:
    """Calcula los flags de *stale* por referencia a partir de ``last_ts``.

    Recorre los nodos ``stale(ref, N)`` del AST: la referencia se considera
    stale si han pasado al menos ``N`` segundos desde su última lectura (o si
    nunca llegó ninguna).
    """
    flags: dict[str, bool] = {}
    _collect_stale(ast, now, last_ts, flags)
    return flags


def _collect_stale(
    node: GuardNode,
    now: datetime,
    last_ts: dict[str, datetime],
    flags: dict[str, bool],
) -> None:
    if isinstance(node, OpBooleana):
        for t in node.terminos:
            _collect_stale(t, now, last_ts, flags)
    elif isinstance(node, Stale):
        ts = last_ts.get(node.ref)
        if ts is None:
            flags[node.ref] = True
        else:
            flags[node.ref] = (now - ts).total_seconds() >= node.segundos
