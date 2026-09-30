"""Métricas del grafo para depuración (/api/v1/debug/graph_db)."""
from __future__ import annotations

from app.conocimiento.grafo import Lector


def estado_grafo(lector: Lector) -> dict:
    nodos = lector("MATCH (n) RETURN labels(n)[0] AS label, count(n) AS cantidad")
    relaciones = lector("MATCH ()-[r]->() RETURN type(r) AS tipo, count(r) AS cantidad")
    literatura = lector("MATCH (l:Literatura) RETURN l.id AS id, l.tipo_cancer AS tipo_cancer, l.drogas AS drogas LIMIT 20")
    return {
        "nodos": {(r.get("label") or "Sin Etiqueta"): r.get("cantidad", 0) for r in nodos},
        "relaciones": {(r.get("tipo") or "Sin Tipo"): r.get("cantidad", 0) for r in relaciones},
        "detalles_literatura": literatura,
    }
