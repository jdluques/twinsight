"""Modelos relacionales de TwinSight (Diseño §3.2).

El relacional guarda lo que el grafo NO debe guardar: series temporales,
eventos, configuración y archivos. El detalle estructural del modelo y de las
representaciones vive en el grafo (Neo4j).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, new_id


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String, unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    import_records: Mapped[list["ImportRecord"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    datasources: Mapped[list["DataSource"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    events: Mapped[list["Event"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    rules: Mapped[list["RuleDef"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    layouts: Mapped[list["WorkspaceLayout"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    presentation: Mapped[list["PresentationConfig"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    viewer_links: Mapped[list["ViewerLink"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class ImportRecord(Base):
    __tablename__ = "import_records"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String)  # gaphor|svg|dxf|image|ifc|gltf
    filename: Mapped[str] = mapped_column(String)
    file_hash: Mapped[str] = mapped_column(String)
    raw_file: Mapped[bytes] = mapped_column(LargeBinary)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    status: Mapped[str] = mapped_column(String, default="failed")  # ok|failed|inconsistent
    report: Mapped[dict] = mapped_column(JSON, default=dict)
    watch_path: Mapped[str | None] = mapped_column(String, nullable=True)
    watch_enabled: Mapped[bool] = mapped_column(Boolean, default=False)

    project: Mapped["Project"] = relationship(back_populates="import_records")


class DataSource(Base):
    __tablename__ = "datasources"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String, default="mqtt")
    config: Mapped[dict] = mapped_column(JSON, default=dict)  # {host, port, username?, base_topic}

    project: Mapped["Project"] = relationship(back_populates="datasources")
    channels: Mapped[list["Channel"]] = relationship(
        back_populates="datasource", cascade="all, delete-orphan"
    )


class Channel(Base):
    __tablename__ = "channels"
    __table_args__ = (
        UniqueConstraint("datasource_id", "topic_pattern", "metric_key", name="uq_channel"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    datasource_id: Mapped[str] = mapped_column(ForeignKey("datasources.id", ondelete="CASCADE"), index=True)
    topic_pattern: Mapped[str] = mapped_column(String)
    metric_key: Mapped[str] = mapped_column(String)
    unit: Mapped[str] = mapped_column(String, default="")
    label: Mapped[str] = mapped_column(String, default="")

    datasource: Mapped["DataSource"] = relationship(back_populates="channels")


class TelemetryReading(Base):
    __tablename__ = "telemetry_readings"

    channel_id: Mapped[str] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), primary_key=True
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    value: Mapped[float] = mapped_column(Float)


class Event(Base):
    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
    origin_kind: Mapped[str] = mapped_column(String)  # rule | transition
    origin_id: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)  # fired | cleared | transitioned
    severity: Mapped[str] = mapped_column(String, default="info")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped["Project"] = relationship(back_populates="events")


class RuleDef(Base):
    __tablename__ = "rules"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    severity: Mapped[str] = mapped_column(String, default="warning")
    definition: Mapped[dict] = mapped_column(JSON, default=dict)  # esquema versionado

    project: Mapped["Project"] = relationship(back_populates="rules")


class WorkspaceLayout(Base):
    __tablename__ = "layouts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    layout: Mapped[dict] = mapped_column(JSON, default=dict)  # dockview + config por panel

    project: Mapped["Project"] = relationship(back_populates="layouts")


class PresentationConfig(Base):
    __tablename__ = "presentation_config"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    severity: Mapped[str] = mapped_column(String)  # normal|info|warning|alarm|stale
    style: Mapped[dict] = mapped_column(JSON, default=dict)  # {color, blink, icon, sound, browser_notification}

    project: Mapped["Project"] = relationship(back_populates="presentation")


class ViewerLink(Base):
    __tablename__ = "viewer_links"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped["Project"] = relationship(back_populates="viewer_links")


class ConfigEntry(Base):
    """Configuración de instancia: claves de acceso hasheadas y key_version
    por nivel (RF-122, RF-125). ``key`` es p.ej. ``access.editor.hash`` o
    ``access.editor.version``.
    """

    __tablename__ = "config"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(Text)
