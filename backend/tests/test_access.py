"""Pruebas del control de acceso por niveles (twinsight.access, RF-120..125).

Usan una base SQLite temporal en archivo (``:memory:`` no comparte datos entre
conexiones del pool). La URL se fija ANTES de importar la capa de persistencia.
"""

from __future__ import annotations

import os
import tempfile

import pytest

# La URL de la BD debe fijarse ANTES de importar la capa de persistencia.
_TMP_DB = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
_TMP_DB.close()
os.environ["TWINSIGHT_DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB.name}"

from fastapi import Depends, FastAPI  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from twinsight.access import (  # noqa: E402
    COOKIE_NAME,
    Principal,
    create_viewer_link,
    current_version,
    ensure_instance_keys,
    issue_cookie,
    level_ge,
    read_cookie,
    require_editor,
    require_operator,
    require_viewer,
    revoke_viewer_link,
    rotate_key,
    verify_key,
    verify_viewer_token,
)
from twinsight.config import get_settings  # noqa: E402
from twinsight.persistence import db as db_mod  # noqa: E402
from twinsight.persistence.db import get_sessionmaker, init_db, new_id  # noqa: E402
from twinsight.persistence.models import Project, ViewerLink  # noqa: E402


@pytest.fixture
async def access_db():
    """Reinicia el engine a la BD temporal, recrea el esquema y siembra claves."""
    get_settings.cache_clear()
    db_mod._engine = None
    db_mod._sessionmaker = None
    engine = db_mod.get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(db_mod.Base.metadata.drop_all)
    await init_db()
    await ensure_instance_keys()
    yield get_sessionmaker()
    await engine.dispose()


# --------------------------------------------------------------------------- #
# Orden de niveles
# --------------------------------------------------------------------------- #
def test_orden_de_niveles():
    assert level_ge("editor", "viewer")
    assert level_ge("operator", "operator")
    assert level_ge("editor", "operator")
    assert not level_ge("viewer", "operator")
    assert not level_ge("operator", "editor")


# --------------------------------------------------------------------------- #
# Hash / verificación de claves
# --------------------------------------------------------------------------- #
async def test_verify_key_correcta_e_incorrecta(access_db):
    settings = get_settings()

    assert await verify_key(settings.editor_key) == ("editor", 1)
    assert await verify_key(settings.operator_key) == ("operator", 1)
    assert await verify_key("clave-que-no-existe") is None


async def test_ensure_instance_keys_es_idempotente(access_db):
    # Segunda llamada no debe cambiar los hashes ni las versiones.
    await ensure_instance_keys()
    assert await current_version("editor") == 1
    assert await current_version("operator") == 1
    assert await verify_key(get_settings().editor_key) == ("editor", 1)


# --------------------------------------------------------------------------- #
# Rotación de claves
# --------------------------------------------------------------------------- #
async def test_rotate_key_cambia_clave_y_version(access_db):
    settings = get_settings()
    vieja = settings.editor_key

    nueva = await rotate_key("editor", new_key="nueva-editor")

    assert nueva == "nueva-editor"
    assert await current_version("editor") == 2
    # La clave nueva verifica con la versión nueva.
    assert await verify_key("nueva-editor") == ("editor", 2)
    # La clave vieja ya no verifica.
    assert await verify_key(vieja) is None
    # Operator no se vio afectado.
    assert await current_version("operator") == 1


async def test_rotate_key_sin_clave_genera_una_aleatoria(access_db):
    nueva = await rotate_key("operator")
    assert isinstance(nueva, str) and nueva
    assert await verify_key(nueva) == ("operator", 2)


async def test_rotate_key_nivel_invalido(access_db):
    with pytest.raises(ValueError):
        await rotate_key("viewer")


# --------------------------------------------------------------------------- #
# Cookie de sesión
# --------------------------------------------------------------------------- #
def test_cookie_roundtrip():
    token = issue_cookie("editor", 1)
    data = read_cookie(token)
    assert data == {"level": "editor", "version": 1}


def test_cookie_firma_invalida():
    assert read_cookie("basura.invalida") is None


async def test_cookie_version_vieja_tras_rotacion(access_db):
    # Cookie emitida con la versión 1.
    token = issue_cookie("editor", 1)
    assert read_cookie(token) == {"level": "editor", "version": 1}

    # Tras rotar, la versión vigente es 2: el chequeo de versión (como en deps)
    # debe rechazar la cookie vieja.
    await rotate_key("editor")
    data = read_cookie(token)
    assert data is not None
    assert data["version"] != await current_version("editor")


# --------------------------------------------------------------------------- #
# Enlaces viewer
# --------------------------------------------------------------------------- #
async def test_viewer_link_crear_verificar_revocar(access_db):
    sm = access_db
    async with sm() as session:
        project = Project(id=new_id(), name="proj-viewer")
        session.add(project)
        await session.commit()
        project_id = project.id

    token = await create_viewer_link(project_id)
    assert await verify_viewer_token(token) == project_id
    # Un token cualquiera no verifica.
    assert await verify_viewer_token("token-inexistente") is None

    # Obtenemos el id del enlace para revocarlo.
    async with sm() as session:
        from sqlalchemy import select

        link = (await session.execute(select(ViewerLink))).scalar_one()
        link_id = link.id

    await revoke_viewer_link(link_id)
    assert await verify_viewer_token(token) is None


# --------------------------------------------------------------------------- #
# Dependencias require_level (mini-app FastAPI)
# --------------------------------------------------------------------------- #
def _build_app() -> FastAPI:
    app = FastAPI()

    @app.get("/viewer")
    async def viewer_ep(p: Principal = Depends(require_viewer)):
        return {"level": p.level, "project_id": p.project_id}

    @app.get("/operator")
    async def operator_ep(p: Principal = Depends(require_operator)):
        return {"level": p.level}

    @app.get("/editor")
    async def editor_ep(p: Principal = Depends(require_editor)):
        return {"level": p.level}

    return app


async def test_require_level_sin_credencial_401(access_db):
    client = TestClient(_build_app())
    assert client.get("/viewer").status_code == 401
    assert client.get("/operator").status_code == 401


async def test_require_level_con_cookie(access_db):
    client = TestClient(_build_app())

    # Cookie de operator vigente (version 1).
    cookie = issue_cookie("operator", await current_version("operator"))
    client.cookies.set(COOKIE_NAME, cookie)

    # operator puede en /operator y /viewer, pero no en /editor (403).
    assert client.get("/operator").status_code == 200
    assert client.get("/viewer").status_code == 200
    assert client.get("/editor").status_code == 403


async def test_require_level_cookie_version_vieja_401(access_db):
    client = TestClient(_build_app())
    cookie = issue_cookie("editor", 1)
    client.cookies.set(COOKIE_NAME, cookie)

    # Antes de rotar: válida.
    assert client.get("/editor").status_code == 200

    # Tras rotar, la versión de la cookie (1) queda obsoleta -> 401.
    await rotate_key("editor")
    assert client.get("/editor").status_code == 401


async def test_require_level_token_viewer(access_db):
    sm = access_db
    async with sm() as session:
        project = Project(id=new_id(), name="proj-dep-viewer")
        session.add(project)
        await session.commit()
        project_id = project.id

    token = await create_viewer_link(project_id)
    client = TestClient(_build_app())

    # Token por query param: concede viewer sobre el proyecto.
    resp = client.get("/viewer", params={"viewer_token": token})
    assert resp.status_code == 200
    assert resp.json() == {"level": "viewer", "project_id": project_id}

    # Token por cabecera: también vale.
    resp = client.get("/viewer", headers={"X-Viewer-Token": token})
    assert resp.status_code == 200

    # viewer no basta para /operator (403).
    assert client.get("/operator", params={"viewer_token": token}).status_code == 403
