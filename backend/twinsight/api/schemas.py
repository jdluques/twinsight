"""Modelos Pydantic de petición/respuesta de la API (Diseño §4.1).

Solo se definen donde ayudan a validar la entrada o a documentar la salida; las
lecturas que ya devuelven dicts del grafo se serializan tal cual.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# --- Proyectos --------------------------------------------------------------
class ProjectCreate(BaseModel):
    name: str
    description: str = ""


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


# --- Fuentes de datos / canales --------------------------------------------
class DataSourceCreate(BaseModel):
    kind: str = "mqtt"
    config: dict = Field(default_factory=dict)


class DataSourceUpdate(BaseModel):
    kind: str | None = None
    config: dict | None = None


class ChannelCreate(BaseModel):
    topic_pattern: str
    metric_key: str
    unit: str = ""
    label: str = ""


class ChannelUpdate(BaseModel):
    topic_pattern: str | None = None
    metric_key: str | None = None
    unit: str | None = None
    label: str | None = None


# --- Mapeos -----------------------------------------------------------------
class MappingCreate(BaseModel):
    element_id: str | None = None
    visual_object_id: str | None = None
    channel_id: str | None = None


class SuggestionIn(BaseModel):
    element_id: str | None = None
    visual_object_id: str | None = None
    channel_id: str | None = None
    score: float = 0.0
    confidence: str = "media"
    evidence: str = ""


class ConfirmSuggestions(BaseModel):
    suggestions: list[SuggestionIn]
    origin: str = "auto"


# --- Reglas / presentación --------------------------------------------------
class RuleCreate(BaseModel):
    name: str
    enabled: bool = True
    severity: str = "warning"
    definition: dict = Field(default_factory=dict)


class RuleUpdate(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    severity: str | None = None
    definition: dict | None = None


class RuleTest(BaseModel):
    last_values: dict[str, float] = Field(default_factory=dict)


class PresentationItem(BaseModel):
    severity: str
    style: dict = Field(default_factory=dict)


class PresentationUpdate(BaseModel):
    items: list[PresentationItem]


# --- Layouts ----------------------------------------------------------------
class LayoutCreate(BaseModel):
    name: str
    is_default: bool = False
    layout: dict = Field(default_factory=dict)


class LayoutUpdate(BaseModel):
    name: str | None = None
    is_default: bool | None = None
    layout: dict | None = None


# --- Anclas (representaciones de imagen) ------------------------------------
class AnchorIn(BaseModel):
    name: str
    x: float
    y: float


class AnchorsCreate(BaseModel):
    anchors: list[AnchorIn]


# --- Autenticación ----------------------------------------------------------
class LoginIn(BaseModel):
    key: str


class RotateIn(BaseModel):
    level: str


# --- Genérico ---------------------------------------------------------------
class AckIn(BaseModel):
    note: str | None = None


def model_to_clean_dict(model: BaseModel) -> dict[str, Any]:
    """``dict`` solo con los campos presentes (para updates parciales)."""
    return model.model_dump(exclude_unset=True)
