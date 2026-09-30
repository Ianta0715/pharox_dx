"""
Evidencia tipada: la unidad que la capa de conocimiento le entrega al resto.

Reemplaza a los strings con etiquetas entre corchetes ("[EVIDENCIA CIViC] ...")
que antes se concatenaban para el LLM. Cada pieza sabe de qué fuente viene,
qué opciones del catálogo respalda, y si describe a OTRAS personas (registro
poblacional, casos similares) — algo que nunca puede presentarse como dato
de la paciente consultada.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum


class Fuente(str, Enum):
    GRAFO = "consulta_cypher"
    PROTOCOLO = "protocolo_estandar"
    CIVIC = "civic"
    ENSAYO = "clinicaltrials"
    ELEGIBILIDAD = "elegibilidad_persistida"
    CBIOPORTAL = "cbioportal"
    CLINVAR = "clinvar"
    REGISTRO = "registro_tumores"
    ACTUALIZACION = "actualizacion_guia"
    LITERATURA = "europepmc"
    CASO = "caso_clinico"


ETIQUETA_FUENTE = {
    Fuente.GRAFO: "Consulta estructurada al grafo",
    Fuente.PROTOCOLO: "Protocolo estándar registrado",
    Fuente.CIVIC: "CIViC",
    Fuente.ENSAYO: "ClinicalTrials.gov",
    Fuente.ELEGIBILIDAD: "Elegibilidad persistida",
    Fuente.CBIOPORTAL: "cBioPortal",
    Fuente.CLINVAR: "ClinVar",
    Fuente.REGISTRO: "Registro de tumores (Hospital Central, Mendoza)",
    Fuente.ACTUALIZACION: "Actualización de guía",
    Fuente.LITERATURA: "Europe PMC",
    Fuente.CASO: "Caso clínico real",
}


@dataclass(frozen=True)
class Evidencia:
    fuente: Fuente
    titulo: str
    detalle: str
    nivel: str | None = None
    url: str | None = None
    referencias: tuple[str, ...] = ()  # "PMID:123", "NCT01234567"
    opciones: tuple[str, ...] = ()  # ids de opciones del catálogo que contextualiza
    sobre_otras_personas: bool = False
    id: str = ""  # "E1", "E2"... asignado al armar el dossier


def numerar(evidencias: list[Evidencia]) -> list[Evidencia]:
    """Asigna ids estables E1..En en el orden recibido."""
    return [replace(e, id=f"E{i}") for i, e in enumerate(evidencias, start=1)]
