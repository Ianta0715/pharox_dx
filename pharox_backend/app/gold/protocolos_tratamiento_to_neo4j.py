"""
protocolos_tratamiento_to_neo4j.py
Normaliza e ingesta en Neo4j la tabla de protocolos de tratamiento estandar de
cancer de mama, provista en data/tratamientos.xlsx (hoja
"Datos Tipo de CancerTratamiento").

A diferencia de RegistroTumor (datos reales de 216 pacientes), esta tabla es
CONOCIMIENTO DE REFERENCIA: filas que dicen "dado este perfil clinico
(topografia + histologia + biomarcadores + estadio), este es el esquema de
tratamiento estandar". No hay relacion 1-a-1 con los pacientes de
RegistroTumor -- se cruzan en tiempo de consulta por coincidencia de perfil
(topografia/biomarcadores/estadio), igual que CIViC se cruza con el resto del
grafo clinico. Ver la nota de independencia de subgrafos en app/graph_db.py.

IMPORTANTE -- filtro a cancer de mama: el excel origen trae protocolos de
MUCHOS tipos de cancer (prostata C61, colon/recto C18-C20, pulmon C34,
estomago C16, cuello uterino C53, ovario C56, testiculo C62, linfoma C77,
cerebro C71), no solo mama. Pharox esta enfocado exclusivamente en cancer de
mama por ahora (mismo criterio ya aplicado en civic_explorer.py), asi que
solo se ingestan filas cuyo "CIE-10 Topo" empiece con C50. El resto se
descarta en el momento de la ingesta -- no llegan a pisar el grafo con
protocolos de otro cancer que despues alguien podria cruzar mal con evidencia
de mama. Cuando el proyecto se expanda a otros tipos de cancer, sacar este
filtro (o generalizarlo por tipo de cancer segun la consulta).

Ademas del filtro por topografia, se deriva "subtipo_molecular_match": el
mismo vocabulario de 4 categorias que usa RegistroTumor.subtipo_molecular
(HER2_positivo / Triple_negativo / RH_positivo_HER2_negativo / desconocido),
parseado del texto libre de "Biomarcadores Criticos". Es lo que permite
cruzar un protocolo con un paciente real o con evidencia CIViC por
coincidencia exacta de subtipo, en vez de una busqueda de texto libre (que en
las pruebas iniciales trajo protocolos de otro perfil por matchear con
CONTAINS "HER2" de forma demasiado amplia).

Sin ID natural en el origen: se usa un id sintetico por posicion de fila
(PROTO_01, PROTO_02, ...), solo para que la ingesta sea idempotente.

Se descarta la columna "Mapping FHIR" (metadata tecnica de interoperabilidad,
no aporta a la busqueda clinica).

Uso:
    python -m app.gold.protocolos_tratamiento_to_neo4j
    python -m app.gold.protocolos_tratamiento_to_neo4j "data/otro_archivo.xlsx"
"""
import os
import sys

import openpyxl
from neo4j import GraphDatabase
from dotenv import load_dotenv

load_dotenv()

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "pharoxpass")

DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data"
)
ARCHIVO_DEFAULT = os.path.join(DATA_DIR, "tratamientos.xlsx")
HOJA = "Datos Tipo de CancerTratamiento"

driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))


def crear_constraints(tx):
    tx.run("CREATE CONSTRAINT unique_protocolo_tratamiento_id IF NOT EXISTS FOR (p:ProtocoloTratamiento) REQUIRE p.id IS UNIQUE")


def inicializar_esquema_protocolos():
    with driver.session() as session:
        session.execute_write(crear_constraints)


# El excel de origen fue exportado desde una herramienta que escribe las flechas
# de secuencia como codigo LaTeX ("$\rightarrow$") en vez de como caracter. Sin
# normalizarlo, el esquema AC-T y el de KEYNOTE-522 llegan al LLM con ese literal
# adentro y salen citados asi en la respuesta clinica, que es lo que ve el medico.
# Las claves van de la mas larga a la mas corta: "\to" es prefijo de "\rightarrow".
_ARTEFACTOS_FORMULA = {
    r"$\rightarrow$": "→",
    r"$\to$": "→",
    r"\rightarrow": "→",
    r"\to": "→",
}


def _limpiar_artefactos_formula(texto: str) -> str:
    """Reemplaza los escapes LaTeX de flecha por el caracter real y colapsa espacios."""
    for crudo, limpio in _ARTEFACTOS_FORMULA.items():
        texto = texto.replace(crudo, limpio)
    # el reemplazo suele dejar espacios dobles alrededor de la flecha
    return " ".join(texto.split())


def _s(val) -> str | None:
    if val is None:
        return None
    texto = _limpiar_artefactos_formula(str(val).strip())
    return texto or None


def es_topografia_mama(topografia_codigo: str | None) -> bool:
    """True si el codigo CIE-10 de topografia corresponde a mama (C50.x). Puede traer varios codigos separados por '/'."""
    if not topografia_codigo:
        return False
    return any(parte.strip().upper().startswith("C50") for parte in topografia_codigo.split("/"))


def derivar_subtipo_molecular_match(biomarcadores_criticos: str | None) -> str:
    """
    Parsea el texto libre de 'Biomarcadores Criticos' al mismo vocabulario de 4
    categorias que usa RegistroTumor.subtipo_molecular, para poder cruzar por
    coincidencia exacta en vez de texto libre. Solo tiene sentido para filas de
    mama (biomarcadores de otros canceres -Gleason, PSA, KRAS, etc- no aplican
    a este vocabulario y quedan como "desconocido").
    """
    if not biomarcadores_criticos:
        return "desconocido"
    texto = biomarcadores_criticos.upper()

    tiene_her2_positivo = "HER2 POSITIVO" in texto or "HER2+" in texto or "HER2 +" in texto
    tiene_her2_negativo = "HER2 NEG" in texto or "HER2-" in texto or "HER2 -" in texto

    if tiene_her2_positivo:
        return "HER2_positivo"

    re_negativo = "RE-" in texto or "RE -" in texto
    rp_negativo = "RP-" in texto or "RP -" in texto
    re_positivo = "RE+" in texto or "RE +" in texto
    rp_positivo = "RP+" in texto or "RP +" in texto

    if re_negativo and rp_negativo and tiene_her2_negativo:
        return "Triple_negativo"
    if (re_positivo or rp_positivo) and tiene_her2_negativo:
        return "RH_positivo_HER2_negativo"

    return "desconocido"


def normalizar_fila(fila: dict, indice: int) -> dict:
    """Aplana una fila cruda del excel (dict columna->valor) a las propiedades planas de :ProtocoloTratamiento."""
    biomarcadores_criticos = _s(fila.get("Biomarcadores Críticos"))
    return {
        "id": f"PROTO_{indice:02d}",
        "topografia_grupo": _s(fila.get("Grupo Topográfico")),
        "topografia_codigo": _s(fila.get("CIE-10 Topo")),
        "histologia_subtipo": _s(fila.get("Histología / Subtipo")),
        "biomarcadores_criticos": biomarcadores_criticos,
        "subtipo_molecular_match": derivar_subtipo_molecular_match(biomarcadores_criticos),
        "estadio_tnm": _s(fila.get("Estadio TNM")),
        "intencion_linea": _s(fila.get("Intención / Línea")),
        "protocolo_esquema": _s(fila.get("Protocolo / Esquema Estándar")),
        "modalidad": _s(fila.get("Modalidad")),
    }


def ingestar_protocolo(tx, protocolo: dict):
    tx.run("""
        MERGE (p:ProtocoloTratamiento {id: $id})
        SET p += $protocolo
    """, {"id": protocolo["id"], "protocolo": protocolo})


def procesar_archivo(ruta: str, hoja: str = HOJA) -> int:
    """Lee la hoja indicada del excel e ingesta cada fila como :ProtocoloTratamiento. Devuelve cuantas filas proceso."""
    wb = openpyxl.load_workbook(ruta, read_only=True, data_only=True)
    ws = wb[hoja]
    filas = list(ws.iter_rows(values_only=True))
    encabezado = filas[0]

    procesadas = 0
    descartadas_otro_cancer = 0
    with driver.session() as session:
        for i, valores in enumerate(filas[1:], start=1):
            fila = dict(zip(encabezado, valores))
            if not any(fila.values()):
                continue
            protocolo = normalizar_fila(fila, i)
            if not protocolo["topografia_grupo"]:
                continue
            if not es_topografia_mama(protocolo["topografia_codigo"]):
                descartadas_otro_cancer += 1
                continue
            session.execute_write(ingestar_protocolo, protocolo)
            procesadas += 1

    print(
        f"[{os.path.basename(ruta)} / hoja '{hoja}'] {procesadas} protocolo(s) de mama ingestado(s)/actualizado(s) "
        f"({descartadas_otro_cancer} descartado(s) por ser de otro tipo de cancer)."
    )
    return procesadas


def main():
    inicializar_esquema_protocolos()

    ruta = sys.argv[1] if len(sys.argv) > 1 else ARCHIVO_DEFAULT
    if not os.path.exists(ruta):
        print(f"No se encontro el archivo: {ruta}")
        return

    total = procesar_archivo(ruta)
    print(f"\nTotal de protocolos ingestados en esta corrida: {total}")


if __name__ == "__main__":
    main()
