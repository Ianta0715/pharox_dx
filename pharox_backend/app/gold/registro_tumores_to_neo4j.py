"""
registro_tumores_to_neo4j.py
Normaliza e ingesta en Neo4j el registro real de tumores de mama del Hospital
Central - Mendoza, provisto en data/datos actualizados tumores.xlsx (hoja "MAMA").

Crea un subgrafo independiente (:RegistroTumor), sin relaciones hacia el
subgrafo clinico sintetico (Paciente/Tumor) ni hacia CIViC/ClinVar/ClinicalTrials/
cBioPortal -- mismo patron que el resto de las fuentes (ver nota de independencia
de subgrafos en app/graph_db.py). Se cruza en tiempo de consulta por coincidencia
de topografia/biomarcadores/estadio, no por relaciones precomputadas.

Fuente sin ID de paciente (removido intencionalmente antes de compartir el
archivo): no hay forma de saber si dos filas corresponden a la misma persona,
asi que cada fila es un :RegistroTumor independiente. La clave de MERGE es un id
sintetico basado en la posicion de la fila (RT_0001, RT_0002, ...), estable
mientras no se reordene el excel -- no es informacion identificatoria, es solo
para hacer la ingesta idempotente.

Columnas descartadas por no tener informacion util (confirmado empiricamente,
ver values.Counter sobre las 216 filas): FMODTU (100% vacia), GLEAS/BRES/IPI
(100% "N/D" -- campos geneticos de la planilla RHT para prostata/melanoma/
linfoma, no aplican a mama), TEMCA2/TEMCA3/TEMCA4 (100% "N/D").

Subtipo molecular: se deriva SOLO para los dos casos no ambiguos a partir de
receptor_estrogeno/receptor_progesterona/her2 (HER2_positivo, Triple_negativo).
NO se fuerza una distincion Luminal_A vs Luminal_B: esa distincion clinica real
depende del indice de proliferacion Ki67, que en este dataset solo aparece de
forma esporadica y no estructurada dentro de INMHQ (texto libre) -- inventar
esa clasificacion sin dato confiable violaria el pedido explicito de fidelidad
de los datos. Cuando hay receptores hormonales positivos y HER2 negativo se
guarda "RH_positivo_HER2_negativo", sin pretender mas precision de la que el
dato sostiene.

Uso:
    python -m app.gold.registro_tumores_to_neo4j
    python -m app.gold.registro_tumores_to_neo4j "data/otro_archivo.xlsx"
"""
import os
import sys
import datetime

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
ARCHIVO_DEFAULT = os.path.join(DATA_DIR, "datos actualizados tumores.xlsx")
HOJA = "MAMA"

# Valores centinela del origen que en realidad significan "sin dato". "Se ignora"/
# "Dudoso" NO estan aca a proposito: son estados clinicos reales (evaluado pero
# incierto/no evaluable), distintos de "no se cargo el dato".
VALORES_NULOS = {"N/D", "n/d", "None", "", "...", None}
FECHA_CENTINELA = datetime.date(1900, 1, 2)

driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))


def crear_constraints(tx):
    tx.run("CREATE CONSTRAINT unique_registro_tumor_id IF NOT EXISTS FOR (r:RegistroTumor) REQUIRE r.id IS UNIQUE")


def inicializar_esquema_registro_tumores():
    with driver.session() as session:
        session.execute_write(crear_constraints)


def _s(val) -> str | None:
    """Normaliza un valor de celda a string limpio, o None si es un centinela de 'sin dato'."""
    if val is None:
        return None
    if isinstance(val, (datetime.date, datetime.datetime)):
        return None  # las fechas se manejan aparte, con _fecha()
    texto = str(val).strip()
    if texto in VALORES_NULOS or texto.strip() == "":
        return None
    return texto


def _fecha(val) -> str | None:
    """Normaliza una celda de fecha a ISO (YYYY-MM-DD). None si es nula o es la fecha centinela 1900-01-02."""
    if val is None:
        return None
    if isinstance(val, datetime.datetime):
        val = val.date()
    if not isinstance(val, datetime.date):
        return None
    if val == FECHA_CENTINELA:
        return None
    return val.isoformat()


def _entero(val) -> int | None:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return int(val)
    texto = _s(val)
    if texto is None:
        return None
    try:
        return int(float(texto))
    except ValueError:
        return None


def derivar_subtipo_molecular(receptor_estrogeno: str | None, receptor_progesterona: str | None, her2: str | None) -> str:
    """
    Deriva el subtipo molecular SOLO cuando el dato lo sostiene sin ambiguedad.
    No distingue Luminal_A de Luminal_B (requeriria Ki67 confiable, que este
    dataset no tiene de forma estructurada).
    """
    if her2 == "(+)":
        return "HER2_positivo"
    if receptor_estrogeno is None or receptor_progesterona is None or her2 is None:
        return "desconocido"
    if receptor_estrogeno == "(-)" and receptor_progesterona == "(-)" and her2 == "(-)":
        return "Triple_negativo"
    if (receptor_estrogeno == "(+)" or receptor_progesterona == "(+)") and her2 == "(-)":
        return "RH_positivo_HER2_negativo"
    return "desconocido"


def normalizar_fila(fila: dict, indice: int) -> dict:
    """Aplana una fila cruda del excel (dict columna->valor) a las propiedades planas de :RegistroTumor."""
    receptor_estrogeno = _s(fila.get("MEST"))
    receptor_progesterona = _s(fila.get("MMPRG"))
    her2 = _s(fila.get("MMHER"))

    return {
        "id": f"RT_{indice:04d}",
        "edad": _entero(fila.get("DGED")),
        "sexo": _s(fila.get("PTESXN")),
        "topografia_codigo": _s(fila.get("TPGF")),
        "topografia_nombre": _s(fila.get("TPGFN")),
        "morfologia_codigo": _entero(fila.get("MFG")),
        "morfologia_nombre": _s(fila.get("MFGN")),
        "comportamiento": _s(fila.get("COMPN")),
        "grado_diferenciacion": _s(fila.get("DIFHIN")),
        "metodo_diagnostico": _s(fila.get("METDGN")),
        "tumor_primario_multiple": _s(fila.get("PRIMA")),
        "numero_primarios": _entero(fila.get("PRINU")),
        "otro_tumor": _s(fila.get("OTRTU")),
        "relacion_otro_tumor": _s(fila.get("TEMCA1")),
        "estadio_clinico_t": _s(fila.get("ESTCT")),
        "estadio_clinico_n": _s(fila.get("ESTCN")),
        "estadio_clinico_m": _s(fila.get("ESTCM")),
        "estadio_clinico": _s(fila.get("TNMCL")),
        "lateralidad": _s(fila.get("TULAT")),
        "estadio_patologico_t": _s(fila.get("ESTPT")),
        "estadio_patologico_n": _s(fila.get("ESTPN")),
        "estadio_patologico_m": _s(fila.get("ESTPM")),
        "estadio_patologico": _s(fila.get("PTNM")),
        "receptor_estrogeno": receptor_estrogeno,
        "receptor_progesterona": receptor_progesterona,
        "her2": her2,
        "subtipo_molecular": derivar_subtipo_molecular(receptor_estrogeno, receptor_progesterona, her2),
        "notas_ihq": _s(fila.get("INMHQ")),
        "ecog": _s(fila.get("ECOG")),
        "categoria_captacion": _s(fila.get("CATPO")),
        "estado_registro": _s(fila.get("STRTU")),
        "hospital": _s(fila.get("ESREFN")),
        "fecha_registro": _fecha(fila.get("FEREG")),
        "fecha_consulta": _fecha(fila.get("FECON")),
        "fecha_diagnostico": _fecha(fila.get("FEDG")),
        "fecha_inicio_tratamiento": _fecha(fila.get("FEINI")),
    }


def ingestar_registro(tx, registro: dict):
    tx.run("""
        MERGE (r:RegistroTumor {id: $id})
        SET r += $registro
    """, {"id": registro["id"], "registro": registro})


def procesar_archivo(ruta: str, hoja: str = HOJA) -> int:
    """Lee la hoja indicada del excel e ingesta cada fila como :RegistroTumor. Devuelve cuantas filas proceso."""
    wb = openpyxl.load_workbook(ruta, read_only=True, data_only=True)
    ws = wb[hoja]
    filas = list(ws.iter_rows(values_only=True))
    encabezado = filas[0]

    procesadas = 0
    with driver.session() as session:
        for i, valores in enumerate(filas[1:], start=1):
            fila = dict(zip(encabezado, valores))
            registro = normalizar_fila(fila, i)
            session.execute_write(ingestar_registro, registro)
            procesadas += 1

    print(f"[{os.path.basename(ruta)} / hoja '{hoja}'] {procesadas} registro(s) de tumor ingestado(s)/actualizado(s).")
    return procesadas


def main():
    inicializar_esquema_registro_tumores()

    ruta = sys.argv[1] if len(sys.argv) > 1 else ARCHIVO_DEFAULT
    if not os.path.exists(ruta):
        print(f"No se encontro el archivo: {ruta}")
        return

    total = procesar_archivo(ruta)
    print(f"\nTotal de registros de tumor ingestados en esta corrida: {total}")


if __name__ == "__main__":
    main()
