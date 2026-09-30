"""
Protocolos de tratamiento estándar (:ProtocoloTratamiento) y actualizaciones
de guías ASCO/ESMO (:ActualizacionProtocolo).

El esquema de un protocolo se devuelve TEXTUAL, tal como está en la base: es la
parte más sensible a citar mal (el orden de las fases decide si una droga va
antes o después de la cirugía). Nunca lo redacta el LLM.

Filtrado por escenario: la tabla tiene protocolos de enfermedad temprana
("I - III") y avanzada ("IV (Metastásico)") para el mismo subtipo. Antes se
devolvían todos los del subtipo, y a una paciente M0 RH+/HER2− se le agregaba
también el esquema de primera línea metastásica.
"""
from __future__ import annotations

import re

from app.conocimiento.evidencia import Evidencia, Fuente
from app.conocimiento.grafo import Lector
from app.dominio.tri import Tri


def protocolo_es_metastasico(protocolo: dict) -> bool:
    texto = f"{protocolo.get('estadio_tnm') or ''} {protocolo.get('intencion_linea') or ''}".upper()
    return bool(re.search(r"\bIV\b|METAST|AVANZAD", texto))


def protocolos_por_subtipos(lector: Lector, subtipos: tuple[str, ...], metastasico: Tri) -> list[dict]:
    if not subtipos:
        return []
    filas = lector(
        """
        MATCH (p:ProtocoloTratamiento)
        WHERE p.topografia_codigo CONTAINS 'C50' AND p.subtipo_molecular_match IN $subtipos
        RETURN p.id AS id, p.histologia_subtipo AS histologia_subtipo,
               p.biomarcadores_criticos AS biomarcadores_criticos, p.estadio_tnm AS estadio_tnm,
               p.intencion_linea AS intencion_linea, p.protocolo_esquema AS protocolo_esquema,
               p.modalidad AS modalidad, p.subtipo_molecular_match AS subtipo
        ORDER BY p.id
        """,
        {"subtipos": list(subtipos)},
    )
    if metastasico is Tri.INDETERMINADO:
        return filas
    quiere_metastasico = metastasico is Tri.SI
    return [f for f in filas if protocolo_es_metastasico(f) == quiere_metastasico]


def evidencia_protocolos(lector: Lector, subtipos: tuple[str, ...], metastasico: Tri, subtipo_confirmado: str | None) -> list[Evidencia]:
    evidencias = []
    for p in protocolos_por_subtipos(lector, subtipos, metastasico):
        condicionado = "" if subtipo_confirmado else f" — aplica solo si se confirma el subtipo {p['subtipo']}"
        evidencias.append(Evidencia(
            fuente=Fuente.PROTOCOLO,
            titulo=f"{p.get('histologia_subtipo') or '?'} · {p.get('biomarcadores_criticos') or '?'} · estadio {p.get('estadio_tnm') or '?'}{condicionado}",
            detalle=f"{p.get('protocolo_esquema') or '?'} ({p.get('modalidad') or '?'} · {p.get('intencion_linea') or '?'})",
            referencias=(p["id"],) if p.get("id") else (),
        ))
    return evidencias


def protocolo_estandar_por_subtipo(lector: Lector, subtipo: str) -> list[dict]:
    """Todos los protocolos de un subtipo (endpoint de consulta directa, sin filtrar por escenario)."""
    return lector(
        """
        MATCH (p:ProtocoloTratamiento {subtipo_molecular_match: $subtipo})
        RETURN p.id AS id, p.histologia_subtipo AS histologia_subtipo,
               p.biomarcadores_criticos AS biomarcadores_criticos, p.estadio_tnm AS estadio_tnm,
               p.intencion_linea AS intencion_linea, p.protocolo_esquema AS protocolo_esquema,
               p.modalidad AS modalidad
        ORDER BY p.id
        """,
        {"subtipo": subtipo},
    )


# ---------------------------------------------------------------------------
# Actualizaciones de guías (vigilancia de protocolos)
# ---------------------------------------------------------------------------
def listar_actualizaciones(lector: Lector, subtipo: str | None = None, limite: int = 20) -> list[dict]:
    filtro = "WHERE a.subtipos_detectados CONTAINS $subtipo" if subtipo else ""
    return lector(
        f"""
        MATCH (a:ActualizacionProtocolo) {filtro}
        RETURN a.id AS id, a.titulo AS titulo, a.resumen AS resumen, a.sociedad AS sociedad,
               a.fuente AS fuente, a.fecha_publicacion AS fecha_publicacion,
               a.subtipos_detectados AS subtipos_detectados, a.url AS url
        ORDER BY a.fecha_publicacion DESC
        LIMIT $limite
        """,
        {"subtipo": subtipo, "limite": limite},
    )


def evidencia_actualizaciones(lector: Lector, subtipos: tuple[str, ...], limite: int = 3) -> list[Evidencia]:
    filas = lector(
        """
        MATCH (a:ActualizacionProtocolo)
        WHERE size($subtipos) = 0 OR any(s IN $subtipos WHERE a.subtipos_detectados CONTAINS s)
        RETURN a.id AS id, a.titulo AS titulo, a.resumen AS resumen, a.sociedad AS sociedad,
               a.fecha_publicacion AS fecha, a.url AS url, a.fuente AS fuente
        ORDER BY a.fecha_publicacion DESC
        LIMIT $limite
        """,
        {"subtipos": list(subtipos), "limite": limite},
    )
    return [
        Evidencia(
            fuente=Fuente.ACTUALIZACION,
            titulo=f"{f.get('sociedad') or '?'} ({f.get('fecha') or 'fecha desconocida'}): {f.get('titulo') or '?'}",
            detalle=(f.get("resumen") or "sin resumen")[:600],
            url=f.get("url") or f.get("fuente"),
            referencias=(f"PMID:{f['id']}",) if str(f.get("id") or "").isdigit() else (),
        )
        for f in filas
    ]
