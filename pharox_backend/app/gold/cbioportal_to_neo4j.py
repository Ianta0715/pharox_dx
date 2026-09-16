"""
cbioportal_to_neo4j.py
Normaliza e ingesta en Neo4j la frecuencia de alteracion por gen descargada por
app/bronze/cbioportal_explorer.py.

Alcance deliberadamente acotado a frecuencia AGREGADA por (estudio, gen, tipo de
alteracion) -- no a mutaciones/CNA por paciente individual: responde "que tan
frecuente es esta alteracion en una cohorte real de mama" con un volumen de datos
manejable, sin la complejidad ni el volumen (decenas de miles de filas por
paciente) de bajar llamadas variante-por-variante.

Crea un subgrafo independiente:
    (:EstudioCBio)-[:REPORTA_FRECUENCIA]->(:FrecuenciaGenCBio)-[:SOBRE_GEN]->(:GenCBio)
FrecuenciaGenCBio existe como nodo intermedio (no como propiedades de la relacion)
porque Neo4j sin APOC en este proyecto no expone propiedades de relacion al LLM de
text-to-Cypher -- mismo criterio que Evidencia en CIViC.

frecuencia_id es una key sintetica ("{study_id}_{entrez_id}_{tipo_alteracion}")
porque cBioPortal no da un id nativo para este hecho agregado (a diferencia de
civic_id/variation_id en las otras fuentes).

La ingesta usa UNWIND por lotes (no un MERGE por fila) porque el CNA de un solo
estudio grande (ej. METABRIC) trae decenas de miles de filas -- un execute_write
por fila ahi si seria un problema real de performance, a diferencia de las otras
3 fuentes (cientos/miles de filas como mucho).

Uso:
    python -m app.gold.cbioportal_to_neo4j
    python -m app.gold.cbioportal_to_neo4j data/bronze/cbioportal_XXXX.json
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

LOTE_INGESTA = 500  # filas de alteracion por transaccion (UNWIND), no una por row


def crear_constraints(tx):
    tx.run("CREATE CONSTRAINT unique_estudio_cbio_study_id IF NOT EXISTS FOR (e:EstudioCBio) REQUIRE e.study_id IS UNIQUE")
    tx.run("CREATE CONSTRAINT unique_gen_cbio_entrez_id IF NOT EXISTS FOR (g:GenCBio) REQUIRE g.entrez_id IS UNIQUE")
    tx.run("CREATE CONSTRAINT unique_frecuencia_cbio_id IF NOT EXISTS FOR (f:FrecuenciaGenCBio) REQUIRE f.frecuencia_id IS UNIQUE")


def inicializar_esquema_cbioportal():
    with driver.session() as session:
        session.execute_write(crear_constraints)


def normalizar_estudio(study_info: dict) -> dict:
    """Aplana la respuesta de GET /api/studies/{studyId}. Pura: sin red, sin Neo4j."""
    return {
        "study_id": study_info.get("studyId"),
        "nombre": study_info.get("name") or "",
        "descripcion": study_info.get("description") or "",
        "n_pacientes": study_info.get("allSampleCount") or 0,
    }


def normalizar_frecuencia_gen(row: dict, study_id: str) -> dict:
    """
    Aplana una fila de AlterationCountByGene (mutated-genes/fetch o cna-genes/fetch,
    ya tageada con tipo_alteracion en bronze) a las propiedades de :FrecuenciaGenCBio
    + :GenCBio. Pura: sin red, sin Neo4j. Calcula el id sintetico frecuencia_id.
    """
    entrez_id = row.get("entrezGeneId")
    tipo_alteracion = row.get("tipo_alteracion") or "DESCONOCIDA"
    n_alterados = row.get("numberOfAlteredCases") or 0
    n_perfilados = row.get("numberOfProfiledCases") or 0
    porcentaje = round((n_alterados / n_perfilados) * 100, 2) if n_perfilados else 0.0

    return {
        "frecuencia_id": f"{study_id}_{entrez_id}_{tipo_alteracion}",
        "entrez_id": entrez_id,
        "hugo_symbol": row.get("hugoGeneSymbol") or "",
        "n_alterados": n_alterados,
        "n_perfilados": n_perfilados,
        "porcentaje": porcentaje,
        "tipo_alteracion": tipo_alteracion,
    }


def ingestar_estudio(tx, estudio: dict):
    tx.run("""
        MERGE (e:EstudioCBio {study_id: $study_id})
        ON CREATE SET e.nombre = $nombre, e.descripcion = $descripcion, e.n_pacientes = $n_pacientes
    """, estudio)


def ingestar_lote_frecuencias(tx, study_id: str, filas: list[dict]):
    tx.run("""
        UNWIND $filas AS fila
        MATCH (est:EstudioCBio {study_id: $study_id})
        MERGE (g:GenCBio {entrez_id: fila.entrez_id})
        ON CREATE SET g.hugo_symbol = fila.hugo_symbol
        MERGE (f:FrecuenciaGenCBio {frecuencia_id: fila.frecuencia_id})
        ON CREATE SET f.n_alterados = fila.n_alterados, f.n_perfilados = fila.n_perfilados,
                      f.porcentaje = fila.porcentaje, f.tipo_alteracion = fila.tipo_alteracion
        ON MATCH SET f.n_alterados = fila.n_alterados, f.n_perfilados = fila.n_perfilados,
                     f.porcentaje = fila.porcentaje
        MERGE (est)-[:REPORTA_FRECUENCIA]->(f)
        MERGE (f)-[:SOBRE_GEN]->(g)
    """, {"study_id": study_id, "filas": filas})


def procesar_archivo_cbioportal(ruta: str) -> int:
    """Normaliza e ingesta un archivo bronze de cBioPortal. Devuelve cuantas filas de frecuencia proceso."""
    with open(ruta, "r", encoding="utf-8") as f:
        estudios_crudos = json.load(f)

    total_filas = 0
    with driver.session() as session:
        for estudio_crudo in estudios_crudos:
            study_id = estudio_crudo["study_id"]
            estudio = normalizar_estudio(estudio_crudo.get("study_info") or {})
            if not estudio["study_id"]:
                estudio["study_id"] = study_id
            session.execute_write(ingestar_estudio, estudio)

            filas = [
                normalizar_frecuencia_gen(row, study_id)
                for row in estudio_crudo.get("alteraciones", [])
                if row.get("entrezGeneId")
            ]
            for i in range(0, len(filas), LOTE_INGESTA):
                lote = filas[i:i + LOTE_INGESTA]
                session.execute_write(ingestar_lote_frecuencias, study_id, lote)
                total_filas += len(lote)

            print(f"[{study_id}] {len(filas)} fila(s) de frecuencia ingestada(s)/actualizada(s).")

    return total_filas


def main():
    inicializar_esquema_cbioportal()

    if len(sys.argv) > 1:
        archivos = sys.argv[1:]
    else:
        archivos = sorted(glob.glob(os.path.join(BRONZE_DIR, "cbioportal_*.json")))

    if not archivos:
        print(f"No se encontraron archivos cbioportal_*.json en {BRONZE_DIR}")
        return

    total = 0
    for ruta in archivos:
        total += procesar_archivo_cbioportal(ruta)

    print(f"\nTotal de filas de frecuencia ingestadas en esta corrida: {total}")


if __name__ == "__main__":
    main()
