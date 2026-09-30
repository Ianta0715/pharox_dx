"""Traducción de los objetos de dominio/servicio a los modelos de respuesta de la API."""
from __future__ import annotations

from dataclasses import asdict

from app.api.esquemas import (
    ConsultaResponse,
    CriterioCatalogo,
    CriterioSalida,
    EvaluacionSalida,
    EvidenciaSalida,
    FuenteFallidaSalida,
    OpcionCatalogo,
    OpcionSalida,
    PerfilEntrada,
    ReglasResponse,
    TrazaSalida,
    ValorInformacionSalida,
)
from app.dominio import perfil as P
from app.dominio.perfil import PerfilClinico
from app.dominio.reglas.catalogo import CATALOGO, ESTADO_VALIDACION, VERSION_REGLAS
from app.dominio.reglas.motor import Evaluacion
from app.dominio.tri import Tri
from app.servicios.copiloto import RespuestaCopiloto

_SOLO_INTERNOS = {"genes_mencionados", "menciona_marcadores_sericos"}


def perfil_a_salida(perfil: PerfilClinico) -> PerfilEntrada:
    datos = {k: v for k, v in asdict(perfil).items() if k not in _SOLO_INTERNOS and v is not None}
    return PerfilEntrada(**datos)


def evaluacion_a_salida(ev: Evaluacion) -> EvaluacionSalida:
    return EvaluacionSalida(
        version_reglas=ev.version_reglas,
        estado_validacion=ev.estado_validacion,
        subtipo=ev.subtipo,
        subtipos_compatibles=list(ev.subtipos_compatibles),
        escenario={Tri.SI: "metastasico", Tri.NO: "temprano", Tri.INDETERMINADO: "indeterminado"}[ev.metastasico],
        estadio=ev.estadio,
        datos_faltantes=[P.etiqueta_dato(d) for d in ev.datos_faltantes],
        opciones=[
            OpcionSalida(
                id=o.opcion.id,
                nombre=o.opcion.nombre,
                tipo=o.opcion.tipo,
                escenario=o.opcion.escenario,
                familia=o.opcion.familia,
                fuente=o.opcion.fuente,
                estado=o.estado.value,
                datos_faltantes=[P.etiqueta_dato(d) for d in o.bloqueantes],
                requiere_revision=o.requiere_revision,
                criterios=[
                    CriterioSalida(id=c.id, texto=c.texto, valor=c.valor.value, datos_faltantes=[P.etiqueta_dato(d) for d in c.datos_faltantes])
                    for c in o.criterios
                ],
                notas=list(o.notas),
            )
            for o in ev.opciones
        ],
        valor_informacion=[
            ValorInformacionSalida(
                dato=v.dato,
                etiqueta=v.etiqueta,
                opciones_bloqueadas=list(v.opciones_bloqueadas),
                opciones_que_decide=list(v.opciones_que_decide),
            )
            for v in ev.valor_informacion
        ],
    )


def _metodo_recuperacion(r: RespuestaCopiloto) -> str:
    d = r.dossier
    partes = ["Motor de reglas" if d.evaluacion is not None else "Consulta general"]
    if d.cypher:
        partes.append("Text-to-Cypher")
    if d.fuentes_consultadas:
        partes.append("Fuentes del grafo: " + ", ".join(d.fuentes_consultadas))
    if r.narrativa:
        partes.append("Redacción verificada")
    return " + ".join(partes)


def respuesta_a_salida(r: RespuestaCopiloto) -> ConsultaResponse:
    d = r.dossier
    return ConsultaResponse(
        success=True,
        modo=d.modo,
        motivo_modo=d.motivo_modo,
        perfil=perfil_a_salida(d.perfil),
        origen=d.origen,
        trazas=[TrazaSalida(**asdict(t)) for t in d.trazas],
        descartados=[TrazaSalida(**asdict(t)) for t in d.descartados],
        evaluacion=evaluacion_a_salida(d.evaluacion) if d.evaluacion is not None else None,
        evidencia=[
            EvidenciaSalida(
                id=e.id,
                fuente=e.fuente.value,
                titulo=e.titulo,
                detalle=e.detalle,
                nivel=e.nivel,
                url=e.url,
                referencias=list(e.referencias),
                opciones=list(e.opciones),
                sobre_otras_personas=e.sobre_otras_personas,
            )
            for e in d.evidencias
        ],
        fuentes_consultadas=list(d.fuentes_consultadas),
        fuentes_fallidas=[FuenteFallidaSalida(fuente=f.fuente, error=f.error) for f in d.fuentes_fallidas],
        narrativa=r.narrativa,
        modelo_narrativa=r.modelo,
        advertencias=list(r.advertencias),
        respuesta=r.markdown,
        cypher_utilizado=d.cypher or "",
        evidencia_recuperada="\n".join(f"[{e.id}] {e.titulo}: {e.detalle}" for e in d.evidencias),
        metodo_recuperacion=_metodo_recuperacion(r),
    )


def catalogo_a_salida() -> ReglasResponse:
    return ReglasResponse(
        version_reglas=VERSION_REGLAS,
        estado_validacion=ESTADO_VALIDACION,
        opciones=[
            OpcionCatalogo(
                id=o.id,
                nombre=o.nombre,
                tipo=o.tipo,
                escenario=o.escenario,
                familia=o.familia,
                fuente=o.fuente,
                resumen=o.resumen,
                criterios=[CriterioCatalogo(id=c.id, texto=c.texto) for c in o.criterios],
            )
            for o in CATALOGO
        ],
    )
