"""
Léxico de fármacos oncológicos de mama.

Un solo lugar para dos usos:
- Reconocer qué fármacos nombra una consulta, y traducirlos a los nombres en
  inglés con que aparecen en CIViC y ClinicalTrials.gov ("letrozol" → "letrozole").
- Verificar que la redacción del LLM no nombre fármacos que la evidencia no trae
  (lenguaje/verificacion.py).

Todo se compara sobre un texto "canónico": minúsculas, sin tildes y con los
sinónimos llevados a una sola forma ("trastuzumab deruxtecán" y "T-DXd" → "t-dxd").
"""
from __future__ import annotations

import re
import unicodedata

# De más largo a más corto: "trastuzumab deruxtecan" antes que "trastuzumab".
_SINONIMOS = {
    "trastuzumab deruxtecan": "t-dxd",
    "trastuzumab emtansine": "t-dm1",
    "trastuzumab emtansina": "t-dm1",
    "ado-trastuzumab": "t-dm1",
    "sacituzumab govitecan": "sacituzumab",
    "datopotamab deruxtecan": "dato-dxd",
    "letrozole": "letrozol",
    "anastrozole": "anastrozol",
    "exemestane": "exemestano",
    "tamoxifen": "tamoxifeno",
    "capecitabine": "capecitabina",
    "carboplatin": "carboplatino",
    "cisplatin": "cisplatino",
    "doxorubicin": "doxorrubicina",
    "epirubicin": "epirrubicina",
    "cyclophosphamide": "ciclofosfamida",
    "goserelin": "goserelina",
    "gemcitabine": "gemcitabina",
    "eribulin": "eribulina",
    "vinorelbine": "vinorelbina",
    "methotrexate": "metotrexato",
    "fluorouracil": "fluorouracilo",
    "zoledronic": "zoledronico",
}

FARMACOS = (
    "t-dxd", "t-dm1", "dato-dxd", "trastuzumab", "pertuzumab", "lapatinib", "neratinib", "tucatinib",
    "olaparib", "talazoparib", "palbociclib", "ribociclib", "abemaciclib", "letrozol", "anastrozol",
    "exemestano", "tamoxifeno", "fulvestrant", "elacestrant", "camizestrant", "imlunestrant", "alpelisib",
    "capivasertib", "inavolisib", "everolimus", "pembrolizumab", "atezolizumab", "sacituzumab",
    "capecitabina", "paclitaxel", "docetaxel", "carboplatino", "cisplatino", "doxorrubicina",
    "epirrubicina", "ciclofosfamida", "eribulina", "gemcitabina", "vinorelbina", "goserelina",
    "leuprolide", "zoledronico", "denosumab", "cmf", "metotrexato", "fluorouracilo",
)

# Nombre canónico → término de búsqueda en inglés (CIViC, ClinicalTrials.gov): el
# inverso de _SINONIMOS sin las variantes en español. Los fármacos que no figuran
# se escriben igual en ambos idiomas.
_TERMINO_BUSQUEDA = {
    canonico: original
    for original, canonico in _SINONIMOS.items()
    if original not in ("trastuzumab emtansina", "ado-trastuzumab")
}


def canonizar(texto: str) -> str:
    sin_tildes = "".join(c for c in unicodedata.normalize("NFKD", (texto or "").lower()) if not unicodedata.combining(c))
    for original, canonico in _SINONIMOS.items():
        sin_tildes = sin_tildes.replace(original, canonico)
    return sin_tildes


def farmacos_en_canonico(texto_canonico: str) -> set[str]:
    """Fármacos nombrados en un texto ya canonizado."""
    return {f for f in FARMACOS if re.search(rf"(?<![\w-]){re.escape(f)}(?![\w-])", texto_canonico)}


def farmacos_mencionados(texto: str | None) -> tuple[str, ...]:
    """Fármacos (nombre canónico) que nombra un texto libre, en el orden del léxico."""
    encontrados = farmacos_en_canonico(canonizar(texto or ""))
    return tuple(f for f in FARMACOS if f in encontrados)


def terminos_busqueda(farmacos: tuple[str, ...]) -> tuple[str, ...]:
    """Nombres en inglés y en minúsculas, como los compara la capa de conocimiento."""
    return tuple(dict.fromkeys(_TERMINO_BUSQUEDA.get(f, f) for f in farmacos))
