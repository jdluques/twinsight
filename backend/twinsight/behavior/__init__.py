"""Motor de comportamiento de TwinSight: guardas, STM y reglas.

Reúne el intérprete de guardas de máquinas de estado (RF-050..RF-052) y el
motor de reglas simples (RF-055..RF-059), orquestados por ``BehaviorService``
sobre el bus de eventos interno.
"""

from __future__ import annotations

from .guard import (
    Comparacion,
    Entre,
    GuardAST,
    GuardNode,
    GuardParseError,
    OpBooleana,
    Stale,
    evaluate,
    parse_guard,
    referenced_names,
    stale_map,
)
from .interpreter import StateMachineInterpreter, severity_for_state
from .rules import RuleEngine, test_rule, validate_rule
from .service import BehaviorService

__all__ = [
    # guardas
    "parse_guard",
    "evaluate",
    "referenced_names",
    "stale_map",
    "GuardAST",
    "GuardNode",
    "GuardParseError",
    "Comparacion",
    "Entre",
    "Stale",
    "OpBooleana",
    # reglas
    "RuleEngine",
    "validate_rule",
    "test_rule",
    # STM
    "StateMachineInterpreter",
    "severity_for_state",
    # servicio
    "BehaviorService",
]
