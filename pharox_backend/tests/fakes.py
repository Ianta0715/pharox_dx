"""
Dobles de prueba: un Lector de Neo4j en memoria y un LLM guionado.

El lector responde según qué subgrafo toca el Cypher, con filas que imitan las
reales (los protocolos son los 4 de mama de data/tratamientos.xlsx). Devuelve
todo sin filtrar por WHERE a propósito, salvo los protocolos: así los tests
verifican también el filtrado que hace el código Python sobre las filas.
"""
from __future__ import annotations

PROTOCOLOS = [
    {"id": "PROTO_01", "histologia_subtipo": "Carcinoma Ductal Infiltrante", "biomarcadores_criticos": "RE+, RP+, HER2 neg",
     "estadio_tnm": "I - III", "intencion_linea": "Adyuvante", "modalidad": "Quimioterapia + Endocrina",
     "protocolo_esquema": "AC-T: Doxorrubicina + Ciclofosfamida (4 ciclos) → Paclitaxel (4 ciclos), seguido de terapia endocrina",
     "subtipo": "RH_positivo_HER2_negativo"},
    {"id": "PROTO_02", "histologia_subtipo": "Carcinoma Infiltrante", "biomarcadores_criticos": "HER2 positivo (IHC 3+ o FISH+)",
     "estadio_tnm": "I - III", "intencion_linea": "Neoadyuvante / Adyuvante", "modalidad": "Terapia Dirigida + Quimioterapia",
     "protocolo_esquema": "TCHP: Docetaxel + Carboplatino + Trastuzumab + Pertuzumab (6 ciclos) → cirugía",
     "subtipo": "HER2_positivo"},
    {"id": "PROTO_03", "histologia_subtipo": "Adenocarcinoma", "biomarcadores_criticos": "RE+, HER2 neg",
     "estadio_tnm": "IV (Metastásico)", "intencion_linea": "1ª Línea Sistémica", "modalidad": "Terapia Dirigida + Endocrina",
     "protocolo_esquema": "CDK4/6i + Terapia Endocrina: Ribociclib o Palbociclib + Letrozol o Fulvestrant",
     "subtipo": "RH_positivo_HER2_negativo"},
    {"id": "PROTO_04", "histologia_subtipo": "Triple Negativo", "biomarcadores_criticos": "RE-, RP-, HER2-",
     "estadio_tnm": "II - III", "intencion_linea": "Neoadyuvante / Adyuvante", "modalidad": "Inmunoterapia + Quimioterapia",
     "protocolo_esquema": "KEYNOTE-522: Pembrolizumab + Carboplatino + Paclitaxel → cirugía → Pembrolizumab",
     "subtipo": "Triple_negativo"},
]

CIVIC = [
    {"gen": "ERBB2", "variante": "Amplification", "civic_id": 101, "descripcion": "Trastuzumab plus pertuzumab improved outcomes.",
     "nivel": "A", "significancia": "Sensitivity/Response", "direccion": "Supports", "enfermedad": "Her2-receptor Positive Breast Cancer",
     "terapias": ["trastuzumab", "pertuzumab"], "pmids": ["22149876"]},
    {"gen": "ERBB2", "variante": "Amplification", "civic_id": 102, "descripcion": "Lapatinib activity.",
     "nivel": "A", "significancia": "Sensitivity/Response", "direccion": "Supports", "enfermedad": "Breast Cancer",
     "terapias": ["lapatinib"], "pmids": []},
    {"gen": "PIK3CA", "variante": "H1047R", "civic_id": 103, "descripcion": "Alpelisib in PIK3CA-mutant disease.",
     "nivel": "B", "significancia": "Sensitivity/Response", "direccion": "Supports", "enfermedad": "Breast Cancer",
     "terapias": ["alpelisib"], "pmids": ["31091374"]},
]


def _ensayo(nct, titulo, intervenciones, subtipos, edad_min=18, edad_max=None, sexo="FEMALE"):
    return {"nct_id": nct, "titulo": titulo, "fases": ["PHASE3"], "estado": "RECRUITING", "condiciones": ["Breast Cancer"],
            "intervenciones": intervenciones, "url": f"https://clinicaltrials.gov/study/{nct}", "sexo": sexo,
            "edad_minima_anios": edad_min, "edad_maxima_anios": edad_max, "subtipos_relacionados": subtipos, "resumen": ""}


ENSAYOS = [
    _ensayo("NCT00000001", "Adjuvant ribociclib in early HR+ HER2- breast cancer", ["Ribociclib", "Letrozole"], ["RH_positivo_HER2_negativo"]),
    _ensayo("NCT00000002", "Elacestrant in advanced or metastatic breast cancer", ["Elacestrant"], ["RH_positivo_HER2_negativo"]),
    _ensayo("NCT00000003", "Neoadjuvant trastuzumab and pertuzumab in early HER2+ breast cancer", ["Trastuzumab", "Pertuzumab"], ["HER2_positivo"]),
    _ensayo("NCT00000004", "Adjuvant endocrine therapy in older women with early breast cancer", ["Letrozole"], [], edad_min=70),
]

REGISTRO_RT_0001 = {
    "id": "RT_0001", "edad": 61, "sexo": "Mujer", "topografia_codigo": "C50.9",
    "receptor_estrogeno": "(-)", "receptor_progesterona": "(-)", "her2": "(-)",
    "grado_diferenciacion": "Pobremente diferenciado", "estadio_clinico_t": "2", "estadio_clinico_n": "1",
    "estadio_clinico_m": "0", "estadio_clinico": "IIB", "ecog": "1-Sintomático pero ambulatorio",
}


class LectorFalso:
    def __init__(self, fallar_en: tuple[str, ...] = ()):
        self.fallar_en = fallar_en
        self.consultas: list[tuple[str, dict]] = []

    def __call__(self, cypher: str, params: dict | None = None) -> list[dict]:
        params = params or {}
        self.consultas.append((cypher, params))
        for clave in self.fallar_en:
            if clave in cypher:
                raise RuntimeError(f"fuente caída ({clave})")

        if "count(r) AS cantidad" in cypher:  # lo que genera el text-to-Cypher para un conteo
            return [{"cantidad": 25}]
        if "CALL db.labels()" in cypher or "CALL db.relationshipTypes()" in cypher:
            return []
        if "ProtocoloTratamiento" in cypher:
            if "subtipos" in params:
                return [p for p in PROTOCOLOS if p["subtipo"] in params["subtipos"]]
            return [p for p in PROTOCOLOS if p["subtipo"] == params.get("subtipo")]
        if "TIENE_EVIDENCIA" in cypher:
            return [dict(f) for f in CIVIC]
        if "EnsayoClinico" in cypher and "RegistroTumor" not in cypher:
            return [{"ensayo": dict(e)} for e in ENSAYOS]
        if "FrecuenciaGenCBio" in cypher and "genes" in params:
            return [{"gen": g, "tipo_alteracion": "AMP" if g == "ERBB2" else "MUT", "porcentaje": 12.5,
                     "n_alterados": 250, "n_perfilados": 2000, "estudio": "METABRIC", "study_id": "brca_metabric"}
                    for g in params["genes"]]
        if "VarianteClinVar" in cypher:
            return [{"gen": g, "clasificacion": "Pathogenic", "n": 40} for g in params.get("genes", [])]
        if "properties(r)" in cypher:
            return [{"r": dict(REGISTRO_RT_0001)}] if params.get("id") == "RT_0001" else []
        if "her2_dudoso" in cypher:
            return [{"total": 213, "her2_dudoso": 3}]
        if "n_estadio" in cypher:
            return [{"subtipo": s, "n": 40, "n_estadio": 6} for s in params.get("subtipos", [])]
        if "ActualizacionProtocolo" in cypher:
            return [{"id": "40000001", "titulo": "ESMO update", "resumen": "Resumen", "sociedad": "ESMO",
                     "fecha": "2026-05-01", "url": "https://example.org/esmo", "fuente": None}]
        if "literature_vectors" in cypher:
            return [{"id": "38000001", "texto": "Título del paper. Resumen del paper.", "drogas": "", "score": 0.81}]
        return []


class _Respuesta:
    def __init__(self, content: str):
        self.content = content


class LLMGuionado:
    """Devuelve siempre el mismo texto; registra los prompts recibidos."""

    def __init__(self, texto: str):
        self.texto = texto
        self.prompts: list[str] = []

    def invoke(self, prompt: str) -> _Respuesta:
        self.prompts.append(prompt)
        return _Respuesta(self.texto)
