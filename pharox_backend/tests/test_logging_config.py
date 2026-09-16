"""
Tests de app/logging_config.py: los dos formatos de salida (texto coloreado
para desarrollo local, JSON estructurado para producción/Azure).
"""
import json
import logging

from app.logging_config import ColoredTextFormatter, JSONFormatter


def _hacer_record(msg: str, level: int = logging.INFO, tag: str | None = None, bold: bool = False) -> logging.LogRecord:
    record = logging.LogRecord(
        name="pharox.test", level=level, pathname=__file__, lineno=1,
        msg=msg, args=(), exc_info=None,
    )
    if tag is not None:
        record.tag = tag
    if bold:
        record.bold = bold
    return record


class TestColoredTextFormatter:
    def test_incluye_el_mensaje(self):
        record = _hacer_record("Base de datos lista")
        salida = ColoredTextFormatter().format(record)
        assert "Base de datos lista" in salida

    def test_usa_el_tag_si_esta_presente(self):
        record = _hacer_record("bloqueado", tag="SEGURIDAD:BLOQUEADO")
        salida = ColoredTextFormatter().format(record)
        assert "[SEGURIDAD:BLOQUEADO]" in salida

    def test_usa_levelname_si_no_hay_tag(self):
        record = _hacer_record("algo raro", level=logging.WARNING)
        salida = ColoredTextFormatter().format(record)
        assert "[WARNING]" in salida


class TestJSONFormatter:
    def test_devuelve_json_valido(self):
        record = _hacer_record("Consulta ejecutada", tag="OK")
        salida = JSONFormatter().format(record)
        payload = json.loads(salida)  # no debe lanzar
        assert payload["message"] == "Consulta ejecutada"
        assert payload["tag"] == "OK"
        assert payload["level"] == "INFO"

    def test_sin_tag_no_incluye_la_clave(self):
        record = _hacer_record("mensaje simple")
        payload = json.loads(JSONFormatter().format(record))
        assert "tag" not in payload

    def test_incluye_logger_y_timestamp(self):
        record = _hacer_record("x")
        payload = json.loads(JSONFormatter().format(record))
        assert payload["logger"] == "pharox.test"
        assert "timestamp" in payload
