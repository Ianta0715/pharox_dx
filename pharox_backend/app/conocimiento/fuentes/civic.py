"""
Evidencia clínico-molecular de CIViC (Variante → Evidencia → Terapia/Enfermedad/Fuente).

Antes se buscaba por palabras clave del texto de la consulta. Ahora la búsqueda
la dirige el motor: genes y drogas de las opciones que quedaron abiertas
(CUMPLE o CONDICIONAL), más los genes que el perfil tiene mutados. Cada
evidencia se etiqueta con las opciones que contextualiza; la que no se puede
vincular a ninguna opción abierta ni a un gen del caso se descarta.
"""
from __future__ import annotations

from app.conocimiento.evidencia import Evidencia, Fuente
from app.conocimiento.grafo import Lector

# CIViC ordena la evidencia de A (validada) a E (inferencial).
_ORDEN_NIVEL = {"A": 0, "B": 1, "C": 2, "D": 3, "E": 4}


def evidencia_civic(
    lector: Lector,
    genes: tuple[str, ...],
    drogas_por_opcion: dict[str, tuple[str, ...]],
    genes_por_opcion: dict[str, tuple[str, ...]],
    limite: int = 8,
) -> list[Evidencia]:
    drogas = sorted({d for ds in drogas_por_opcion.values() for d in ds})
    if not genes and not drogas:
        return []

    filas = lector(
        """
        MATCH (v:Variante)-[:TIENE_EVIDENCIA]->(e:Evidencia)
        OPTIONAL MATCH (e)-[:INVOLUCRA_TERAPIA]->(t:Terapia)
        OPTIONAL MATCH (e)-[:ASOCIADA_A_ENFERMEDAD]->(en:Enfermedad)
        OPTIONAL MATCH (e)-[:RESPALDADA_POR]->(f:Fuente)
        WITH v, e, en,
             [x IN collect(DISTINCT toLower(t.nombre)) WHERE x IS NOT NULL] AS terapias,
             [x IN collect(DISTINCT f.pubmed_id) WHERE x IS NOT NULL] AS pmids
        WHERE v.gen IN $genes OR any(t IN terapias WHERE any(d IN $drogas WHERE t CONTAINS d))
        RETURN v.gen AS gen, v.nombre_variante AS variante, e.civic_id AS civic_id,
               e.descripcion AS descripcion, e.nivel AS nivel, e.significancia AS significancia,
               e.direccion AS direccion, en.nombre_mostrado AS enfermedad, terapias, pmids
        """,
        {"genes": list(genes), "drogas": drogas},
    )

    evidencias = []
    for f in filas:
        terapias = f.get("terapias") or []
        opciones = tuple(
            op for op, ds in drogas_por_opcion.items()
            if any(d in t for t in terapias for d in ds)
        ) + tuple(
            op for op, gs in genes_por_opcion.items()
            if f.get("gen") in gs and op not in drogas_por_opcion
        )
        opciones = tuple(dict.fromkeys(opciones))
        if not opciones and f.get("gen") not in genes:
            continue
        significancia = " · ".join(x for x in (f.get("significancia"), f.get("direccion")) if x)
        evidencias.append(Evidencia(
            fuente=Fuente.CIVIC,
            titulo=f"{f.get('gen') or '?'} {f.get('variante') or ''}".strip()
            + (f" en {f['enfermedad']}" if f.get("enfermedad") else "")
            + (f" — {', '.join(terapias)}" if terapias else ""),
            detalle=(f.get("descripcion") or "")[:700] + (f" ({significancia})" if significancia else ""),
            nivel=f.get("nivel"),
            url=f"https://civicdb.org/evidence/{f['civic_id']}" if f.get("civic_id") else None,
            referencias=tuple(f"PMID:{p}" for p in (f.get("pmids") or [])[:3]),
            opciones=opciones,
        ))

    # Primero lo que respalda una opción abierta; dentro de eso, por nivel de evidencia.
    evidencias.sort(key=lambda e: (not e.opciones, _ORDEN_NIVEL.get(e.nivel or "", 9), -len(e.opciones)))
    return evidencias[:limite]
