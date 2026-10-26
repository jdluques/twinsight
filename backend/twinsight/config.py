"""Configuración central de TwinSight (pydantic-settings).

Todas las variables se pueden sobreescribir por entorno con el prefijo
``TWINSIGHT_`` (p. ej. ``TWINSIGHT_NEO4J_URI``) o por un archivo ``.env``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TWINSIGHT_", env_file=".env", extra="ignore"
    )

    # --- Servidor ---
    host: str = "0.0.0.0"
    port: int = 8000
    data_dir: Path = Field(default=Path("./data"))

    # --- Base relacional ---
    database_url: str = "sqlite+aiosqlite:///./data/twinsight.sqlite3"

    # --- Grafo (Neo4j) ---
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "twinsight"
    # Backend de grafo: "neo4j" (por defecto). La capa ModelGraph permite
    # añadir otros backends sin tocar el resto de la plataforma.
    graph_backend: str = "neo4j"

    # --- MQTT (broker externo; solo nos suscribimos) ---
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_base_topic: str = "gemelo"

    # --- Seguridad / acceso ---
    # Clave para firmar cookies de sesión. En producción se genera en la
    # instalación; por defecto una fija solo para desarrollo/demo.
    secret_key: str = "dev-only-change-me"
    session_ttl_seconds: int = 60 * 60 * 12
    # Claves de instancia iniciales para la demo (se hashean al arrancar si no
    # existen en la tabla de configuración). En producción las genera el Setup.
    editor_key: str = "editor-demo"
    operator_key: str = "operator-demo"

    # --- Frontend estático ---
    static_dir: Path | None = None  # dist del frontend, si está compilado

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings
