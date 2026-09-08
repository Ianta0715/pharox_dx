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

Uso:
    python -m app.gold.clinicaltrials_to_neo4j
    python -m app.gold.clinicaltrials_to_neo4j data/bronze/clinicaltrials_recruiting_XXXX.json
"""
import os
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

    nct_id = identificacion.get("nctId")

    intervenciones = [
        i.get("name") for i in (intervenciones_mod.get("interventions") or [])
        if i.get("name")
    ]
    paises = sorted(set(
        loc.get("country") for loc in (contactos.get("locations") or [])
        if loc.get("country")
    ))

    return {
        "nct_id": nct_id,
        "titulo": identificacion.get("briefTitle") or "",
        "fases": diseno.get("phases") or [],
        "estado": estado_mod.get("overallStatus") or "UNKNOWN",
        "condiciones": condiciones_mod.get("conditions") or [],
        "intervenciones": intervenciones,
        "sponsor": (patrocinio.get("leadSponsor") or {}).get("name") or "",
        "resumen": descripcion.get("briefSummary") or "",
        "paises": paises,
        "url": f"https://clinicaltrials.gov/study/{nct_id}" if nct_id else None,
    }


def ingestar_ensayo(tx, ensayo: dict):
    tx.run("""
        MERGE (e:EnsayoClinico {nct_id: $nct_id})
        ON CREATE SET
            e.titulo = $titulo, e.condiciones = $condiciones, e.intervenciones = $intervenciones,
            e.sponsor = $sponsor, e.resumen = $resumen, e.paises = $paises, e.url = $url,
            e.fases = $fases, e.estado = $estado
        ON MATCH SET
            e.fases = $fases, e.estado = $estado
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
