"""
Conexión a Neo4j (app/conocimiento/grafo.py): base de datos configurable
(NEO4J_DATABASE, el id de la instancia en AuraDB) y modo de acceso de cada
sesión. Usa un driver falso: no abre conexiones.
"""
import pytest
from neo4j import READ_ACCESS, WRITE_ACCESS

from app.config import get_settings
from app.conocimiento import grafo


class _Registro:
    def __init__(self, datos):
        self._datos = datos

    def data(self):
        return self._datos


class _Tx:
    def run(self, cypher, params):
        return [_Registro({"x": 1})]


class _Sesion:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute_read(self, fn):
        return fn(_Tx())


class _DriverFalso:
    def __init__(self):
        self.sesiones = []

    def session(self, **kwargs):
        self.sesiones.append(kwargs)
        return _Sesion()


@pytest.fixture
def driver(monkeypatch):
    # Hermético: sin esto, get_settings() recarga el .env real del desarrollador
    # (que en AuraDB define NEO4J_DATABASE) y pisa lo que fija cada test.
    monkeypatch.setattr("app.config.load_dotenv", lambda *args, **kwargs: False)
    get_settings.cache_clear()
    falso = _DriverFalso()
    monkeypatch.setattr(grafo, "_driver", falso)
    yield falso
    get_settings.cache_clear()


def test_lectura_en_la_base_de_auradb_y_en_modo_lectura(driver, monkeypatch):
    monkeypatch.setenv("NEO4J_DATABASE", "2661a5a9")
    assert grafo.leer("RETURN 1 AS x") == [{"x": 1}]
    assert driver.sesiones == [{"database": "2661a5a9", "default_access_mode": READ_ACCESS}]


def test_sin_neo4j_database_usa_la_base_por_defecto(driver, monkeypatch):
    monkeypatch.delenv("NEO4J_DATABASE", raising=False)
    grafo.leer("RETURN 1 AS x")
    assert driver.sesiones[0]["database"] is None


def test_escritura_solo_para_ingesta_y_esquema(driver, monkeypatch):
    monkeypatch.setenv("NEO4J_DATABASE", "2661a5a9")
    with grafo.sesion_escritura():
        pass
    assert driver.sesiones == [{"database": "2661a5a9", "default_access_mode": WRITE_ACCESS}]
