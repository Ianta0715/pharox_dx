"""
clinvar_explorer.py
Cliente de exploracion para ClinVar via NCBI E-utilities (eutils.ncbi.nlm.nih.gov).

Objetivo: traer, para un gen dado, las variantes clasificadas por laboratorios
clinicos (patogenica / VUS / benigna, etc.) asociadas a cancer de mama, con las
expresiones HGVS y coordenadas genomicas que CIViC no expone (ver
app/bronze/civic_explorer.py y la nota de independencia de subgrafos).

Flujo E-utilities: esearch (termino -> lista de VariationID) -> esummary batcheado
(VariationID... -> resumen JSON completo). No se pide un ID por request para no
acercarse al limite de rate (3 req/s sin key, 10 req/s con NCBI_API_KEY).

Uso:
    python -m app.bronze.clinvar_explorer
    python -m app.bronze.clinvar_explorer BRCA1
    python -m app.bronze.clinvar_explorer PIK3CA "PIK3CA AND breast cancer"

NCBI_API_KEY es OPCIONAL (a diferencia de CIVIC_API_KEY): sin ella, E-utilities
igual funciona pero mas lento (3 req/s en vez de 10 req/s).
"""
import os
import sys
import json
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

load_dotenv()

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
NCBI_API_KEY = os.getenv("NCBI_API_KEY")

SALIDA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "bronze"
)

RETMAX_DEFAULT = 500
LOTE_ESUMMARY = 150  # cuantos VariationID se piden juntos por esummary (evita URLs gigantes)


def _params_con_key(params: dict) -> dict:
    """Agrega api_key solo si esta seteada; nunca lanza si falta (a diferencia de CIViC)."""
    if NCBI_API_KEY:
        params = {**params, "api_key": NCBI_API_KEY}
    return params


def buscar_variation_ids(termino: str, retmax: int = RETMAX_DEFAULT) -> list[str]:
    """esearch: devuelve la lista de VariationID (ClinVar) que matchean el termino."""
    params = _params_con_key({
        "db": "clinvar",
        "term": termino,
        "retmode": "json",
        "retmax": retmax,
    })
    respuesta = requests.get(f"{EUTILS_BASE}/esearch.fcgi", params=params, timeout=30)
    respuesta.raise_for_status()
    return respuesta.json().get("esearchresult", {}).get("idlist", [])


def obtener_resumenes(variation_ids: list[str]) -> dict:
    """esummary batcheado: devuelve {uid: resumen} para todos los ids pedidos."""
    resumenes = {}
    for i in range(0, len(variation_ids), LOTE_ESUMMARY):
        lote = variation_ids[i:i + LOTE_ESUMMARY]
        params = _params_con_key({
            "db": "clinvar",
            "id": ",".join(lote),
            "retmode": "json",
        })
        respuesta = requests.get(f"{EUTILS_BASE}/esummary.fcgi", params=params, timeout=30)
        respuesta.raise_for_status()
        result = respuesta.json().get("result", {})
        for uid in result.get("uids", []):
            resumenes[uid] = result[uid]
        print(f"[ClinVar] esummary: {len(resumenes)}/{len(variation_ids)} variante(s) descargada(s)...")
    return resumenes


def buscar_variantes_clinvar(gen: str, termino_extra: str = "breast cancer", retmax: int = RETMAX_DEFAULT) -> list[dict]:
    """Punto de entrada: gen -> lista de resumenes crudos de esummary para ese gen + condicion."""
    termino = f"{gen}[gene] AND {termino_extra}"
    ids = buscar_variation_ids(termino, retmax)
    print(f"[ClinVar] {len(ids)} variante(s) encontrada(s) para '{termino}'.")
    if not ids:
        return []
    resumenes = obtener_resumenes(ids)
    return list(resumenes.values())


def guardar_json(variantes: list[dict], gen: str) -> str:
    os.makedirs(SALIDA_DIR, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    nombre_archivo = f"clinvar_{gen}_{timestamp}.json".replace(" ", "_")
    ruta = os.path.join(SALIDA_DIR, nombre_archivo)
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(variantes, f, indent=2, ensure_ascii=False)
    return ruta


def main():
    gen = sys.argv[1] if len(sys.argv) > 1 else "BRCA1"
    termino_extra = sys.argv[2] if len(sys.argv) > 2 else "breast cancer"

    if not NCBI_API_KEY:
        print("[Aviso] NCBI_API_KEY no esta seteada: E-utilities limita a 3 req/s (10 req/s con key).")

    print(f"Consultando ClinVar: gen={gen}, condicion='{termino_extra}'...")
    variantes = buscar_variantes_clinvar(gen, termino_extra)

    print(f"\nTotal de variantes descargadas: {len(variantes)}")
    if variantes:
        primera = variantes[0]
        print(f"Ejemplo: {primera.get('accession')} - {primera.get('title')}")

    ruta = guardar_json(variantes, gen)
    print(f"\nJSON crudo guardado en: {ruta}")


if __name__ == "__main__":
    main()
