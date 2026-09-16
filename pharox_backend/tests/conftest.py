"""
Variables de entorno requeridas por los módulos de la app en tiempo de
import (PHAROX_API_KEY, ANONYMIZATION_SALT) deben existir ANTES de que
pytest importe cualquier test — por eso se fijan acá, en conftest.py, que
pytest carga antes de recolectar los módulos de test.
"""
import os

os.environ.setdefault("PHAROX_API_KEY", "test-api-key")
os.environ.setdefault("ANONYMIZATION_SALT", "test-salt-no-usar-en-produccion")
os.environ.setdefault("CIVIC_API_KEY", "test-civic-key")
