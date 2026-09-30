"""
Modelos Pydantic de entrada y salida. Documentan en /docs la forma real de
cada endpoint y validan el perfil estructurado antes de que llegue al motor.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.dominio.perfil import PerfilClinico

# ---------------------------------------------------------------------------
# Perfil clínico estructurado (entrada)
# ---------------------------------------------------------------------------
Receptor = Literal["positivo", "negativo"]


class PerfilEntrada(BaseModel):
    """
    Perfil clínico estructurado. Todos los campos son opcionales: lo que no se
    envía queda INDETERMINADO para el motor, nunca negativo. Si se envía junto
    con un texto libre, cada campo presente acá tiene prioridad sobre lo
    extraído del texto.
    """

    model_config = ConfigDict(extra="forbid")

    edad: int | None = Field(None, ge=0, le=120)
    sexo: Literal["F", "M"] | None = None
    re_pct: int | None = Field(None, ge=0, le=100, description="Receptor de estrógeno, % de células teñidas")
    re: Receptor | None = Field(None, description="Estado de RE si no se informa porcentaje")
    rp_pct: int | None = Field(None, ge=0, le=100)
    rp: Receptor | None = None
    her2_ihq: int | None = Field(None, ge=0, le=3, description="Score de IHQ: 0, 1, 2 o 3 (+)")
    her2_ish: Literal["amplificado", "no_amplificado"] | None = None
    her2_informado: Receptor | None = Field(None, description="Estado HER2 final si no hay score de IHQ")
    her2_low_informado: bool | None = None
    ki67_pct: int | None = Field(None, ge=0, le=100)
    grado: int | None = Field(None, ge=1, le=3)
    t: str | None = Field(None, pattern=r"^T(is|X|[0-4](mi|[a-d])?)$", examples=["T2"])
    n: str | None = Field(None, pattern=r"^N(X|[0-3](mi|[a-c])?)$", examples=["N1"])
    m: Literal["M0", "M1"] | None = None
    n_patologico: bool | None = Field(None, description="True si N es patológico (pN/ypN)")
    estadio: str | None = Field(None, pattern=r"^(0|IS|IV|I{1,3}[ABC]?)$", examples=["IIB"])
    tamano_cm: float | None = Field(None, gt=0, le=30)
    ganglios_positivos: int | None = Field(None, ge=0, le=80)
    metastasico: bool | None = None
    neoadyuvancia_previa: bool | None = None
    respuesta_patologica_completa: bool | None = None
    cps_eg: int | None = Field(None, ge=0, le=6)
    quimio_previa_metastasico: bool | None = None
    endocrino_previo_metastasico: bool | None = None
    anti_her2_previo_metastasico: bool | None = None
    lineas_previas_metastasico: int | None = Field(None, ge=0, le=20)
    ecog: int | None = Field(None, ge=0, le=4)
    menopausia: Literal["pre", "post"] | None = None
    brca_germinal: Literal["mutado", "no_mutado"] | None = None
    variantes: dict[str, bool] = Field(default_factory=dict, description="Gen → true (mutado) / false (estudiado, sin mutación)")
    pdl1_cps: int | None = Field(None, ge=0, le=100)
    recurrence_score: int | None = Field(None, ge=0, le=100)
    mammaprint_alto_riesgo: bool | None = None

    def a_dominio(self) -> PerfilClinico:
        datos = self.model_dump(exclude_none=True)
        datos["variantes"] = {g.upper(): v for g, v in self.variantes.items()}
        return PerfilClinico(**datos)


class ConsultaRequest(BaseModel):
    consulta: str | None = Field(None, max_length=8000, description="Consulta del médico en texto libre")
    perfil: PerfilEntrada | None = Field(None, description="Perfil estructurado; tiene prioridad sobre el texto")
    tipo_cancer: str = Field("Cáncer de Mama", description="Por ahora el motor solo cubre cáncer de mama")
    narrar: bool = Field(True, description="Pedir al modelo local una redacción en prosa del dossier")
    incluir_evidencia: bool = Field(True, description="Consultar el grafo de conocimiento")
    modo: Literal["auto", "caso", "general"] = Field(
        "auto",
        description="auto: se decide por la consulta. caso: aplicar el motor de reglas al perfil. "
        "general: pregunta de conocimiento, sin evaluar opciones.",
    )

    @model_validator(mode="after")
    def _algo_para_evaluar(self):
        if not (self.consulta and self.consulta.strip()) and self.perfil is None:
            raise ValueError("Se requiere 'consulta' (texto) o 'perfil' (estructurado).")
        return self


# ---------------------------------------------------------------------------
# Salida del copiloto
# ---------------------------------------------------------------------------
class TrazaSalida(BaseModel):
    campo: str
    valor: str
    fragmento: str


class CriterioSalida(BaseModel):
    id: str
    texto: str
    valor: Literal["si", "no", "indeterminado"]
    datos_faltantes: list[str]


class OpcionSalida(BaseModel):
    id: str
    nombre: str
    tipo: Literal["tratamiento", "estudio"]
    escenario: Literal["temprano", "metastasico", "cualquiera"]
    familia: str
    fuente: str
    estado: Literal["cumple", "condicional", "no_aplica"]
    datos_faltantes: list[str]
    requiere_revision: bool
    criterios: list[CriterioSalida]
    notas: list[str]


class ValorInformacionSalida(BaseModel):
    dato: str
    etiqueta: str
    opciones_bloqueadas: list[str]
    opciones_que_decide: list[str]


class EvaluacionSalida(BaseModel):
    version_reglas: str
    estado_validacion: str
    subtipo: str | None
    subtipos_compatibles: list[str]
    escenario: Literal["temprano", "metastasico", "indeterminado"]
    estadio: str | None
    datos_faltantes: list[str]
    opciones: list[OpcionSalida]
    valor_informacion: list[ValorInformacionSalida]


class EvidenciaSalida(BaseModel):
    id: str
    fuente: str
    titulo: str
    detalle: str
    nivel: str | None
    url: str | None
    referencias: list[str]
    opciones: list[str]
    sobre_otras_personas: bool


class FuenteFallidaSalida(BaseModel):
    fuente: str
    error: str


class ConsultaResponse(BaseModel):
    success: bool
    modo: Literal["caso", "general"]
    motivo_modo: str
    perfil: PerfilEntrada
    origen: dict[str, str]
    trazas: list[TrazaSalida]
    descartados: list[TrazaSalida]
    evaluacion: EvaluacionSalida | None = Field(description="Evaluación del motor de reglas; null en una consulta general")
    evidencia: list[EvidenciaSalida]
    fuentes_consultadas: list[str]
    fuentes_fallidas: list[FuenteFallidaSalida]
    narrativa: str | None
    modelo_narrativa: str | None
    advertencias: list[str]
    respuesta: str = Field(description="Respuesta completa en markdown: redacción (si hubo) + dossier")
    # Campos del contrato anterior de /api/v1/consultar, para no romper scripts existentes.
    cypher_utilizado: str
    evidencia_recuperada: str
    metodo_recuperacion: str


class CriterioCatalogo(BaseModel):
    id: str
    texto: str


class OpcionCatalogo(BaseModel):
    id: str
    nombre: str
    tipo: str
    escenario: str
    familia: str
    fuente: str
    resumen: str
    criterios: list[CriterioCatalogo]


class ReglasResponse(BaseModel):
    version_reglas: str
    estado_validacion: str
    opciones: list[OpcionCatalogo]


# ---------------------------------------------------------------------------
# Explorador text-to-Cypher
# ---------------------------------------------------------------------------
class ExplorarRequest(BaseModel):
    pregunta: str = Field(..., min_length=3, max_length=2000)


class ExplorarResponse(BaseModel):
    success: bool
    cypher: str
    filas: list[dict]
    aviso: str


# ---------------------------------------------------------------------------
# Resto de los endpoints (sin cambios de contrato)
# ---------------------------------------------------------------------------
class HealthResponse(BaseModel):
    status: str
    service: str


class EstadoGrafo(BaseModel):
    nodos: dict[str, int] | None = None
    relaciones: dict[str, int] | None = None
    detalles_literatura: list[dict] | None = None
    error: str | None = None


class DebugGraphResponse(BaseModel):
    success: bool
    estado: EstadoGrafo


class IngestaCasoResponse(BaseModel):
    success: bool
    caso_id: str
    resumen_clinico: str
    hallazgos_extraidos: int
    eventos_postop_extraidos: int
    estructura_completa: dict


class IngestaTextoRequest(BaseModel):
    texto: str


class BuscarCasosResponse(BaseModel):
    success: bool
    casos_encontrados: int
    casos: list[str]


class ActualizacionProtocolo(BaseModel):
    id: str
    titulo: str | None = None
    resumen: str | None = None
    sociedad: str | None = None
    fuente: str | None = None
    fecha_publicacion: str | None = None
    subtipos_detectados: str | None = None
    url: str | None = None


class ActualizacionesProtocoloResponse(BaseModel):
    success: bool
    total: int
    actualizaciones: list[ActualizacionProtocolo]


class RegistroTumorResumen(BaseModel):
    id: str
    edad: int | None = None
    topografia_nombre: str | None = None
    estadio_clinico: str | None = None
    subtipo_molecular: str | None = None
    receptor_estrogeno: str | None = None
    receptor_progesterona: str | None = None
    her2: str | None = None
    hospital: str | None = None
    fecha_diagnostico: str | None = None


class RegistroTumoresResponse(BaseModel):
    success: bool
    total: int
    registros: list[RegistroTumorResumen]


class ProtocoloEstandar(BaseModel):
    id: str
    histologia_subtipo: str | None = None
    biomarcadores_criticos: str | None = None
    estadio_tnm: str | None = None
    intencion_linea: str | None = None
    protocolo_esquema: str | None = None
    modalidad: str | None = None


class ProtocolosEstandarResponse(BaseModel):
    success: bool
    subtipo_molecular: str
    total: int
    protocolos: list[ProtocoloEstandar]


class SubtipoCount(BaseModel):
    subtipo: str
    total: int


class EstadioCount(BaseModel):
    estadio: str
    total: int


class ResumenRegistroTumores(BaseModel):
    total: int
    edad_promedio: float | None = None
    por_subtipo: list[SubtipoCount]
    por_estadio: list[EstadioCount]


class EstudioCBio(BaseModel):
    study_id: str | None = None
    nombre: str | None = None
    descripcion: str | None = None
    n_pacientes: int | None = None


class GenAlterado(BaseModel):
    gen: str
    porcentaje: float | None = None
    tipo_alteracion: str | None = None


class ResumenCBioPortal(BaseModel):
    estudio: EstudioCBio | None = None
    top_genes: list[GenAlterado]


class ResumenCohorteRealResponse(BaseModel):
    success: bool
    registro_tumores: ResumenRegistroTumores
    cbioportal: ResumenCBioPortal


class ElegibilidadTrial(BaseModel):
    veredicto: str
    nct_id: str | None = None
    titulo: str | None = None
    url: str | None = None
    motivo: str | None = None
    criterio_pendiente: str | None = None
    regla: str | None = None
    fecha_evaluacion: str | None = None


class ElegibilidadPacienteResponse(BaseModel):
    success: bool
    paciente_id: str
    total: int
    ensayos: list[ElegibilidadTrial]
