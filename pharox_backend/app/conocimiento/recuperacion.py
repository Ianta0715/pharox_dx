"""
Recuperación de evidencia del grafo, en dos variantes según el tipo de consulta
(ver dominio/consulta.py):

- `recuperar_evidencia` (CASO): dirigida por el motor de reglas. El motor ya
  resolvió subtipos compatibles, escenario y opciones abiertas; acá se traduce
  eso en consultas concretas a cada subgrafo.
- `recuperar_evidencia_general` (GENERAL): dirigida por lo que la pregunta
  nombra — genes, fármacos, subtipo, escenario y temas (ensayos, registro,
  protocolos, guías) —, sin evaluar opciones terapéuticas.

En ambas, cada fuente devuelve `Evidencia` tipada, y una fuente que falla no
tumba la consulta: queda en `fuentes_fallidas`. Si Neo4j no está disponible,
el resto de las fuentes se saltea en vez de esperar el timeout de cada una.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable

from neo4j.exceptions import ServiceUnavailable

from app.conocimiento.evidencia import Evidencia, numerar
from app.conocimiento.fuentes import civic, ensayos, genomica, protocolos, registro, semantica
from app.conocimiento.fuentes.semantica import Embedder
from app.conocimiento.grafo import Lector
from app.dominio import perfil as P
from app.dominio.ensayos import paciente_para_ensayos
from app.dominio.perfil import PerfilClinico
from app.dominio.reglas.motor import Evaluacion
from app.dominio.tri import Tri
from app.logging_config import get_logger

logger = get_logger("pharox.conocimiento.recuperacion")

_GENES_GERMINALES = ("BRCA1", "BRCA2", "PALB2")
NEO4J_NO_DISPONIBLE = "Neo4j no disponible"

Tarea = Callable[[], list[Evidencia]]


@dataclass(frozen=True)
class FuenteFallida:
    fuente: str
    error: str


@dataclass(frozen=True)
class ResultadoRecuperacion:
    evidencias: tuple[Evidencia, ...]
    fuentes_consultadas: tuple[str, ...]
    fuentes_fallidas: tuple[FuenteFallida, ...]


class _Ejecutor:
    """Corre las fuentes en orden, junta evidencia y registra fallas."""

    def __init__(self) -> None:
        self.evidencias: list[Evidencia] = []
        self.consultadas: list[str] = []
        self.fallidas: list[FuenteFallida] = []
        self.grafo_caido = False

    def correr(self, nombre: str, tarea: Tarea) -> None:
        if self.grafo_caido:
            self.fallidas.append(FuenteFallida(nombre, NEO4J_NO_DISPONIBLE))
            return
        try:
            self.evidencias.extend(tarea())
            self.consultadas.append(nombre)
        except ServiceUnavailable as e:
            logger.error(f"[Recuperación] {NEO4J_NO_DISPONIBLE}: {e}")
            self.grafo_caido = True
            self.fallidas.append(FuenteFallida(nombre, NEO4J_NO_DISPONIBLE))
        except Exception as e:
            logger.error(f"[Recuperación] Falló la fuente '{nombre}': {e}")
            self.fallidas.append(FuenteFallida(nombre, str(e)[:200]))

    def busqueda_semantica(self, lector: Lector, embedder: Embedder | None, texto: str) -> None:
        if embedder is None:
            self.fallidas.append(FuenteFallida("literatura_y_casos", "búsqueda vectorial desactivada (sin embeddings)"))
            return
        if self.grafo_caido:
            self.fallidas.append(FuenteFallida("literatura_y_casos", NEO4J_NO_DISPONIBLE))
            return
        try:
            vector = embedder(texto)
        except Exception as e:
            logger.error(f"[Recuperación] No se pudo generar el embedding: {e}")
            self.fallidas.append(FuenteFallida("literatura_y_casos", f"embeddings no disponibles: {str(e)[:150]}"))
            return
        self.correr("literatura", lambda: semantica.evidencia_literatura(lector, vector))
        self.correr("casos_clinicos", lambda: semantica.evidencia_casos(lector, vector))

    def resultado(self) -> ResultadoRecuperacion:
        return ResultadoRecuperacion(tuple(numerar(self.evidencias)), tuple(self.consultadas), tuple(self.fallidas))


def _genes_mutados(perfil: PerfilClinico) -> list[str]:
    return [g for g, mutado in perfil.variantes.items() if mutado]


# ---------------------------------------------------------------------------
# CASO: dirigida por el motor de reglas
# ---------------------------------------------------------------------------
def texto_busqueda_desde_perfil(perfil: PerfilClinico, evaluacion: Evaluacion) -> str:
    """Consulta en inglés para la búsqueda vectorial cuando no hay texto libre (p. ej. un registro)."""
    etiquetas = {
        P.SUBTIPO_HER2_POSITIVO: "HER2-positive",
        P.SUBTIPO_TRIPLE_NEGATIVO: "triple-negative",
        P.SUBTIPO_RH_POSITIVO_HER2_NEGATIVO: "hormone receptor-positive HER2-negative",
    }
    partes = ["breast cancer"] + [etiquetas[s] for s in evaluacion.subtipos_compatibles]
    partes.append({"si": "metastatic", "no": "early"}.get(evaluacion.metastasico.value, ""))
    partes += [o.opcion.nombre for o in evaluacion.abiertas if o.opcion.tipo == "tratamiento"][:3]
    return " ".join(p for p in partes if p)


def recuperar_evidencia(
    lector: Lector,
    perfil: PerfilClinico,
    evaluacion: Evaluacion,
    texto_consulta: str | None = None,
    embedder: Embedder | None = None,
    registro_id: str | None = None,
) -> ResultadoRecuperacion:
    abiertas = evaluacion.abiertas
    drogas_por_opcion = {o.opcion.id: o.opcion.drogas for o in abiertas if o.opcion.drogas}
    genes_por_opcion = {o.opcion.id: o.opcion.genes for o in abiertas if o.opcion.genes}
    genes = tuple(dict.fromkeys(
        [g for gs in genes_por_opcion.values() for g in gs] + _genes_mutados(perfil) + list(perfil.genes_mencionados)
    ))
    genes_sin_estudiar = tuple(g for g in genes if g not in perfil.variantes)
    genes_germinales = tuple(g for g in genes if g in _GENES_GERMINALES) if perfil.brca_germinal != "no_mutado" else ()
    subtipos = evaluacion.subtipos_compatibles
    her2_indeterminado = P.her2_positivo(perfil) is Tri.INDETERMINADO

    e = _Ejecutor()
    e.correr("protocolos", lambda: protocolos.evidencia_protocolos(lector, subtipos, evaluacion.metastasico, evaluacion.subtipo))
    e.correr("civic", lambda: civic.evidencia_civic(lector, genes, drogas_por_opcion, genes_por_opcion))
    e.correr("ensayos", lambda: ensayos.evidencia_ensayos(lector, paciente_para_ensayos(perfil), subtipos, evaluacion.metastasico, drogas_por_opcion))
    if registro_id:
        e.correr("elegibilidad_persistida", lambda: ensayos.evidencia_elegibilidad_persistida(lector, registro_id))
    e.correr("cbioportal", lambda: genomica.evidencia_cbioportal(lector, genes_sin_estudiar, genes_por_opcion))
    e.correr("clinvar", lambda: genomica.evidencia_clinvar(lector, genes_germinales, genes_por_opcion))
    e.correr("registro_local", lambda: registro.evidencia_cohorte_local(lector, subtipos, evaluacion.estadio, her2_indeterminado))
    e.correr("actualizaciones_guias", lambda: protocolos.evidencia_actualizaciones(lector, subtipos))
    e.busqueda_semantica(lector, embedder, texto_consulta or texto_busqueda_desde_perfil(perfil, evaluacion))
    return e.resultado()


# ---------------------------------------------------------------------------
# GENERAL: dirigida por lo que nombra la pregunta
# ---------------------------------------------------------------------------
def recuperar_evidencia_general(
    lector: Lector,
    perfil: PerfilClinico,
    terminos_farmacos: tuple[str, ...],
    temas: frozenset[str],
    texto_consulta: str,
    embedder: Embedder | None = None,
) -> ResultadoRecuperacion:
    """
    Una fuente se consulta si la pregunta la nombra (tema) o si la acota (genes,
    fármacos, subtipo o escenario): sin ninguna señal, traer "cinco ensayos
    cualquiera" o "todos los protocolos" solo agregaría ruido a la respuesta.
    """
    genes = tuple(dict.fromkeys(list(perfil.genes_mencionados) + _genes_mutados(perfil)))
    subtipos = P.subtipos_compatibles(perfil)
    subtipo_acotado = len(subtipos) < len(P.SUBTIPOS)
    metastasico = P.metastasico(perfil)
    terminos = {"consulta": terminos_farmacos + genes} if (terminos_farmacos or genes) else {}
    acotada = bool(terminos) or subtipo_acotado or metastasico is not Tri.INDETERMINADO

    def sin_opciones(tarea: Tarea) -> Tarea:
        # En una consulta general no hay opciones del catálogo a las que vincular la evidencia.
        return lambda: [replace(ev, opciones=()) for ev in tarea()]

    e = _Ejecutor()
    if subtipo_acotado or "protocolos" in temas:
        e.correr("protocolos", lambda: protocolos.evidencia_protocolos(lector, subtipos, metastasico, P.subtipo_molecular(perfil)))
    if genes or terminos_farmacos:
        e.correr("civic", sin_opciones(lambda: civic.evidencia_civic(
            lector, genes, {"consulta": terminos_farmacos} if terminos_farmacos else {}, {"consulta": genes} if genes else {},
        )))
    if "ensayos" in temas or acotada:
        e.correr("ensayos", sin_opciones(lambda: ensayos.evidencia_ensayos(
            lector, paciente_para_ensayos(perfil), subtipos, metastasico, terminos,
            exigir_coincidencia=bool(terminos), hay_paciente=False,
        )))
    if genes:
        e.correr("cbioportal", lambda: genomica.evidencia_cbioportal(lector, genes, {}))
        germinales = tuple(g for g in genes if g in _GENES_GERMINALES)
        if germinales:
            e.correr("clinvar", lambda: genomica.evidencia_clinvar(lector, germinales, {}))
    if "registro" in temas or subtipo_acotado:
        e.correr("registro_local", lambda: registro.evidencia_cohorte_local(lector, subtipos, P.estadio_efectivo(perfil), False))
    if "guias" in temas or subtipo_acotado:
        e.correr("actualizaciones_guias", lambda: protocolos.evidencia_actualizaciones(lector, subtipos if subtipo_acotado else ()))
    e.busqueda_semantica(lector, embedder, texto_consulta)
    return e.resultado()
