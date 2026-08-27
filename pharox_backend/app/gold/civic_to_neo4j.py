"""
civic_to_neo4j.py
Capa Gold: normaliza el JSON crudo de CIViC (Capa Bronze, generado por
app/bronze/civic_explorer.py) e ingesta el mapa clinico molecular en Neo4j
como un grafo de conocimiento persistente.

Modelo de grafo:
    (Variante)-[:TIENE_EVIDENCIA]->(Evidencia)
    (Evidencia)-[:ASOCIADA_A_ENFERMEDAD]->(Enfermedad)
    (Evidencia)-[:INVOLUCRA_TERAPIA]->(Terapia)
    (Evidencia)-[:RESPALDADA_POR]->(Fuente)

Nota de diseno: las claves de unicidad usan el id interno de CIViC
(civic_id) para cada entidad en lugar de identificadores externos
(DOID / NCIt ID / PubMed ID), porque esos campos pueden venir null
en algunos registros de CIViC y un MERGE por una propiedad null no
garantiza deduplicacion. Los identificadores externos se conservan
como propiedades del nodo para referencia cruzada.

Uso:
    python -m app.gold.civic_to_neo4j pharox_backend/data/bronze/civic_BRAF_V600E_*.json
    python -m app.gold.civic_to_neo4j            # procesa todos los civic_*.json de data/bronze
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


# ---------------------------------------------------------------------------
# ESQUEMA: restricciones de unicidad para evitar duplicados
# ---------------------------------------------------------------------------
def crear_constraints(tx):
    """Crea las restricciones de unicidad para las entidades del grafo de CIViC."""
    tx.run("CREATE CONSTRAINT unique_variante_civic_id IF NOT EXISTS "
           "FOR (v:Variante) REQUIRE v.civic_id IS UNIQUE")
    tx.run("CREATE CONSTRAINT unique_evidencia_civic_id IF NOT EXISTS "
           "FOR (e:Evidencia) REQUIRE e.civic_id IS UNIQUE")
    tx.run("CREATE CONSTRAINT unique_enfermedad_civic_id IF NOT EXISTS "
           "FOR (en:Enfermedad) REQUIRE en.civic_id IS UNIQUE")
    tx.run("CREATE CONSTRAINT unique_terapia_civic_id IF NOT EXISTS "
           "FOR (t:Terapia) REQUIRE t.civic_id IS UNIQUE")
    tx.run("CREATE CONSTRAINT unique_fuente_civic_id IF NOT EXISTS "
           "FOR (f:Fuente) REQUIRE f.civic_id IS UNIQUE")


def inicializar_esquema_civic():
    """Inicializa las restricciones de unicidad en Neo4j (idempotente)."""
    with driver.session() as session:
        session.execute_write(crear_constraints)


# ---------------------------------------------------------------------------
# NORMALIZACION: JSON crudo de CIViC -> estructuras planas para Cypher
# ---------------------------------------------------------------------------
def normalizar_variante(data: dict) -> dict:
    """Extrae los campos de la Variante y su Perfil Molecular desde el JSON de CIViC."""
    feature = data.get("feature") or {}
    gene = feature.get("featureInstance") or {}
    perfil = data.get("singleVariantMolecularProfile") or {}

    return {
        "civic_id": data["id"],
        "nombre": f"{feature.get('name', '')} {data.get('name', '')}".strip(),
        "nombre_variante": data.get("name"),
        "gen": feature.get("name"),
        "gene_entrez_id": gene.get("entrezId"),
        "link": f"https://civicdb.org{data.get('link', '')}",
        "deprecada": bool(data.get("deprecated", False)),
        "tipos_so": [t["name"] for t in (data.get("variantTypes") or [])],
        "perfil_molecular_id": perfil.get("id"),
        "perfil_molecular_nombre": perfil.get("name"),
        "perfil_molecular_score": perfil.get("molecularProfileScore"),
        "perfil_molecular_descripcion": perfil.get("description"),
    }


def normalizar_evidencia(ev: dict) -> dict:
    """Extrae los campos planos de una Evidencia Clinica desde el JSON de CIViC."""
    return {
        "civic_id": ev["id"],
        "nombre": ev.get("name"),
        "descripcion": ev.get("description"),
        "tipo": ev.get("evidenceType"),
        "direccion": ev.get("evidenceDirection"),
        "nivel": ev.get("evidenceLevel"),
        "rating": ev.get("evidenceRating"),
        "significancia": ev.get("significance"),
        "estado": ev.get("status"),
        "origen": ev.get("variantOrigin"),
        "interaccion_terapia": ev.get("therapyInteractionType"),
    }


def normalizar_enfermedad(disease: dict) -> dict:
    """Extrae los campos planos de una Enfermedad. Se llama solo si disease no es None."""
    return {
        "civic_id": disease["id"],
        "nombre": disease.get("name"),
        "nombre_mostrado": disease.get("displayName"),
        "doid": disease.get("doid"),
    }


def normalizar_terapia(therapy: dict) -> dict:
    """Extrae los campos planos de una Terapia. Se llama por cada item de la lista therapies."""
    return {
        "civic_id": therapy["id"],
        "nombre": therapy.get("name"),
        "ncit_id": therapy.get("ncitId"),
    }


def normalizar_fuente(source: dict) -> dict:
    """Extrae los campos planos de una Fuente bibliografica. source siempre viene presente en CIViC."""
    return {
        "civic_id": source["id"],
        "pubmed_id": source.get("citationId"),
        "tipo_fuente": source.get("sourceType"),
        "cita": source.get("citation"),
        "anio": source.get("publicationYear"),
        "journal": source.get("journal"),
        "url": source.get("sourceUrl"),
    }


# ---------------------------------------------------------------------------
# INGESTA: MERGE de nodos y relaciones en Neo4j
# ---------------------------------------------------------------------------
def ingestar_variante(tx, variante: dict):
    """Crea/actualiza el nodo Variante con su Perfil Molecular."""
    tx.run("""
        MERGE (v:Variante {civic_id: $civic_id})
        ON CREATE SET
            v.nombre = $nombre,
            v.nombre_variante = $nombre_variante,
            v.gen = $gen,
            v.gene_entrez_id = $gene_entrez_id,
            v.link = $link,
            v.deprecada = $deprecada,
            v.tipos_so = $tipos_so,
            v.perfil_molecular_id = $perfil_molecular_id,
            v.perfil_molecular_nombre = $perfil_molecular_nombre,
            v.perfil_molecular_score = $perfil_molecular_score,
            v.perfil_molecular_descripcion = $perfil_molecular_descripcion
        ON MATCH SET
            v.perfil_molecular_score = $perfil_molecular_score
    """, variante)


def ingestar_evidencia(tx, variante_civic_id: int, evidencia: dict):
    """Crea/actualiza el nodo Evidencia y lo enlaza a su Variante."""
    tx.run("""
        MATCH (v:Variante {civic_id: $variante_civic_id})
        MERGE (e:Evidencia {civic_id: $civic_id})
        ON CREATE SET
            e.nombre = $nombre,
            e.descripcion = $descripcion,
            e.tipo = $tipo,
            e.direccion = $direccion,
            e.nivel = $nivel,
            e.rating = $rating,
            e.significancia = $significancia,
            e.estado = $estado,
            e.origen = $origen,
            e.interaccion_terapia = $interaccion_terapia
        MERGE (v)-[:TIENE_EVIDENCIA]->(e)
    """, {**evidencia, "variante_civic_id": variante_civic_id})


def ingestar_enfermedad(tx, evidencia_civic_id: int, enfermedad: dict):
    """Crea/actualiza el nodo Enfermedad y lo enlaza a su Evidencia."""
    tx.run("""
        MATCH (e:Evidencia {civic_id: $evidencia_civic_id})
        MERGE (en:Enfermedad {civic_id: $civic_id})
        ON CREATE SET
            en.nombre = $nombre,
            en.nombre_mostrado = $nombre_mostrado,
            en.doid = $doid
        MERGE (e)-[:ASOCIADA_A_ENFERMEDAD]->(en)
    """, {**enfermedad, "evidencia_civic_id": evidencia_civic_id})


def ingestar_terapia(tx, evidencia_civic_id: int, terapia: dict):
    """Crea/actualiza un nodo Terapia y lo enlaza a su Evidencia."""
    tx.run("""
        MATCH (e:Evidencia {civic_id: $evidencia_civic_id})
        MERGE (t:Terapia {civic_id: $civic_id})
        ON CREATE SET
            t.nombre = $nombre,
            t.ncit_id = $ncit_id
        MERGE (e)-[:INVOLUCRA_TERAPIA]->(t)
    """, {**terapia, "evidencia_civic_id": evidencia_civic_id})


def ingestar_fuente(tx, evidencia_civic_id: int, fuente: dict):
    """Crea/actualiza el nodo Fuente y lo enlaza a su Evidencia."""
    tx.run("""
        MATCH (e:Evidencia {civic_id: $evidencia_civic_id})
        MERGE (f:Fuente {civic_id: $civic_id})
        ON CREATE SET
            f.pubmed_id = $pubmed_id,
            f.tipo_fuente = $tipo_fuente,
            f.cita = $cita,
            f.anio = $anio,
            f.journal = $journal,
            f.url = $url
        MERGE (e)-[:RESPALDADA_POR]->(f)
    """, {**fuente, "evidencia_civic_id": evidencia_civic_id})


# ---------------------------------------------------------------------------
# ORQUESTACION
# ---------------------------------------------------------------------------
def procesar_archivo_civic(ruta_json: str) -> dict:
    """
    Lee un JSON crudo de CIViC (Capa Bronze) e ingesta su variante,
    evidencias, enfermedades, terapias y fuentes en Neo4j.
    Maneja de forma segura evidencias sin enfermedad o sin terapias asociadas
    (comun en evidencias de tipo diagnostico o pronostico).
    """
    with open(ruta_json, "r", encoding="utf-8") as f:
        data = json.load(f)

    variante = normalizar_variante(data)
    perfil = data.get("singleVariantMolecularProfile") or {}
    evidencias_raw = (perfil.get("evidenceItems") or {}).get("nodes") or []

    contadores = {"evidencias": 0, "enfermedades": 0, "terapias": 0, "fuentes": 0}

    with driver.session() as session:
        session.execute_write(ingestar_variante, variante)

        for ev_raw in evidencias_raw:
            evidencia = normalizar_evidencia(ev_raw)
            session.execute_write(ingestar_evidencia, variante["civic_id"], evidencia)
            contadores["evidencias"] += 1

            disease = ev_raw.get("disease")
            if disease:
                enfermedad = normalizar_enfermedad(disease)
                session.execute_write(ingestar_enfermedad, evidencia["civic_id"], enfermedad)
                contadores["enfermedades"] += 1

            for therapy in (ev_raw.get("therapies") or []):
                terapia = normalizar_terapia(therapy)
                session.execute_write(ingestar_terapia, evidencia["civic_id"], terapia)
                contadores["terapias"] += 1

            source = ev_raw.get("source")
            if source:
                fuente = normalizar_fuente(source)
                session.execute_write(ingestar_fuente, evidencia["civic_id"], fuente)
                contadores["fuentes"] += 1

    print(
        f"[{os.path.basename(ruta_json)}] {variante['nombre']}: "
        f"{contadores['evidencias']} evidencias, "
        f"{contadores['enfermedades']} enfermedades, "
        f"{contadores['terapias']} terapias, "
        f"{contadores['fuentes']} fuentes ingestadas."
    )
    return contadores


def main():
    inicializar_esquema_civic()

    rutas = sys.argv[1:]
    if not rutas:
        rutas = sorted(glob.glob(os.path.join(BRONZE_DIR, "civic_*.json")))

    if not rutas:
        print(f"No se encontraron archivos civic_*.json en {BRONZE_DIR}")
        return

    for ruta in rutas:
        procesar_archivo_civic(ruta)


if __name__ == "__main__":
    main()
