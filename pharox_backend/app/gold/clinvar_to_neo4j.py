"""
clinvar_to_neo4j.py
Normaliza e ingesta en Neo4j las variantes descargadas por app/bronze/clinvar_explorer.py.

Crea un subgrafo independiente (:VarianteClinVar)-[:ASOCIADA_A_CONDICION]->(:CondicionClinVar),
sin relaciones hacia el subgrafo clinico de pacientes ni hacia CIViC/ClinicalTrials/cBioPortal.

CondicionClinVar se identifica por nombre (trait_name), no por MedGen CUI: en la
respuesta real de ClinVar hay traits sin ningun trait_xref (por lo tanto sin MedGen
id) pero siempre con trait_name -- misma leccion que CIViC ya aplico con civic_id
(nunca usar como key algo que puede venir null).

Cada variante puede tener tres bloques de clasificacion (germline_classification,
clinical_impact_classification, oncogenicity_classification); se usa el primero
que tenga descripcion no vacia, en ese orden de prioridad (la mayoria de las
variantes de cancer de mama hereditario -BRCA1/2, PALB2, ATM, CHEK2- solo
completan germline_classification).

Uso:
    python -m app.gold.clinvar_to_neo4j
    python -m app.gold.clinvar_to_neo4j data/bronze/clinvar_BRCA1_XXXX.json
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

ORDEN_CLASIFICACIONES = ["germline_classification", "oncogenicity_classification", "clinical_impact_classification"]


def crear_constraints(tx):
    tx.run("CREATE CONSTRAINT unique_variante_clinvar_variation_id IF NOT EXISTS FOR (v:VarianteClinVar) REQUIRE v.variation_id IS UNIQUE")
    tx.run("CREATE CONSTRAINT unique_condicion_clinvar_nombre IF NOT EXISTS FOR (c:CondicionClinVar) REQUIRE c.nombre IS UNIQUE")


def inicializar_esquema_clinvar():
    with driver.session() as session:
        session.execute_write(crear_constraints)


def _elegir_clasificacion(summary: dict) -> dict:
    """Devuelve el primer bloque de clasificacion con descripcion no vacia, o uno vacio si ninguno la tiene."""
    for clave in ORDEN_CLASIFICACIONES:
        bloque = summary.get(clave) or {}
        if bloque.get("description"):
            return bloque
    return {}


def _elegir_ubicacion(variation_set: list[dict]) -> dict:
    """Prefiere la ubicacion marcada status='current' (hoy GRCh38); si no hay, la primera disponible."""
    locs = (variation_set[0].get("variation_loc") if variation_set else None) or []
    actual = next((l for l in locs if l.get("status") == "current"), None)
    return actual or (locs[0] if locs else {})


def normalizar_variante_clinvar(summary: dict) -> dict:
    """
    Aplana un resumen crudo de esummary (ClinVar) a las propiedades planas del
    nodo :VarianteClinVar. Pura: sin red, sin Neo4j.
    """
    variation_set = summary.get("variation_set") or []
    primera_variante = variation_set[0] if variation_set else {}
    genes = summary.get("genes") or []
    clasificacion = _elegir_clasificacion(summary)
    ubicacion = _elegir_ubicacion(variation_set)

    variation_id = summary.get("uid")

    return {
        "variation_id": int(variation_id) if variation_id else None,
        "nombre": summary.get("title") or "",
        "gen": genes[0].get("symbol") if genes else None,
        "tipo_variante": summary.get("obj_type") or "",
        "hgvs_c": primera_variante.get("cdna_change") or "",
        "hgvs_p": summary.get("protein_change") or "",
        "assembly": ubicacion.get("assembly_name") or "",
        "cromosoma": ubicacion.get("chr") or "",
        "posicion": ubicacion.get("display_start") or "",
        "clasificacion_clinica": clasificacion.get("description") or "No clasificada",
        "review_status": clasificacion.get("review_status") or "",
        "ultima_evaluacion": clasificacion.get("last_evaluated") or "",
    }


def normalizar_condicion_clinvar(trait: dict) -> dict | None:
    """Aplana un trait de ClinVar a las propiedades de :CondicionClinVar. None si no trae nombre."""
    nombre = trait.get("trait_name")
    if not nombre:
        return None
    medgen_id = next(
        (x.get("db_id") for x in (trait.get("trait_xrefs") or []) if x.get("db_source") == "MedGen"),
        None,
    )
    return {"nombre": nombre, "medgen_id": medgen_id}


def ingestar_variante(tx, variante: dict):
    tx.run("""
        MERGE (v:VarianteClinVar {variation_id: $variation_id})
        ON CREATE SET
            v.nombre = $nombre, v.gen = $gen, v.tipo_variante = $tipo_variante,
            v.hgvs_c = $hgvs_c, v.hgvs_p = $hgvs_p, v.assembly = $assembly,
            v.cromosoma = $cromosoma, v.posicion = $posicion,
            v.clasificacion_clinica = $clasificacion_clinica,
            v.review_status = $review_status, v.ultima_evaluacion = $ultima_evaluacion
        ON MATCH SET
            v.clasificacion_clinica = $clasificacion_clinica,
            v.review_status = $review_status, v.ultima_evaluacion = $ultima_evaluacion
    """, variante)


def ingestar_condicion(tx, variation_id: int, condicion: dict):
    tx.run("""
        MATCH (v:VarianteClinVar {variation_id: $variation_id})
        MERGE (c:CondicionClinVar {nombre: $nombre})
        ON CREATE SET c.medgen_id = $medgen_id
        MERGE (v)-[:ASOCIADA_A_CONDICION]->(c)
    """, {"variation_id": variation_id, **condicion})


def procesar_archivo_clinvar(ruta: str) -> int:
    """Normaliza e ingesta un archivo bronze de ClinVar. Devuelve cuantas variantes proceso."""
    with open(ruta, "r", encoding="utf-8") as f:
        resumenes = json.load(f)

    procesadas = 0
    with driver.session() as session:
        for summary in resumenes:
            variante = normalizar_variante_clinvar(summary)
            if not variante["variation_id"]:
                continue
            session.execute_write(ingestar_variante, variante)

            clasificacion = _elegir_clasificacion(summary)
            for trait in clasificacion.get("trait_set") or []:
                condicion = normalizar_condicion_clinvar(trait)
                if condicion:
                    session.execute_write(ingestar_condicion, variante["variation_id"], condicion)

            procesadas += 1

    print(f"[{os.path.basename(ruta)}] {procesadas} variante(s) ingestada(s)/actualizada(s).")
    return procesadas


def main():
    inicializar_esquema_clinvar()

    if len(sys.argv) > 1:
        archivos = sys.argv[1:]
    else:
        archivos = sorted(glob.glob(os.path.join(BRONZE_DIR, "clinvar_*.json")))

    if not archivos:
        print(f"No se encontraron archivos clinvar_*.json en {BRONZE_DIR}")
        return

    total = 0
    for ruta in archivos:
        total += procesar_archivo_clinvar(ruta)

    print(f"\nTotal de variantes ingestadas en esta corrida: {total}")


if __name__ == "__main__":
    main()
