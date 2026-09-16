"""
clinicaltrials_to_neo4j.py
Normaliza e ingesta en Neo4j los ensayos clinicos descargados por
app/bronze/clinicaltrials_explorer.py.

Crea un subgrafo independiente (:EnsayoClinico), sin relaciones hacia el subgrafo
clinico de pacientes ni hacia CIViC/ClinVar/cBioPortal (ver nota de independencia
de subgrafos en app/graph_db.py). Es un nodo "plano": condiciones e intervenciones
son propiedades de lista, no nodos propios, porque la API de ClinicalTrials.gov las
entrega como strings sueltos (sin id ni campos adicionales que ameriten un nodo
reutilizable, a diferencia de Enfermedad/Terapia en CIViC).

Ademas de los campos originales, normaliza el modulo de elegibilidad
(eligibilityModule) y deriva subtipos_relacionados -- estos campos son el
INSUMO de app/gold/reglas_elegibilidad_trials.py (Pharox_Documento_v5 §11
Capa 1), que sí crea relaciones explicitas hacia :RegistroTumor. Este script
sigue sin crearlas: solo deja el nodo :EnsayoClinico con todo lo necesario
para que la capa de reglas pueda evaluarlo.

Uso:
    python -m app.gold.clinicaltrials_to_neo4j
    python -m app.gold.clinicaltrials_to_neo4j data/bronze/clinicaltrials_recruiting_XXXX.json
"""
import os
import re
import sys
import json
import glob

from neo4j import GraphDatabase
from dotenv import load_dotenv

load_dotenv()

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "pharoxpass")

BRONZE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "bronze"
)

driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))


def crear_constraints(tx):
    tx.run("CREATE CONSTRAINT unique_ensayo_nct_id IF NOT EXISTS FOR (e:EnsayoClinico) REQUIRE e.nct_id IS UNIQUE")


def inicializar_esquema_ensayos():
    with driver.session() as session:
        session.execute_write(crear_constraints)


# Mismo vocabulario de 4 categorias que RegistroTumor.subtipo_molecular /
# ProtocoloTratamiento.subtipo_molecular_match / ActualizacionProtocolo.subtipos_detectados
# (ver app/gold/vigilancia_protocolos_to_neo4j.py). A diferencia de esas dos
# fuentes, ACA "sin señal clara" devuelve lista VACIA en vez de ["desconocido"]:
# un ensayo sin mencion de subtipo se interpreta como "no restringe por
# subtipo" (no excluye a nadie), no como "subtipo desconocido" -- son
# semanticas distintas a proposito, ver reglas_elegibilidad_trials.py.
_PATRONES_SUBTIPO_ENSAYO = {
    "Triple_negativo": ["triple-negative", "triple negative", "tnbc"],
    "HER2_positivo": ["her2-positive", "her2 positive", "her2+", "her2-positivo"],
}
_PATRONES_HR_POSITIVO = [
    "hormone receptor-positive", "hormone receptor positive", "hr-positive", "hr+",
    "estrogen receptor-positive", "estrogen receptor positive", "er-positive",
]
_PATRONES_HER2_NEGATIVO = ["her2-negative", "her2 negative", "her2-"]


def _detectar_subtipos_ensayo(texto_lower: str) -> list[str]:
    subtipos = {
        etiqueta for etiqueta, patrones in _PATRONES_SUBTIPO_ENSAYO.items()
        if any(p in texto_lower for p in patrones)
    }
    tiene_hr_positivo = any(p in texto_lower for p in _PATRONES_HR_POSITIVO)
    tiene_her2_negativo = any(p in texto_lower for p in _PATRONES_HER2_NEGATIVO)
    if tiene_hr_positivo and tiene_her2_negativo:
        subtipos.add("RH_positivo_HER2_negativo")
    return sorted(subtipos)


_EDAD_CON_UNIDAD = re.compile(r"^\s*(\d+)\s*Years?\s*$", re.IGNORECASE)


def _parsear_edad_anios(texto: str | None) -> int | None:
    """
    "18 Years" -> 18. Devuelve None para "N/A", ausente, o unidades que no
    sean años (Months/Weeks/Days) -- no intentamos convertir esas unidades,
    son irrelevantes para elegibilidad de pacientes adultas de mama y una
    conversion mal hecha seria peor que no comparar la edad.
    """
    if not texto:
        return None
    match = _EDAD_CON_UNIDAD.match(texto)
    return int(match.group(1)) if match else None


def normalizar_ensayo(study: dict) -> dict:
    """
    Aplana un registro crudo de ClinicalTrials.gov (protocolSection.*) a las
    propiedades planas del nodo :EnsayoClinico. Pura: sin red, sin Neo4j.
    """
    protocolo = study.get("protocolSection") or {}
    identificacion = protocolo.get("identificationModule") or {}
    estado_mod = protocolo.get("statusModule") or {}
    patrocinio = protocolo.get("sponsorCollaboratorsModule") or {}
    descripcion = protocolo.get("descriptionModule") or {}
    condiciones_mod = protocolo.get("conditionsModule") or {}
    diseno = protocolo.get("designModule") or {}
    intervenciones_mod = protocolo.get("armsInterventionsModule") or {}
    contactos = protocolo.get("contactsLocationsModule") or {}
    elegibilidad_mod = protocolo.get("eligibilityModule") or {}

    nct_id = identificacion.get("nctId")
    titulo = identificacion.get("briefTitle") or ""
    condiciones = condiciones_mod.get("conditions") or []
    criterios_elegibilidad = elegibilidad_mod.get("eligibilityCriteria") or ""

    intervenciones = [
        i.get("name") for i in (intervenciones_mod.get("interventions") or [])
        if i.get("name")
    ]
    paises = sorted(set(
        loc.get("country") for loc in (contactos.get("locations") or [])
        if loc.get("country")
    ))

    texto_subtipos = " ".join([titulo, *condiciones, criterios_elegibilidad]).lower()

    return {
        "nct_id": nct_id,
        "titulo": titulo,
        "fases": diseno.get("phases") or [],
        "estado": estado_mod.get("overallStatus") or "UNKNOWN",
        "condiciones": condiciones,
        "intervenciones": intervenciones,
        "sponsor": (patrocinio.get("leadSponsor") or {}).get("name") or "",
        "resumen": descripcion.get("briefSummary") or "",
        "paises": paises,
        "url": f"https://clinicaltrials.gov/study/{nct_id}" if nct_id else None,
        "criterios_elegibilidad": criterios_elegibilidad,
        "sexo": (elegibilidad_mod.get("sex") or "ALL").upper(),
        "edad_minima_anios": _parsear_edad_anios(elegibilidad_mod.get("minimumAge")),
        "edad_maxima_anios": _parsear_edad_anios(elegibilidad_mod.get("maximumAge")),
        "acepta_voluntarios_sanos": elegibilidad_mod.get("healthyVolunteers"),
        "subtipos_relacionados": _detectar_subtipos_ensayo(texto_subtipos),
    }


def ingestar_ensayo(tx, ensayo: dict):
    tx.run("""
        MERGE (e:EnsayoClinico {nct_id: $nct_id})
        ON CREATE SET
            e.titulo = $titulo, e.condiciones = $condiciones, e.intervenciones = $intervenciones,
            e.sponsor = $sponsor, e.resumen = $resumen, e.paises = $paises, e.url = $url,
            e.fases = $fases, e.estado = $estado,
            e.criterios_elegibilidad = $criterios_elegibilidad, e.sexo = $sexo,
            e.edad_minima_anios = $edad_minima_anios, e.edad_maxima_anios = $edad_maxima_anios,
            e.acepta_voluntarios_sanos = $acepta_voluntarios_sanos, e.subtipos_relacionados = $subtipos_relacionados
        ON MATCH SET
            e.fases = $fases, e.estado = $estado,
            e.criterios_elegibilidad = $criterios_elegibilidad, e.sexo = $sexo,
            e.edad_minima_anios = $edad_minima_anios, e.edad_maxima_anios = $edad_maxima_anios,
            e.acepta_voluntarios_sanos = $acepta_voluntarios_sanos, e.subtipos_relacionados = $subtipos_relacionados
    """, ensayo)


def procesar_archivo_clinicaltrials(ruta: str) -> int:
    """Normaliza e ingesta un archivo bronze de ClinicalTrials.gov. Devuelve cuantos ensayos proceso."""
    with open(ruta, "r", encoding="utf-8") as f:
        estudios = json.load(f)

    procesados = 0
    with driver.session() as session:
        for study in estudios:
            ensayo = normalizar_ensayo(study)
            if not ensayo["nct_id"]:
                continue
            session.execute_write(ingestar_ensayo, ensayo)
            procesados += 1

    print(f"[{os.path.basename(ruta)}] {procesados} ensayo(s) ingestado(s)/actualizado(s).")
    return procesados


def main():
    inicializar_esquema_ensayos()

    if len(sys.argv) > 1:
        archivos = sys.argv[1:]
    else:
        archivos = sorted(glob.glob(os.path.join(BRONZE_DIR, "clinicaltrials_*.json")))

    if not archivos:
        print(f"No se encontraron archivos clinicaltrials_*.json en {BRONZE_DIR}")
        return

    total = 0
    for ruta in archivos:
        total += procesar_archivo_clinicaltrials(ruta)

    print(f"\nTotal de ensayos ingestados en esta corrida: {total}")


if __name__ == "__main__":
    main()
