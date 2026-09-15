"""
vigilancia_protocolos_to_neo4j.py
Normaliza e ingesta en Neo4j las actualizaciones de guias descargadas por
app/bronze/vigilancia_protocolos_explorer.py -- Pharox_Documento_v5 §11
"Capa 2 - Vigilancia de protocolos".

Crea el subgrafo independiente :ActualizacionProtocolo (mismo patron que
:ProtocoloTratamiento y :RegistroTumor -- ver la nota de independencia de
subgrafos en app/graph_db.py): sin relaciones precomputadas hacia el resto del
grafo, se cruza en tiempo de consulta por coincidencia de subtipo molecular
(subtipos_detectados), igual que el resto de las fuentes.

subtipos_detectados usa el MISMO vocabulario de 4 categorias que
RegistroTumor.subtipo_molecular / ProtocoloTratamiento.subtipo_molecular_match
(HER2_positivo / Triple_negativo / RH_positivo_HER2_negativo / desconocido),
pero es una LISTA (comma-joined): a diferencia de un registro de paciente real
(que tiene un unico subtipo), una guia puede cubrir mas de un subtipo a la vez
("actualizacion para HR+/HER2- y triple negativo"), asi que forzar un unico
valor perderia informacion.

No cubre HER2-low: es una categoria clinicamente real (elegibilidad a
T-DXd) pero distinta de HER2_positivo clasico, y el vocabulario de 4
categorias no la tiene -- mezclarla con HER2_positivo daria falsos
cruces con pacientes/protocolos HER2+ clasicos. Queda sin detectar hasta
que el vocabulario se extienda (mismo criterio de "no forzar clasificacion
sin sustento" ya aplicado en registro_tumores_to_neo4j.py).

Usa MERGE por id=PMID: se puede correr repetidamente sobre ventanas de fechas
solapadas sin duplicar (ver nota en el bronze).

Uso:
    python -m app.bronze.vigilancia_protocolos_explorer && python -m app.gold.vigilancia_protocolos_to_neo4j
    python -m app.gold.vigilancia_protocolos_to_neo4j data/bronze/vigilancia_protocolos_XXXX.json
"""
import os
import re
import sys
import json
import glob
import html

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

_TAG_HTML = re.compile(r"<[^>]+>")

# Mismo vocabulario que RegistroTumor.subtipo_molecular / ProtocoloTratamiento.subtipo_molecular_match.
_PATRONES_SUBTIPO = {
    "Triple_negativo": ["triple-negative", "triple negative", "tnbc"],
    "HER2_positivo": ["her2-positive", "her2 positive", "her2+", "her2-positivo"],
    # RH_positivo_HER2_negativo se detecta aparte: requiere las DOS señales (HR+ Y HER2-).
}
_PATRONES_HR_POSITIVO = [
    "hormone receptor-positive", "hormone receptor positive", "hr-positive", "hr+",
    "estrogen receptor-positive", "estrogen receptor positive", "er-positive",
]
_PATRONES_HER2_NEGATIVO = ["her2-negative", "her2 negative", "her2-"]

_PATRONES_SOCIEDAD = {
    "ESMO": ["esmo", "annals of oncology"],
    "ASCO": ["asco", "journal of clinical oncology"],
    "NCCN": ["nccn"],
}


def _limpiar_html(texto: str) -> str:
    if not texto:
        return ""
    return _TAG_HTML.sub(" ", html.unescape(texto)).strip()


def _detectar_subtipos(texto_lower: str) -> list[str]:
    subtipos = {etiqueta for etiqueta, patrones in _PATRONES_SUBTIPO.items() if any(p in texto_lower for p in patrones)}
    tiene_hr_positivo = any(p in texto_lower for p in _PATRONES_HR_POSITIVO)
    tiene_her2_negativo = any(p in texto_lower for p in _PATRONES_HER2_NEGATIVO)
    if tiene_hr_positivo and tiene_her2_negativo:
        subtipos.add("RH_positivo_HER2_negativo")
    return sorted(subtipos) if subtipos else ["desconocido"]


def _detectar_sociedad(journal: str, texto_lower: str) -> str:
    journal_lower = (journal or "").lower()
    for sociedad, patrones in _PATRONES_SOCIEDAD.items():
        if any(p in journal_lower or p in texto_lower for p in patrones):
            return sociedad
    return "otro"


def crear_constraints(tx):
    tx.run("CREATE CONSTRAINT unique_actualizacion_protocolo_id IF NOT EXISTS FOR (a:ActualizacionProtocolo) REQUIRE a.id IS UNIQUE")


def inicializar_esquema_vigilancia():
    with driver.session() as session:
        session.execute_write(crear_constraints)


def normalizar_actualizacion(articulo: dict) -> dict:
    """
    Aplana un resultado crudo de Europe PMC a las propiedades planas del nodo
    :ActualizacionProtocolo. Pura: sin red, sin Neo4j.
    """
    pmid = articulo.get("pmid") or ""
    id_registro = pmid or articulo.get("id") or ""
    titulo = _limpiar_html(articulo.get("title") or "")
    resumen = _limpiar_html(articulo.get("abstractText") or "")
    journal = (articulo.get("journalInfo") or {}).get("journal", {}).get("title", "") or ""
    fecha_publicacion = articulo.get("firstPublicationDate") or ""

    texto_lower = " ".join([titulo, resumen]).lower()

    return {
        "id": id_registro,
        "titulo": titulo,
        "resumen": resumen,
        "fuente": journal,
        "sociedad": _detectar_sociedad(journal, texto_lower),
        "fecha_publicacion": fecha_publicacion,
        "subtipos_detectados": ", ".join(_detectar_subtipos(texto_lower)),
        # Solo un PMID real resuelve en pubmed.ncbi.nlm.nih.gov -- IDs de preprint
        # (ej. "PPR123456") no son PMIDs y no deben armar esta URL.
        "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "",
    }


def ingestar_actualizacion(tx, actualizacion: dict):
    tx.run("""
        MERGE (a:ActualizacionProtocolo {id: $id})
        SET a += $actualizacion
    """, {"id": actualizacion["id"], "actualizacion": actualizacion})


def procesar_archivo(ruta: str) -> int:
    """Normaliza e ingesta un archivo bronze de vigilancia de protocolos. Devuelve cuantas actualizaciones proceso."""
    with open(ruta, "r", encoding="utf-8") as f:
        articulos_crudos = json.load(f)

    procesadas = 0
    with driver.session() as session:
        for crudo in articulos_crudos:
            actualizacion = normalizar_actualizacion(crudo)
            if not actualizacion["id"] or not actualizacion["titulo"]:
                continue
            session.execute_write(ingestar_actualizacion, actualizacion)
            procesadas += 1

    print(f"[{os.path.basename(ruta)}] {procesadas} actualizacion(es) de protocolo ingestada(s)/actualizada(s).")
    return procesadas


def main():
    inicializar_esquema_vigilancia()

    if len(sys.argv) > 1:
        archivos = sys.argv[1:]
    else:
        archivos = sorted(glob.glob(os.path.join(BRONZE_DIR, "vigilancia_protocolos_*.json")))

    if not archivos:
        print(f"No se encontraron archivos vigilancia_protocolos_*.json en {BRONZE_DIR}")
        return

    total = 0
    for ruta in archivos:
        total += procesar_archivo(ruta)

    print(f"\nTotal de actualizaciones ingestadas en esta corrida: {total}")


if __name__ == "__main__":
    main()
