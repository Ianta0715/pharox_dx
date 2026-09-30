"""
Contexto genómico poblacional: frecuencia de alteración (cBioPortal) y
variantes clasificadas (ClinVar).

Ninguna de las dos describe a la paciente. cBioPortal responde "¿qué tan
probable es encontrar esta alteración si se estudia?" — el complemento natural
del valor de la información del motor. ClinVar se resume por clasificación
clínica en vez de listar dos variantes sueltas, que antes el LLM llegaba a
presentar como si fueran hallazgos del caso.
"""
from __future__ import annotations

from app.conocimiento.evidencia import Evidencia, Fuente
from app.conocimiento.grafo import Lector


def evidencia_cbioportal(lector: Lector, genes: tuple[str, ...], genes_por_opcion: dict[str, tuple[str, ...]]) -> list[Evidencia]:
    if not genes:
        return []
    filas = lector(
        """
        MATCH (est:EstudioCBio)-[:REPORTA_FRECUENCIA]->(f:FrecuenciaGenCBio)-[:SOBRE_GEN]->(g:GenCBio)
        WHERE g.hugo_symbol IN $genes
        RETURN g.hugo_symbol AS gen, f.tipo_alteracion AS tipo_alteracion, f.porcentaje AS porcentaje,
               f.n_alterados AS n_alterados, f.n_perfilados AS n_perfilados,
               est.nombre AS estudio, est.study_id AS study_id
        ORDER BY f.porcentaje DESC
        """,
        {"genes": list(genes)},
    )
    return [
        Evidencia(
            fuente=Fuente.CBIOPORTAL,
            titulo=f"Frecuencia de alteración de {f['gen']} ({f.get('tipo_alteracion') or 'alteración'})",
            detalle=(
                f"{f.get('porcentaje') or 0}% ({f.get('n_alterados') or 0}/{f.get('n_perfilados') or 0} casos) "
                f"en {f.get('estudio') or 'estudio público'}. Es frecuencia poblacional, no un resultado de esta paciente."
            ),
            url=f"https://www.cbioportal.org/study/summary?id={f['study_id']}" if f.get("study_id") else None,
            opciones=tuple(op for op, gs in genes_por_opcion.items() if f["gen"] in gs),
            sobre_otras_personas=True,
        )
        for f in filas
    ]


def evidencia_clinvar(lector: Lector, genes: tuple[str, ...], genes_por_opcion: dict[str, tuple[str, ...]]) -> list[Evidencia]:
    if not genes:
        return []
    filas = lector(
        """
        MATCH (v:VarianteClinVar) WHERE v.gen IN $genes
        RETURN v.gen AS gen, coalesce(v.clasificacion_clinica, 'sin clasificar') AS clasificacion, count(v) AS n
        ORDER BY gen, n DESC
        """,
        {"genes": list(genes)},
    )
    por_gen: dict[str, list[str]] = {}
    for f in filas:
        por_gen.setdefault(f["gen"], []).append(f"{f['n']} {f['clasificacion']}")
    return [
        Evidencia(
            fuente=Fuente.CLINVAR,
            titulo=f"Variantes de {gen} registradas en ClinVar (base local)",
            detalle=(
                f"{'; '.join(clasificaciones)}. Referencia para interpretar un eventual resultado; "
                "no es un hallazgo de esta paciente."
            ),
            url=f"https://www.ncbi.nlm.nih.gov/clinvar/?term={gen}%5Bgene%5D",
            opciones=tuple(op for op, gs in genes_por_opcion.items() if gen in gs),
            sobre_otras_personas=True,
        )
        for gen, clasificaciones in por_gen.items()
    ]
