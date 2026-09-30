"""
Copiloto clínico: el flujo completo de una consulta.

                         ┌── CASO (describe a una paciente) ─────────────────────────┐
    texto libre ─► interpretación ─► PerfilClinico ─► motor de reglas ─► evidencia dirigida
    (+ perfil            │                                (DECIDE)          por el motor
     estructurado)       └── GENERAL (pregunta de conocimiento) ──► evidencia por lo que
                                                                    nombra la pregunta
                  text-to-Cypher de solo lectura (si hay LLM) ──► "consulta estructurada"
                                          │
                        dossier markdown armado por código  ← la respuesta
                                          │
                  redacción LLM opcional + verificación     ← REFORMULA

El LLM está al final y es prescindible: si Ollama no está, la respuesta es el
dossier. Si su redacción menciona algo que no está en el dossier, se avisa
(política "advertir") o se descarta (política "estricta").
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Callable

from app.conocimiento.evidencia import Evidencia, numerar
from app.conocimiento.fuentes import registro as fuente_registro
from app.conocimiento.fuentes.semantica import Embedder
from app.conocimiento.grafo import Lector
from app.conocimiento.recuperacion import FuenteFallida, recuperar_evidencia, recuperar_evidencia_general
from app.dominio.consulta import Modo, ModoPedido, interpretar
from app.dominio.extraccion import Traza
from app.dominio.farmacos import terminos_busqueda
from app.dominio.perfil import PerfilClinico, fusionar
from app.dominio.registro import perfil_desde_registro
from app.dominio.reglas.motor import Evaluacion, evaluar
from app.lenguaje.redaccion import redactar
from app.lenguaje.verificacion import verificar_narrativa
from app.logging_config import get_logger
from app.servicios.exploracion import ejecutar_pregunta, evidencia_de_cypher
from app.servicios.presentacion import bloque_verificacion, dossier_general_markdown, dossier_markdown

logger = get_logger("pharox.servicios.copiloto")


@dataclass(frozen=True)
class Dependencias:
    """Todo lo que el copiloto necesita del mundo exterior, inyectable en tests."""

    lector: Lector
    embedder: Embedder | None = None
    crear_llm: Callable[[], Any] | None = None
    crear_llm_cypher: Callable[[], Any] | None = None
    nombre_modelo: str | None = None
    verificacion_estricta: bool = False


@dataclass(frozen=True)
class Dossier:
    modo: Modo
    motivo_modo: str
    perfil: PerfilClinico
    origen: dict[str, str]
    trazas: tuple[Traza, ...]
    descartados: tuple[Traza, ...]
    evaluacion: Evaluacion | None  # None en una consulta general
    evidencias: tuple[Evidencia, ...]
    fuentes_consultadas: tuple[str, ...]
    fuentes_fallidas: tuple[FuenteFallida, ...]
    cypher: str | None
    markdown: str


@dataclass(frozen=True)
class RespuestaCopiloto:
    dossier: Dossier
    narrativa: str | None
    modelo: str | None
    advertencias: tuple[str, ...]
    no_respaldado: tuple[str, ...] = ()  # entidades de la redacción sin respaldo (política "advertir")

    @property
    def markdown(self) -> str:
        if not self.narrativa:
            return self.dossier.markdown
        partes = [self.narrativa]
        if self.no_respaldado:
            partes.append(bloque_verificacion(self.no_respaldado))
        material = "del dossier de abajo; los estados vienen del motor de reglas" if self.dossier.modo == "caso" else "de la evidencia de abajo"
        partes.append(f"_Redactado por el modelo local {self.modelo or ''} a partir {material}._")
        partes.append(self.dossier.markdown)
        return "\n\n".join(partes)


def _origen(trazas: tuple[Traza, ...], estructurado: PerfilClinico | None, etiqueta_estructurado: str) -> dict[str, str]:
    origen: dict[str, str] = {}
    for t in trazas:
        clave = "variantes" if t.campo.startswith("gen_") else t.campo
        origen.setdefault(clave, f"texto: «{t.fragmento}»")
    if estructurado is not None:
        vacio = PerfilClinico()
        for f in fields(PerfilClinico):
            if getattr(estructurado, f.name) != getattr(vacio, f.name):
                origen[f.name] = etiqueta_estructurado
    return origen


def _consulta_estructurada(consulta: str, deps: Dependencias) -> tuple[Evidencia | None, str | None, FuenteFallida | None]:
    """Paso text-to-Cypher. Nunca levanta: un Cypher inválido o vacío solo significa una fuente menos."""
    if deps.crear_llm_cypher is None:
        return None, None, None
    try:
        resultado = ejecutar_pregunta(consulta, deps.lector, deps.crear_llm_cypher())
    except ValueError as e:
        logger.warning(f"[Copiloto] Cypher bloqueado por seguridad: {e}")
        return None, None, FuenteFallida("consulta_estructurada", f"Cypher bloqueado: {e}")
    except Exception as e:
        logger.warning(f"[Copiloto] Falló la consulta estructurada: {e}")
        return None, None, FuenteFallida("consulta_estructurada", str(e)[:200])
    return evidencia_de_cypher(resultado), resultado.cypher, None


def _narrar(dossier: Dossier, consulta: str, deps: Dependencias) -> RespuestaCopiloto:
    def sin_redaccion(*advertencias: str) -> RespuestaCopiloto:
        return RespuestaCopiloto(dossier, None, None, advertencias)

    if deps.crear_llm is None:
        return sin_redaccion("Redacción en prosa desactivada: se devuelve el material determinístico.")
    try:
        narrativa = redactar(consulta, dossier.markdown, deps.crear_llm(), dossier.modo)
    except Exception as e:
        logger.error(f"[Copiloto] Falló la redacción con el LLM: {e}")
        return sin_redaccion(f"El modelo local no respondió ({str(e)[:120]}); se devuelve el material determinístico.")

    hallazgos = verificar_narrativa(narrativa, f"{dossier.markdown}\n{consulta}", {e.id for e in dossier.evidencias})
    no_respaldadas = tuple(h.detalle for h in hallazgos if h.tipo == "entidad_no_respaldada")
    consejos = tuple(h.detalle for h in hallazgos if h.tipo == "consejo")
    if no_respaldadas and deps.verificacion_estricta:
        logger.warning(f"[Copiloto] Redacción descartada (verificación estricta): {no_respaldadas}")
        return sin_redaccion("Se descartó la redacción del modelo porque mencionaba datos que no están en la evidencia:", *no_respaldadas)
    if no_respaldadas:
        logger.warning(f"[Copiloto] Redacción con entidades no respaldadas: {no_respaldadas}")
    return RespuestaCopiloto(dossier, narrativa, deps.nombre_modelo, no_respaldadas + consejos, no_respaldadas)


def consultar(
    consulta: str | None,
    perfil_estructurado: PerfilClinico | None,
    deps: Dependencias,
    narrar: bool = True,
    con_evidencia: bool = True,
    modo: ModoPedido = "auto",
    usar_cypher: bool = True,
) -> RespuestaCopiloto:
    """Consulta clínica en texto libre (caso o pregunta general) y/o perfil estructurado."""
    interp = interpretar(consulta, modo, hay_perfil_estructurado=perfil_estructurado is not None)
    ex = interp.extraccion
    perfil = fusionar(ex.perfil, perfil_estructurado) if perfil_estructurado else ex.perfil
    origen = _origen(ex.trazas, perfil_estructurado, "perfil estructurado")

    ev_cypher, cypher, falla_cypher = (None, None, None)
    if con_evidencia and usar_cypher and consulta:
        ev_cypher, cypher, falla_cypher = _consulta_estructurada(consulta, deps)

    evaluacion = evaluar(perfil) if interp.modo == "caso" else None
    if not con_evidencia:
        evidencias, consultadas, fallidas = [], (), ()
    elif evaluacion is not None:
        rec = recuperar_evidencia(deps.lector, perfil, evaluacion, consulta, deps.embedder)
        evidencias, consultadas, fallidas = list(rec.evidencias), rec.fuentes_consultadas, rec.fuentes_fallidas
    else:
        rec = recuperar_evidencia_general(
            deps.lector, perfil, terminos_busqueda(interp.farmacos), interp.temas, consulta or "", deps.embedder
        )
        evidencias, consultadas, fallidas = list(rec.evidencias), rec.fuentes_consultadas, rec.fuentes_fallidas

    # En un caso la consulta estructurada complementa al motor (va al final); en
    # una pregunta general suele ser la respuesta directa (va primero).
    if ev_cypher is not None:
        evidencias = [ev_cypher] + evidencias if evaluacion is None else evidencias + [ev_cypher]
    if falla_cypher is not None:
        fallidas = (*fallidas, falla_cypher)
    evidencias_numeradas = tuple(numerar(evidencias))

    if evaluacion is not None:
        markdown = dossier_markdown(perfil, origen, ex.descartados, evaluacion, evidencias_numeradas, fallidas)
    else:
        markdown = dossier_general_markdown(perfil, interp.farmacos, interp.motivo_modo, evidencias_numeradas, fallidas)
    dossier = Dossier(
        interp.modo, interp.motivo_modo, perfil, origen, ex.trazas, ex.descartados, evaluacion,
        evidencias_numeradas, tuple(consultadas), tuple(fallidas), cypher, markdown,
    )

    if narrar and consulta:
        return _narrar(dossier, consulta, deps)
    return RespuestaCopiloto(dossier, None, None, ())


def evaluar_registro(registro_id: str, deps: Dependencias, con_evidencia: bool = True) -> RespuestaCopiloto | None:
    """Evalúa con el motor de reglas a una paciente real del registro. None si el registro no existe."""
    registro = fuente_registro.obtener_registro(deps.lector, registro_id)
    if registro is None:
        return None
    perfil = perfil_desde_registro(registro)
    origen = _origen((), perfil, f"registro {registro_id}")
    evaluacion = evaluar(perfil)
    if con_evidencia:
        rec = recuperar_evidencia(deps.lector, perfil, evaluacion, None, deps.embedder, registro_id)
        evidencias, consultadas, fallidas = rec.evidencias, rec.fuentes_consultadas, rec.fuentes_fallidas
    else:
        evidencias, consultadas, fallidas = (), (), ()
    markdown = dossier_markdown(perfil, origen, (), evaluacion, evidencias, fallidas)
    dossier = Dossier(
        "caso", f"registro real {registro_id}", perfil, origen, (), (), evaluacion,
        evidencias, consultadas, fallidas, None, markdown,
    )
    return RespuestaCopiloto(dossier, None, None, ())
