"""
Motor de evaluación: perfil + catálogo → estados auditables.

Contrato de estados de una opción (sobre sus criterios trivaluados):
- NO_APLICA: al menos un criterio dio NO (se informa cuál).
- CUMPLE: todos los criterios dieron SI.
- CONDICIONAL: ninguno dio NO pero alguno quedó INDETERMINADO; se informa
  qué dato del caso lo destrabaría.

La distinción entre CONDICIONAL y NO_APLICA es el producto: un caso sin ISH
no es HER2-negativo, y tratarlo como tal cerraría opciones que siguen abiertas.

Sobre las opciones condicionales se calcula el VALOR DE LA INFORMACIÓN: para
cada dato faltante, cuántas opciones bloquea y cuáles quedarían decididas si
se completara solo ese dato.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.dominio import perfil as P
from app.dominio.perfil import PerfilClinico
from app.dominio.reglas.catalogo import CATALOGO, ESTADO_VALIDACION, VERSION_REGLAS, Opcion
from app.dominio.tri import Tri


class Estado(str, Enum):
    CUMPLE = "cumple"
    CONDICIONAL = "condicional"
    NO_APLICA = "no_aplica"


@dataclass(frozen=True)
class ResultadoCriterio:
    id: str
    texto: str
    valor: Tri
    datos_faltantes: tuple[str, ...]


@dataclass(frozen=True)
class OpcionEvaluada:
    opcion: Opcion
    estado: Estado
    criterios: tuple[ResultadoCriterio, ...]
    notas: tuple[str, ...]

    @property
    def bloqueantes(self) -> tuple[str, ...]:
        """Datos faltantes de los criterios INDETERMINADOS (vacío si no es condicional)."""
        if self.estado is not Estado.CONDICIONAL:
            return ()
        datos = [d for c in self.criterios if c.valor is Tri.INDETERMINADO for d in c.datos_faltantes]
        return tuple(dict.fromkeys(datos))

    @property
    def criterios_no_cumplidos(self) -> tuple[ResultadoCriterio, ...]:
        return tuple(c for c in self.criterios if c.valor is Tri.NO)

    @property
    def requiere_revision(self) -> bool:
        """Condicional sin ningún dato faltante identificable: el criterio necesita lectura humana."""
        return self.estado is Estado.CONDICIONAL and not self.bloqueantes


@dataclass(frozen=True)
class ValorInformacion:
    dato: str
    etiqueta: str
    opciones_bloqueadas: tuple[str, ...]
    opciones_que_decide: tuple[str, ...]  # quedan decididas si se completa solo este dato


@dataclass(frozen=True)
class Evaluacion:
    version_reglas: str
    estado_validacion: str
    subtipo: str | None
    subtipos_compatibles: tuple[str, ...]
    metastasico: Tri
    estadio: str | None
    opciones: tuple[OpcionEvaluada, ...]
    valor_informacion: tuple[ValorInformacion, ...]
    datos_faltantes: tuple[str, ...]

    def por_estado(self, estado: Estado, tipo: str | None = None) -> tuple[OpcionEvaluada, ...]:
        return tuple(o for o in self.opciones if o.estado is estado and (tipo is None or o.opcion.tipo == tipo))

    @property
    def abiertas(self) -> tuple[OpcionEvaluada, ...]:
        """Opciones que cumplen o siguen condicionales: las que dirigen la búsqueda de evidencia."""
        return tuple(o for o in self.opciones if o.estado is not Estado.NO_APLICA)


def evaluar_opcion(opcion: Opcion, perfil: PerfilClinico) -> OpcionEvaluada:
    resultados = []
    for criterio in opcion.criterios:
        valor = criterio.evaluar(perfil)
        faltantes = criterio.datos_faltantes(perfil) if valor is Tri.INDETERMINADO else ()
        resultados.append(ResultadoCriterio(criterio.id, criterio.texto, valor, faltantes))

    if any(r.valor is Tri.NO for r in resultados):
        estado = Estado.NO_APLICA
    elif all(r.valor is Tri.SI for r in resultados):
        estado = Estado.CUMPLE
    else:
        estado = Estado.CONDICIONAL

    notas = opcion.notas(perfil) if opcion.notas and estado is not Estado.NO_APLICA else ()
    return OpcionEvaluada(opcion=opcion, estado=estado, criterios=tuple(resultados), notas=tuple(notas))


def _valor_informacion(opciones: tuple[OpcionEvaluada, ...]) -> tuple[ValorInformacion, ...]:
    condicionales = [o for o in opciones if o.estado is Estado.CONDICIONAL]
    datos = dict.fromkeys(d for o in condicionales for d in o.bloqueantes)
    valores = []
    for dato in datos:
        bloqueadas = tuple(o.opcion.id for o in condicionales if dato in o.bloqueantes)
        decide = tuple(o.opcion.id for o in condicionales if o.bloqueantes == (dato,))
        valores.append(ValorInformacion(dato, P.etiqueta_dato(dato), bloqueadas, decide))
    valores.sort(key=lambda v: (-len(v.opciones_bloqueadas), -len(v.opciones_que_decide), v.dato))
    return tuple(valores)


def evaluar(perfil: PerfilClinico, catalogo: tuple[Opcion, ...] = CATALOGO) -> Evaluacion:
    """Evalúa el perfil contra todo el catálogo. Pura y determinística."""
    opciones = tuple(evaluar_opcion(o, perfil) for o in catalogo)
    return Evaluacion(
        version_reglas=VERSION_REGLAS,
        estado_validacion=ESTADO_VALIDACION,
        subtipo=P.subtipo_molecular(perfil),
        subtipos_compatibles=P.subtipos_compatibles(perfil),
        metastasico=P.metastasico(perfil),
        estadio=P.estadio_efectivo(perfil),
        opciones=opciones,
        valor_informacion=_valor_informacion(opciones),
        datos_faltantes=tuple(d for d in P.ETIQUETAS_DATOS if _relevante_faltante(perfil, d)),
    )


# Datos básicos del caso que siempre se informan si faltan, además de los que
# bloquean alguna opción: sirven para que el médico vea el caso completo.
_DATOS_BASICOS = ("edad", "re", "rp", "her2_ihq", "ki67", "grado", "tnm", "estadificacion_sistemica", "ecog", "menopausia")


def _relevante_faltante(perfil: PerfilClinico, dato: str) -> bool:
    return dato in _DATOS_BASICOS and P.falta_dato(perfil, dato)
