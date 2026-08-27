"""
civic_explorer.py
Cliente de exploracion para la API GraphQL de CIViC (civicdb.org).

Objetivo: mapear exactamente que informacion devuelve CIViC para una
variante molecular (gen + variante), incluyendo su perfil molecular,
tipos de variante y evidencias clinicas asociadas (terapias, enfermedad,
nivel de evidencia, fuente bibliografica).

Uso:
    python -m app.bronze.civic_explorer BRAF V600E
    python -m app.bronze.civic_explorer            # usa BRAF V600E por defecto

Requiere CIVIC_API_KEY definido en el archivo .env de la raiz del proyecto.
La API key de CIViC autentica al usuario y evita el rate limit anonimo;
se envia como Bearer token (no es una API key de tipo query-param).
"""
import os
import sys
import json
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

load_dotenv()

CIVIC_GRAPHQL_URL = "https://civicdb.org/api/graphql"
CIVIC_API_KEY = os.getenv("CIVIC_API_KEY")

# Carpeta bronze de datos crudos (misma capa que raw_fhir_batch.json)
SALIDA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "bronze"
)

# Consulta que trae, para una variante puntual, todo lo que CIViC expone:
# identidad de la variante, tipos SO, el gen/feature al que pertenece,
# su perfil molecular (Molecular Profile) y las evidencias clinicas ligadas.
QUERY_VARIANTE_COMPLETA = """
query VarianteCompleta($id: Int!, $primerasEvidencias: Int!) {
  variant(id: $id) {
    id
    name
    link
    deprecated
    variantTypes {
      id
      name
      url
    }
    feature {
      id
      name
      featureType
      description
      featureInstance {
        ... on Gene {
          id
          name
          entrezId
        }
      }
    }
    singleVariantMolecularProfile {
      id
      name
      description
      molecularProfileScore
      evidenceCountsByStatus {
        acceptedCount
        submittedCount
        rejectedCount
      }
      evidenceCountsByType {
        diagnosticCount
        predictiveCount
        predisposingCount
        prognosticCount
        oncogenicCount
        functionalCount
      }
      evidenceItems(first: $primerasEvidencias) {
        totalCount
        nodes {
          id
          name
          description
          evidenceType
          evidenceDirection
          evidenceLevel
          evidenceRating
          significance
          status
          variantOrigin
          therapyInteractionType
          disease {
            id
            name
            doid
            displayName
          }
          therapies {
            id
            name
            ncitId
          }
          source {
            id
            citationId
            sourceType
            citation
            publicationYear
            journal
            sourceUrl
          }
        }
      }
    }
  }
}
"""

QUERY_BUSCAR_VARIANT_ID = """
query BuscarVariantId($entrezSymbol: String!, $variantName: String!) {
  gene(entrezSymbol: $entrezSymbol) {
    id
    name
    variants(name: $variantName, first: 5) {
      totalCount
      nodes {
        id
        name
      }
    }
  }
}
"""


def consultar_civic(query: str, variables: dict) -> dict:
    """Ejecuta una consulta contra la API GraphQL de CIViC autenticada con Bearer token."""
    if not CIVIC_API_KEY:
        raise RuntimeError(
            "CIVIC_API_KEY no esta definida. Agregala al archivo .env: "
            "CIVIC_API_KEY=cvc_xxx"
        )

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {CIVIC_API_KEY}",
    }
    respuesta = requests.post(
        CIVIC_GRAPHQL_URL,
        json={"query": query, "variables": variables},
        headers=headers,
        timeout=30,
    )
    respuesta.raise_for_status()
    payload = respuesta.json()

    if "errors" in payload:
        mensajes = "; ".join(e.get("message", str(e)) for e in payload["errors"])
        raise RuntimeError(f"CIViC GraphQL devolvio errores: {mensajes}")

    return payload["data"]


def resolver_variant_id(gen: str, nombre_variante: str) -> int:
    """
    Resuelve el ID interno de CIViC para una variante puntual dado el
    simbolo del gen (Entrez symbol) y el nombre exacto de la variante (ej: 'V600E').
    """
    data = consultar_civic(
        QUERY_BUSCAR_VARIANT_ID,
        {"entrezSymbol": gen, "variantName": nombre_variante},
    )
    gene = data.get("gene")
    if not gene:
        raise ValueError(f"Gen '{gen}' no encontrado en CIViC.")

    nodos = gene["variants"]["nodes"]
    coincidencia_exacta = next((v for v in nodos if v["name"] == nombre_variante), None)
    if coincidencia_exacta:
        return coincidencia_exacta["id"]
    if nodos:
        print(
            f"[Aviso] No hubo coincidencia exacta para '{nombre_variante}'. "
            f"Usando la primera coincidencia parcial: {nodos[0]['name']} (id={nodos[0]['id']})"
        )
        return nodos[0]["id"]

    raise ValueError(f"Variante '{nombre_variante}' no encontrada para el gen '{gen}'.")


def obtener_variante_molecular(gen: str, nombre_variante: str, max_evidencias: int = 25) -> dict:
    """
    Punto de entrada principal: dado un gen y una variante puntual,
    devuelve el registro completo tal como lo expone la API de CIViC
    (identidad, tipos SO, gen, perfil molecular y evidencias clinicas).
    """
    variant_id = resolver_variant_id(gen, nombre_variante)
    data = consultar_civic(
        QUERY_VARIANTE_COMPLETA,
        {"id": variant_id, "primerasEvidencias": max_evidencias},
    )
    return data["variant"]


def resumir_variante(variante: dict) -> None:
    """Imprime en consola un resumen legible de la informacion devuelta por CIViC."""
    perfil = variante.get("singleVariantMolecularProfile") or {}
    evidencias = (perfil.get("evidenceItems") or {}).get("nodes", [])
    total_evidencias = (perfil.get("evidenceItems") or {}).get("totalCount", 0)

    print("=" * 70)
    print(f"Variante: {variante['feature']['name']} {variante['name']} (CIViC id={variante['id']})")
    print(f"URL: https://civicdb.org{variante['link']}")
    print(f"Deprecada: {variante['deprecated']}")
    print(f"Tipos (Sequence Ontology): {[t['name'] for t in variante.get('variantTypes', [])]}")
    print("-" * 70)
    print(f"Perfil molecular: {perfil.get('name')} (score={perfil.get('molecularProfileScore')})")
    print(f"Descripcion: {(perfil.get('description') or '')[:300]}...")
    print(f"Evidencias por estado: {perfil.get('evidenceCountsByStatus')}")
    print(f"Evidencias por tipo: {perfil.get('evidenceCountsByType')}")
    print("-" * 70)
    print(f"Mostrando {len(evidencias)} de {total_evidencias} evidencias totales:")
    for ev in evidencias:
        terapias = ", ".join(t["name"] for t in ev.get("therapies", [])) or "-"
        enfermedad = (ev.get("disease") or {}).get("displayName", "-")
        fuente = ev.get("source") or {}
        print(
            f"  [{ev['name']}] {ev['evidenceType']}/{ev['significance']} "
            f"(nivel {ev['evidenceLevel']}, rating {ev['evidenceRating']}) "
            f"| Enfermedad: {enfermedad} | Terapias: {terapias} "
            f"| Fuente: {fuente.get('citation')} ({fuente.get('sourceUrl')})"
        )
    print("=" * 70)


def guardar_json(variante: dict, gen: str, nombre_variante: str) -> str:
    """Persiste la respuesta cruda de CIViC en la capa bronze para su posterior ETL."""
    os.makedirs(SALIDA_DIR, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    nombre_archivo = f"civic_{gen}_{nombre_variante}_{timestamp}.json".replace(" ", "_")
    ruta = os.path.join(SALIDA_DIR, nombre_archivo)
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(variante, f, indent=2, ensure_ascii=False)
    return ruta


def main():
    gen = sys.argv[1] if len(sys.argv) > 1 else "BRAF"
    nombre_variante = sys.argv[2] if len(sys.argv) > 2 else "V600E"

    print(f"Consultando CIViC: gen={gen}, variante={nombre_variante}...")
    variante = obtener_variante_molecular(gen, nombre_variante)

    resumir_variante(variante)
    ruta = guardar_json(variante, gen, nombre_variante)
    print(f"\nJSON crudo guardado en: {ruta}")


if __name__ == "__main__":
    main()
