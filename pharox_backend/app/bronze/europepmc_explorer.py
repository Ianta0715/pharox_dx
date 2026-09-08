"""
europepmc_explorer.py
Cliente de exploracion para la API REST de Europe PMC (ebi.ac.uk/europepmc).

Objetivo: traer literatura reciente de cancer de mama (titulo + abstract + PMID)
para poblar el nodo :Literatura ya existente (ver app/graph_db.py) con evidencia
real en vez de -o ademas de- el dataset sintetico que lo siembra hoy. A diferencia
de las otras 3 fuentes nuevas, esto NO crea un subgrafo propio.

Ordena por fecha de publicacion descendente (sort=P_PDATE_D desc) para que una
corrida periodica capture la evidencia mas nueva primero -- la idea de "incorporar
evidencia automaticamente" del usuario.

Uso:
    python -m app.bronze.europepmc_explorer
    python -m app.bronze.europepmc_explorer "HER2 breast cancer" 100

No requiere API key: la API REST de Europe PMC es publica y sin autenticacion.
"""
import os
import sys
import json
from datetime import datetime, timezone

import requests

EUROPEPMC_SEARCH_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

SALIDA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "bronze"
)

QUERY_DEFAULT = "breast cancer AND (HER2 OR ESR1 OR BRCA1 OR BRCA2 OR TNBC OR CDK4 OR CDK6)"
PAGE_SIZE = 100


def buscar_articulos(query: str = QUERY_DEFAULT, max_articulos: int = 200) -> list[dict]:
    """
    Pagina sobre la API de Europe PMC (cursorMark) hasta agotar resultados o
    alcanzar max_articulos. Restringe a registros indexados en PubMed (SRC:MED)
    y devuelve la lista cruda de resultados (resultType=core, incluye abstractText).
    """
    articulos: list[dict] = []
    cursor_mark = "*"

    while True:
        params = {
            "query": f"({query}) AND SRC:MED",
            "format": "json",
            "resultType": "core",
            "pageSize": min(PAGE_SIZE, max_articulos - len(articulos)),
            "sort": "P_PDATE_D desc",
            "cursorMark": cursor_mark,
        }
        respuesta = requests.get(EUROPEPMC_SEARCH_URL, params=params, timeout=30)
        respuesta.raise_for_status()
        payload = respuesta.json()

        resultados = payload.get("resultList", {}).get("result", [])
        articulos.extend(resultados)
        print(f"[Europe PMC] {len(articulos)} articulo(s) descargado(s) hasta ahora...")

        siguiente_cursor = payload.get("nextCursorMark")
        if not resultados or not siguiente_cursor or siguiente_cursor == cursor_mark or len(articulos) >= max_articulos:
            break
        cursor_mark = siguiente_cursor

    return articulos[:max_articulos]


def guardar_json(articulos: list[dict], query: str) -> str:
    os.makedirs(SALIDA_DIR, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    nombre_archivo = f"europepmc_{timestamp}.json"
    ruta = os.path.join(SALIDA_DIR, nombre_archivo)
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(articulos, f, indent=2, ensure_ascii=False)
    return ruta


def main():
    query = sys.argv[1] if len(sys.argv) > 1 else QUERY_DEFAULT
    max_articulos = int(sys.argv[2]) if len(sys.argv) > 2 else 200

    print(f"Consultando Europe PMC: query='{query}', max={max_articulos}...")
    articulos = buscar_articulos(query, max_articulos)

    print(f"\nTotal de articulos descargados: {len(articulos)}")
    if articulos:
        primero = articulos[0]
        print(f"Ejemplo: PMID {primero.get('pmid')} - {primero.get('title')}")

    ruta = guardar_json(articulos, query)
    print(f"\nJSON crudo guardado en: {ruta}")


if __name__ == "__main__":
    main()
