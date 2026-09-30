"""
Verificación determinística de la redacción del LLM contra el dossier.

El prompt le pide al modelo no inventar; esto lo COMPRUEBA. Toda entidad
verificable que aparece en la redacción — droga, ensayo NCT, PMID, porcentaje,
id de evidencia — tiene que estar en el dossier. Si no está, la redacción se
descarta y el médico recibe solo el dossier determinístico, con el motivo.
Nace de fallas reales del copiloto anterior: con qwen3:14b llegó a inventar un
esquema completo ("CMF") que no existía en ninguna parte de la evidencia.

Las frases de consejo ("se recomienda", "debería") no descartan la redacción
pero quedan señaladas: el copiloto informa, no prescribe.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.dominio.farmacos import canonizar, farmacos_en_canonico

_CONSEJO =re.compile(r"\b(se recomienda|se sugiere|recomendamos|sugerimos|deberia|deberian|conviene|lo ideal seria)\b")


@dataclass(frozen=True)
class Hallazgo:
    tipo: Literal["entidad_no_respaldada", "consejo"]
    detalle: str


def _porcentajes(texto: str) -> set[str]:
    return {m.replace(",", ".") for m in re.findall(r"(\d+(?:[.,]\d+)?)\s*%", texto)}


def verificar_narrativa(narrativa: str, dossier: str, ids_evidencia: set[str]) -> list[Hallazgo]:
    hallazgos: list[Hallazgo] = []
    narr, dos = canonizar(narrativa), canonizar(dossier)

    for droga in sorted(farmacos_en_canonico(narr) - farmacos_en_canonico(dos)):
        hallazgos.append(Hallazgo("entidad_no_respaldada", f"Menciona la droga '{droga}', que no está en el dossier."))

    for nct in sorted(set(re.findall(r"NCT\d{8}", narrativa.upper())) - set(re.findall(r"NCT\d{8}", dossier.upper()))):
        hallazgos.append(Hallazgo("entidad_no_respaldada", f"Cita el ensayo {nct}, que no está en el dossier."))

    pmids_dossier = set(re.findall(r"PMID:?\s*(\d+)", dossier, re.IGNORECASE))
    for pmid in sorted(set(re.findall(r"PMID:?\s*(\d+)", narrativa, re.IGNORECASE)) - pmids_dossier):
        hallazgos.append(Hallazgo("entidad_no_respaldada", f"Cita PMID {pmid}, que no está en el dossier."))

    for pct in sorted(_porcentajes(narrativa) - _porcentajes(dossier)):
        hallazgos.append(Hallazgo("entidad_no_respaldada", f"Da la cifra {pct}%, que no está en el dossier."))

    citados = {f"E{n}" for grupo in re.findall(r"\[([^\]]*)\]", narrativa) for n in re.findall(r"\bE(\d+)\b", grupo)}
    for eid in sorted(citados - ids_evidencia, key=lambda x: int(x[1:])):
        hallazgos.append(Hallazgo("entidad_no_respaldada", f"Cita la evidencia [{eid}], que no existe en el dossier."))

    for frase in sorted(set(_CONSEJO.findall(narr))):
        hallazgos.append(Hallazgo("consejo", f"Usa una fórmula de recomendación ('{frase}'): el copiloto informa, no prescribe."))

    return hallazgos
