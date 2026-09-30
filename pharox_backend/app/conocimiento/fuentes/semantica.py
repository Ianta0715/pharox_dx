"""
Búsquedas vectoriales: literatura real de Europe PMC (:Literatura) y casos
clínicos reales ingresados (:CasoClinico).

Son las únicas fuentes que se consultan con el texto libre, porque la
similitud semántica es justamente su criterio. Necesitan un embedding
(Ollama, nomic-embed-text); si no está disponible, el copiloto sigue
funcionando sin ellas.
"""
from __future__ import annotations

from typing import Callable

from app.conocimiento.evidencia import Evidencia, Fuente
from app.conocimiento.grafo import Lector

Embedder = Callable[[str], list[float]]

TIPO_CANCER_MAMA = "Cáncer de Mama"
UMBRAL_SIMILITUD_CASOS = 0.6


def evidencia_literatura(lector: Lector, vector: list[float], n: int = 3) -> list[Evidencia]:
    filas = lector(
        """
        CALL db.index.vector.queryNodes('literature_vectors', $k, $vector)
        YIELD node, score
        WHERE node.tipo_cancer = $tipo_cancer
        RETURN node.id AS id, node.text AS texto, node.drogas AS drogas, score
        ORDER BY score DESC
        LIMIT $n
        """,
        {"k": n * 3, "vector": vector, "tipo_cancer": TIPO_CANCER_MAMA, "n": n},
    )
    evidencias = []
    for f in filas:
        texto = f.get("texto") or ""
        titulo, _, resumen = texto.partition(". ")
        pmid = str(f.get("id") or "")
        evidencias.append(Evidencia(
            fuente=Fuente.LITERATURA,
            titulo=titulo[:200] or f"PMID {pmid}",
            detalle=(resumen or texto)[:600] + f" (similitud {f.get('score', 0):.2f})",
            url=f"https://europepmc.org/article/MED/{pmid}" if pmid.isdigit() else None,
            referencias=(f"PMID:{pmid}",) if pmid.isdigit() else (),
        ))
    return evidencias


def buscar_casos_similares(lector: Lector, vector: list[float], n: int = 2) -> list[dict]:
    return lector(
        """
        CALL db.index.vector.queryNodes('caso_clinico_vectors', $n, $vector)
        YIELD node AS caso, score
        WHERE score > $umbral
        OPTIONAL MATCH (caso)-[:INCLUYE_HALLAZGO]->(hp:HallazgoPatologico)
        OPTIONAL MATCH (caso)-[:TUVO_EVENTO_POSTOP]->(ev:EventoPostOperatorio)
        RETURN caso.id AS id, caso.resumen_clinico AS resumen,
               [h IN collect(DISTINCT hp.subtipo_molecular + ' ' + hp.lateralidad + ' - ' + hp.procedimiento) WHERE h IS NOT NULL] AS hallazgos,
               [e IN collect(DISTINCT ev.tipo_evento + ': ' + ev.detalle_resolucion + ' → ' + ev.resultado) WHERE e IS NOT NULL] AS eventos,
               score
        ORDER BY score DESC
        """,
        {"n": n, "vector": vector, "umbral": UMBRAL_SIMILITUD_CASOS},
    )


def formatear_caso(caso: dict) -> str:
    texto = f"[similitud {caso.get('score', 0):.2f}] {caso.get('resumen') or ''}"
    if caso.get("hallazgos"):
        texto += f" | Hallazgos: {'; '.join(caso['hallazgos'])}"
    if caso.get("eventos"):
        texto += f" | Complicaciones post-op: {'; '.join(caso['eventos'])}"
    return texto


def evidencia_casos(lector: Lector, vector: list[float], n: int = 2) -> list[Evidencia]:
    return [
        Evidencia(
            fuente=Fuente.CASO,
            titulo=f"Caso clínico real similar (similitud {c.get('score', 0):.2f})",
            detalle=formatear_caso(c),
            sobre_otras_personas=True,
        )
        for c in buscar_casos_similares(lector, vector, n)
    ]
