"""
europepmc_to_neo4j.py
Normaliza e ingesta en Neo4j los articulos descargados por app/bronze/europepmc_explorer.py.

A diferencia de las otras 3 fuentes nuevas, este script NO crea un subgrafo propio:
repuebla la etiqueta :Literatura que ya existe (indice vectorial literature_vectors,
ya cableada en el paso 1 de buscar_contexto_hibrido en app/graph_db.py). La unica
constraint que hacia falta (unique_literatura_id) se agrego directamente en
graph_db.py::inicializar_esquema, porque Literatura ya es responsabilidad de ese
modulo, no de un subgrafo independiente (ver nota en graph_db.py).

Usa MERGE por id=PMID (no CREATE): a diferencia del seed sintetico de
graph_db.py::inicializar_db (que corre una sola vez si el grafo esta vacio), este
script se puede correr repetidamente -- esa es la idea de "incorporar evidencia
nueva automaticamente" del usuario.

tipo_cancer se escribe con el MISMO literal exacto que usa el resto del proyecto
("Cáncer de Mama", ver main.py) para que el filtro de buscar_contexto_hibrido lo
encuentre incluso sin el fix de normalizacion (defensa en profundidad).

IMPORTANTE: a diferencia de los otros 3 gold scripts (solo necesitan Neo4j), este
necesita Ollama corriendo (genera embeddings via app.ai_gateway.generar_embedding,
modelo nomic-embed-text) -- no se pudo verificar end-to-end en esta maquina porque
no tiene GPU/Ollama levantado (ver conversacion); confirmar en la maquina con GPU.

Uso:
    python -m app.bronze.europepmc_explorer && python -m app.gold.europepmc_to_neo4j
    python -m app.gold.europepmc_to_neo4j data/bronze/europepmc_XXXX.json
"""
import os
import re
import sys
import json
import glob
import html

from neo4j import GraphDatabase
from dotenv import load_dotenv

from app.ai_gateway import generar_embedding

load_dotenv()

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "pharoxpass")

BRONZE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "bronze"
)

driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

TIPO_CANCER_MAMA = "Cáncer de Mama"

# Vocabulario de entidades de mama que el usuario pidio seguir explicitamente.
# Orden intencional: patrones mas especificos antes que sus abreviaturas genericas.
ENTIDADES_CATEGORIA = {
    "HER2": ["her2", "erbb2"],
    "TNBC": ["tnbc", "triple-negative", "triple negative"],
    "HR+": ["hr+", "hormone receptor-positive", "hormone receptor positive", "estrogen receptor-positive"],
    "ESR1": ["esr1"],
    "BRCA": ["brca1", "brca2", "brca"],
    "CDK4/6": ["cdk4", "cdk6", "cdk4/6"],
    "ADC": ["antibody-drug conjugate", "antibody drug conjugate", "trastuzumab deruxtecan", "sacituzumab govitecan", "trastuzumab emtansine"],
}

DROGAS_CONOCIDAS = [
    "tamoxifeno", "tamoxifen", "trastuzumab deruxtecan", "trastuzumab emtansine", "trastuzumab",
    "pertuzumab", "palbociclib", "ribociclib", "abemaciclib", "letrozol", "letrozole",
    "anastrozol", "anastrozole", "exemestano", "exemestane", "fulvestrant", "olaparib",
    "talazoparib", "sacituzumab govitecan", "capecitabine", "docetaxel", "paclitaxel",
    "doxorubicin", "ciclofosfamida", "cyclophosphamide", "pembrolizumab", "atezolizumab",
    "everolimus", "elacestrant",
]

_TAG_HTML = re.compile(r"<[^>]+>")


def _limpiar_html(texto: str) -> str:
    """
    Des-escapa entidades HTML y saca tags. En ese orden: Europe PMC a veces manda
    los tags ya escapados (ej. titulo "HER2&lt;sup&gt;+&lt;/sup&gt;"), asi que si
    se sacan tags antes de des-escapar, esos tags escapados pasan de largo.
    """
    if not texto:
        return ""
    return _TAG_HTML.sub(" ", html.unescape(texto)).strip()


def _detectar_coincidencias(texto_lower: str, vocabulario) -> list[str]:
    if isinstance(vocabulario, dict):
        return [etiqueta for etiqueta, patrones in vocabulario.items() if any(p in texto_lower for p in patrones)]
    return [termino for termino in vocabulario if termino in texto_lower]


def normalizar_articulo(articulo: dict) -> dict:
    """
    Aplana un resultado crudo de Europe PMC a las propiedades planas del nodo
    :Literatura (sin el embedding, que requiere Ollama y se calcula en la
    ingesta). Pura: sin red, sin Neo4j, sin Ollama.
    """
    pmid = articulo.get("pmid") or articulo.get("id") or ""
    titulo = _limpiar_html(articulo.get("title") or "")
    resumen = _limpiar_html(articulo.get("abstractText") or "")

    mesh_terms = [
        m.get("descriptorName", "")
        for m in (articulo.get("meshHeadingList") or {}).get("meshHeading", [])
    ]
    texto_busqueda = " ".join([titulo, resumen, " ".join(mesh_terms)]).lower()

    categorias = _detectar_coincidencias(texto_busqueda, ENTIDADES_CATEGORIA)
    drogas = _detectar_coincidencias(texto_busqueda, DROGAS_CONOCIDAS)

    return {
        "id": pmid,
        "text": f"{titulo}. {resumen}".strip(". "),
        "tipo_cancer": TIPO_CANCER_MAMA,
        "categoria": ", ".join(categorias) if categorias else "General",
        "drogas": ", ".join(sorted(set(drogas))),
    }


def ingestar_articulo(tx, articulo: dict, embedding: list[float]):
    tx.run("""
        MERGE (l:Literatura {id: $id})
        ON CREATE SET
            l.text = $text, l.tipo_cancer = $tipo_cancer,
            l.categoria = $categoria, l.drogas = $drogas, l.embedding = $embedding
    """, {**articulo, "embedding": embedding})


def procesar_archivo_europepmc(ruta: str) -> int:
    """Normaliza, genera embeddings e ingesta un archivo bronze de Europe PMC. Devuelve cuantos articulos proceso."""
    with open(ruta, "r", encoding="utf-8") as f:
        articulos_crudos = json.load(f)

    procesados = 0
    with driver.session() as session:
        for crudo in articulos_crudos:
            articulo = normalizar_articulo(crudo)
            if not articulo["id"] or not articulo["text"].strip("."):
                continue
            print(f"[Europe PMC] Generando embedding para PMID {articulo['id']}...")
            embedding = generar_embedding(articulo["text"])
            session.execute_write(ingestar_articulo, articulo, embedding)
            procesados += 1

    print(f"[{os.path.basename(ruta)}] {procesados} articulo(s) ingestado(s)/actualizado(s).")
    return procesados


def main():
    if len(sys.argv) > 1:
        archivos = sys.argv[1:]
    else:
        archivos = sorted(glob.glob(os.path.join(BRONZE_DIR, "europepmc_*.json")))

    if not archivos:
        print(f"No se encontraron archivos europepmc_*.json en {BRONZE_DIR}")
        return

    total = 0
    for ruta in archivos:
        total += procesar_archivo_europepmc(ruta)

    print(f"\nTotal de articulos ingestados en esta corrida: {total}")


if __name__ == "__main__":
    main()
