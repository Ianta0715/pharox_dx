"""
Ensayos clínicos reclutando (:EnsayoClinico, ClinicalTrials.gov) y elegibilidad
persistida (:RegistroTumor)-[:HABILITA|CONDICIONA|EXCLUYE_TRIAL]->(:EnsayoClinico).

Los candidatos se filtran en tres pasos, todos determinísticos:
1. Cypher: reclutando y subtipo compatible (o ensayo sin restricción de subtipo).
2. Escenario: un ensayo solo de enfermedad avanzada no se ofrece a una paciente M0.
3. dominio/ensayos.evaluar_criterios_deterministicos: sexo y edad.
Se ordenan por cuántos términos buscados (drogas de las opciones abiertas, o
fármacos y genes de una consulta general) aparecen en sus intervenciones,
condiciones o título. Nunca se concluye HABILITA: los criterios en texto libre
quedan explícitamente pendientes.
"""
from __future__ import annotations

from app.conocimiento.evidencia import Evidencia, Fuente
from app.conocimiento.grafo import Lector
from app.dominio.ensayos import escenario_del_ensayo, evaluar_criterios_deterministicos
from app.dominio.tri import Tri


def evidencia_ensayos(
    lector: Lector,
    paciente: dict,
    subtipos: tuple[str, ...],
    metastasico: Tri,
    drogas_por_opcion: dict[str, tuple[str, ...]],
    limite: int = 5,
    exigir_coincidencia: bool = False,
    hay_paciente: bool = True,
) -> list[Evidencia]:
    """
    `exigir_coincidencia`: solo ensayos que nombran algún término buscado (consulta
    general sobre un fármaco o gen). `hay_paciente`: en una consulta general no hay
    paciente a quien aplicarle el filtro de sexo/edad, y no se lo menciona.
    """
    filas = lector(
        """
        MATCH (e:EnsayoClinico)
        WHERE e.estado = 'RECRUITING'
          AND (size(coalesce(e.subtipos_relacionados, [])) = 0
               OR any(s IN e.subtipos_relacionados WHERE s IN $subtipos))
        RETURN e {.nct_id, .titulo, .fases, .estado, .condiciones, .intervenciones, .url,
                  .sexo, .edad_minima_anios, .edad_maxima_anios, .subtipos_relacionados, .resumen} AS ensayo
        LIMIT 500
        """,
        {"subtipos": list(subtipos)},
    )

    candidatos = []
    for fila in filas:
        ensayo = fila["ensayo"]
        escenario = escenario_del_ensayo(ensayo)
        if metastasico is Tri.NO and escenario == "metastasico":
            continue
        if metastasico is Tri.SI and escenario == "temprano":
            continue
        resultado = evaluar_criterios_deterministicos(paciente, ensayo)
        if resultado["veredicto"] == "EXCLUYE":
            continue

        texto = " ".join(
            (ensayo.get("intervenciones") or []) + (ensayo.get("condiciones") or []) + [ensayo.get("titulo") or ""]
        ).lower()
        opciones = tuple(op for op, terminos in drogas_por_opcion.items() if any(t.lower() in texto for t in terminos))
        if exigir_coincidencia and not opciones:
            continue
        puntaje =3 * len(opciones) + (1 if ensayo.get("subtipos_relacionados") else 0) + (1 if escenario else 0)
        candidatos.append((puntaje, ensayo, opciones, resultado, escenario))

    candidatos.sort(key=lambda c: (-c[0], c[1].get("nct_id") or ""))
    evidencias = []
    for _puntaje, ensayo, opciones, resultado, escenario in candidatos[:limite]:
        fases = ", ".join(ensayo.get("fases") or []) or "sin fase"
        intervenciones = ", ".join((ensayo.get("intervenciones") or [])[:5]) or "no informadas"
        detalle = f"{fases} · reclutando · escenario: {escenario or 'no especificado'} · intervenciones: {intervenciones}."
        if hay_paciente:
            detalle += f" Filtro determinístico: {resultado['motivo']} {resultado['criterio_pendiente'] or ''}"
        evidencias.append(Evidencia(
            fuente=Fuente.ENSAYO,
            titulo=f"{ensayo.get('nct_id')} — {ensayo.get('titulo') or 'sin título'}",
            detalle=detalle.strip(),
            url=ensayo.get("url"),
            referencias=(ensayo["nct_id"],) if ensayo.get("nct_id") else (),
            opciones=opciones,
        ))
    return evidencias


def elegibilidad_persistida(lector: Lector, paciente_id: str) -> list[dict]:
    """Veredictos ya calculados por app/gold/reglas_elegibilidad_trials.py. Lectura directa, sin recalcular."""
    return lector(
        """
        MATCH (p:RegistroTumor {id: $paciente_id})-[r]->(e:EnsayoClinico)
        WHERE type(r) IN ['HABILITA_TRIAL', 'CONDICIONA_TRIAL', 'EXCLUYE_TRIAL']
        RETURN type(r) AS veredicto, e.nct_id AS nct_id, e.titulo AS titulo, e.url AS url,
               r.motivo AS motivo, r.criterio_pendiente AS criterio_pendiente,
               r.regla AS regla, r.fecha_evaluacion AS fecha_evaluacion
        ORDER BY r.fecha_evaluacion DESC
        """,
        {"paciente_id": paciente_id},
    )


def evidencia_elegibilidad_persistida(lector: Lector, paciente_id: str) -> list[Evidencia]:
    return [
        Evidencia(
            fuente=Fuente.ELEGIBILIDAD,
            titulo=f"{f.get('nct_id')} — {f.get('titulo') or ''} [{f.get('veredicto')}]",
            detalle=f"{f.get('motivo') or ''} (regla: {f.get('regla') or '?'}, evaluado {f.get('fecha_evaluacion') or '?'})"
            + (f" Pendiente: {f['criterio_pendiente']}" if f.get("criterio_pendiente") else ""),
            url=f.get("url"),
            referencias=(f["nct_id"],) if f.get("nct_id") else (),
        )
        for f in elegibilidad_persistida(lector, paciente_id)
        if f.get("veredicto") != "EXCLUYE_TRIAL"
    ]
