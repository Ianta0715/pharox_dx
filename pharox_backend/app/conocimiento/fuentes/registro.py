"""
Registro real de tumores de mama del Hospital Central - Mendoza (:RegistroTumor)
y el resumen de cohorte que lo combina con cBioPortal.

Como evidencia para una consulta se usa SOLO en forma agregada: cuántas
pacientes locales comparten el subtipo (o los subtipos todavía compatibles) y
el estadio. Antes se pasaban registros individuales al LLM y, como el registro
no trae tratamiento ni evolución, el modelo tendía a completarlos inventando.
"""
from __future__ import annotations

from app.conocimiento.evidencia import Evidencia, Fuente
from app.conocimiento.grafo import Lector

_FILTRO_MAMA = "r.topografia_codigo STARTS WITH 'C50'"  # excluye piel de mama (C44), ver registro_tumores_to_neo4j.py

_ETIQUETA_SUBTIPO = {
    "HER2_positivo": "HER2+",
    "Triple_negativo": "triple negativo",
    "RH_positivo_HER2_negativo": "RH+/HER2−",
}


def listar_registros(lector: Lector, subtipo: str | None = None, limite: int = 50) -> list[dict]:
    filtro = " AND r.subtipo_molecular = $subtipo" if subtipo else ""
    return lector(
        f"""
        MATCH (r:RegistroTumor) WHERE {_FILTRO_MAMA}{filtro}
        RETURN r.id AS id, r.edad AS edad, r.topografia_nombre AS topografia_nombre,
               r.estadio_clinico AS estadio_clinico, r.subtipo_molecular AS subtipo_molecular,
               r.receptor_estrogeno AS receptor_estrogeno, r.receptor_progesterona AS receptor_progesterona,
               r.her2 AS her2, r.hospital AS hospital, r.fecha_diagnostico AS fecha_diagnostico
        ORDER BY r.id
        LIMIT $limite
        """,
        {"subtipo": subtipo, "limite": limite},
    )


def obtener_registro(lector: Lector, registro_id: str) -> dict | None:
    filas = lector("MATCH (r:RegistroTumor {id: $id}) RETURN properties(r) AS r", {"id": registro_id})
    return filas[0]["r"] if filas else None


def obtener_subtipo(lector: Lector, registro_id: str) -> str | None:
    """Subtipo de un registro, "desconocido" si el dato no es concluyente, None si el registro no existe."""
    filas = lector("MATCH (r:RegistroTumor {id: $id}) RETURN r.subtipo_molecular AS subtipo", {"id": registro_id})
    if not filas:
        return None
    return filas[0]["subtipo"] or "desconocido"


def evidencia_cohorte_local(lector: Lector, subtipos: tuple[str, ...], estadio: str | None, her2_indeterminado: bool) -> list[Evidencia]:
    totales = lector(
        f"""
        MATCH (r:RegistroTumor) WHERE {_FILTRO_MAMA}
        RETURN count(r) AS total, sum(CASE WHEN r.her2 = 'Dudoso' THEN 1 ELSE 0 END) AS her2_dudoso
        """
    )
    if not totales or not totales[0]["total"]:
        return []
    por_subtipo = lector(
        f"""
        MATCH (r:RegistroTumor) WHERE {_FILTRO_MAMA} AND r.subtipo_molecular IN $subtipos
        RETURN r.subtipo_molecular AS subtipo, count(r) AS n,
               sum(CASE WHEN r.estadio_clinico = $estadio THEN 1 ELSE 0 END) AS n_estadio
        ORDER BY n DESC
        """,
        {"subtipos": list(subtipos), "estadio": estadio},
    )

    partes = [
        f"{_ETIQUETA_SUBTIPO.get(f['subtipo'], f['subtipo'])}: {f['n']}"
        + (f" ({f['n_estadio']} en estadio {estadio})" if estadio else "")
        for f in por_subtipo
    ]
    detalle = f"De {totales[0]['total']} registros de cáncer de mama: " + ("; ".join(partes) if partes else "sin subtipo comparable determinado") + "."
    if her2_indeterminado:
        detalle += f" {totales[0]['her2_dudoso']} registros tienen HER2 'Dudoso', como este caso sin ISH."
    detalle += (
        " El registro no incluye tratamiento recibido ni evolución: dimensiona la población local, "
        "no permite comparar resultados."
    )
    return [Evidencia(
        fuente=Fuente.REGISTRO,
        titulo="Pacientes con perfil comparable en el registro local",
        detalle=detalle,
        sobre_otras_personas=True,
    )]


def resumen_cohorte_real(lector: Lector) -> dict:
    """Resumen agregado: registro local (Mendoza) + frecuencias génicas de cBioPortal."""
    registro = lector(f"MATCH (r:RegistroTumor) WHERE {_FILTRO_MAMA} RETURN count(r) AS total, avg(r.edad) AS edad_promedio")[0]
    por_subtipo = lector(
        f"""
        MATCH (r:RegistroTumor) WHERE {_FILTRO_MAMA}
        RETURN coalesce(r.subtipo_molecular, 'desconocido') AS subtipo, count(r) AS total
        ORDER BY total DESC
        """
    )
    por_estadio = lector(
        f"""
        MATCH (r:RegistroTumor) WHERE {_FILTRO_MAMA}
        RETURN coalesce(r.estadio_clinico, 'sin registrar') AS estadio, count(r) AS total
        ORDER BY total DESC
        """
    )
    estudios = lector("MATCH (e:EstudioCBio) RETURN e.study_id AS study_id, e.nombre AS nombre, e.descripcion AS descripcion, e.n_pacientes AS n_pacientes")
    top_genes = lector(
        """
        MATCH (:EstudioCBio)-[:REPORTA_FRECUENCIA]->(f:FrecuenciaGenCBio)-[:SOBRE_GEN]->(g:GenCBio)
        RETURN g.hugo_symbol AS gen, f.porcentaje AS porcentaje, f.tipo_alteracion AS tipo_alteracion
        ORDER BY f.porcentaje DESC
        LIMIT 15
        """
    )
    edad = registro["edad_promedio"]
    return {
        "registro_tumores": {
            "total": registro["total"],
            "edad_promedio": round(edad, 1) if edad is not None else None,
            "por_subtipo": por_subtipo,
            "por_estadio": por_estadio,
        },
        "cbioportal": {"estudio": estudios[0] if estudios else None, "top_genes": top_genes},
    }
