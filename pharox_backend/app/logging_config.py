"""
Logging estructurado para Pharox DX.

Reemplaza los print() sueltos que había en main.py/graph_db.py/etl_casos_clinicos.py
por el módulo logging estándar de Python, con dos formatos intercambiables vía
variables de entorno:

  LOG_LEVEL:  DEBUG | INFO | WARNING | ERROR (default: INFO)
  LOG_FORMAT: text (default) — coloreado, legible en una terminal de desarrollo
              json           — una línea JSON por evento, para que Azure Log
                                Analytics / Application Insights (u otro
                                agregador) puedan parsearlo e indexarlo

Los scripts de línea de comandos (bronze/civic_explorer.py, gold/civic_to_neo4j.py,
pipeline_etl.py) siguen usando print() a propósito: son herramientas que un humano
corre a mano y lee directo en su terminal, no logs de un servicio corriendo en la nube.
"""
import json
import logging
import os
import sys


class TerminalColors:
    HEADER = "\033[95m"
    OKBLUE = "\033[94m"
    OKCYAN = "\033[96m"
    OKGREEN = "\033[92m"
    WARNING = "\033[93m"
    FAIL = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"


_NIVEL_A_COLOR = {
    logging.DEBUG: TerminalColors.OKBLUE,
    logging.INFO: TerminalColors.OKCYAN,
    logging.WARNING: TerminalColors.WARNING,
    logging.ERROR: TerminalColors.FAIL,
    logging.CRITICAL: TerminalColors.FAIL,
}


class ColoredTextFormatter(logging.Formatter):
    """Formato legible y coloreado para terminal, para desarrollo local."""

    def format(self, record: logging.LogRecord) -> str:
        color = _NIVEL_A_COLOR.get(record.levelno, "")
        tag = getattr(record, "tag", record.levelname)
        bold = TerminalColors.BOLD if getattr(record, "bold", False) else ""
        return f"{bold}{color}[{tag}] {record.getMessage()}{TerminalColors.ENDC}"


class JSONFormatter(logging.Formatter):
    """Una línea JSON por evento — pensado para agregadores de logs en la nube."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        tag = getattr(record, "tag", None)
        if tag:
            payload["tag"] = tag
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configurar_logging() -> None:
    """Configura el logger raíz de la app. Llamar una sola vez, al arrancar."""
    nivel = os.getenv("LOG_LEVEL", "INFO").upper()
    formato = os.getenv("LOG_FORMAT", "text").lower()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter() if formato == "json" else ColoredTextFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(nivel)


def get_logger(nombre: str) -> logging.Logger:
    return logging.getLogger(nombre)
