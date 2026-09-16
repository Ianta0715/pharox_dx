import os
import json
import re
from typing import Optional
from fastapi import FastAPI, HTTPException, File, Form, UploadFile, Depends, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader
from pydantic import BaseModel

# Importaciones nativas de LangChain
from langchain_core.prompts import PromptTemplate, FewShotPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough, RunnableParallel, RunnableLambda

# Importaciones de Pharox DX
from app.logging_config import configurar_logging, get_logger
from app.graph_db import (
    inicializar_db,
    buscar_contexto_hibrido,
    detectar_subtipo_molecular,
    obtener_estado_grafo,
    obtener_esquema_grafo,
    listar_actualizaciones_protocolo,
    listar_registros_tumor,
    obtener_subtipo_molecular_registro,
    protocolo_estandar_por_subtipo,
    resumen_cohorte_real,
    obtener_elegibilidad_trials_paciente,
    get_graph
)
from app.ai_gateway import get_llm, DEFAULT_MODEL

configurar_logging()
logger = get_logger("pharox.main")

app = FastAPI(
    title="Pharox DX - Core Engine (LangChain Graph RAG)",
    version="3.0.0",
    description="Backend Server orquestado con LangChain (LCEL), Neo4j y Ollama"
)

# -------------------------------------------------------------------------
# CORS — lista explícita de orígenes permitidos (nunca "*" con credentials)
# -------------------------------------------------------------------------
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:3000").split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------------------------------------------------------
# AUTENTICACIÓN POR API KEY
# -------------------------------------------------------------------------
# Todos los endpoints clínicos requieren el header X-API-Key. El health
# check en "/" queda público para probes de infraestructura (load balancer,
# Azure Container Apps) que no envían headers custom.
PHAROX_API_KEY = os.getenv("PHAROX_API_KEY")
if not PHAROX_API_KEY:
    raise RuntimeError(
        "PHAROX_API_KEY no está definida. Agregala al archivo .env antes de "
        "levantar el backend: PHAROX_API_KEY=<valor secreto>"
    )

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def verificar_api_key(api_key: str = Security(_api_key_header)) -> str:
    if api_key != PHAROX_API_KEY:
        raise HTTPException(status_code=401, detail="API key inválida o faltante (header X-API-Key).")
    return api_key

# -------------------------------------------------------------------------
# LOGS DE LA APLICACIÓN
# -------------------------------------------------------------------------
# Mismas firmas que antes (log_info(msg), log_security(msg, blocked=...), etc.)
# para no tocar los call sites — pero ahora van por logging_config, que en
# desarrollo local se ve igual de coloreado en la terminal, y en producción
# (LOG_FORMAT=json) sale como JSON estructurado para Azure Log Analytics.
def log_info(msg: str):
    logger.info(msg, extra={"tag": "INFO"})

def log_success(msg: str):
    logger.info(msg, extra={"tag": "OK"})

def log_warning(msg: str):
    logger.warning(msg, extra={"tag": "WARN"})

def log_error(msg: str):
    logger.error(msg, extra={"tag": "ERROR"})

def log_security(msg: str, blocked: bool = False):
    tag = "SEGURIDAD:BLOQUEADO" if blocked else "SEGURIDAD:APROBADO"
    nivel = logger.warning if blocked else logger.info
    nivel(msg, extra={"tag": tag, "bold": True})

def log_clinical(msg: str):
    logger.info(msg, extra={"tag": "CLINICA", "bold": True})


class ConsultaMedicaRequest(BaseModel):
    consulta: str
    tipo_cancer: str = "Cáncer de Mama"

# -------------------------------------------------------------------------
# MODELOS DE RESPUESTA — documentan en /docs la forma real de cada endpoint
# -------------------------------------------------------------------------
class HealthResponse(BaseModel):
    status: str
    service: str

class ConsultaResponse(BaseModel):
    success: bool
    respuesta: str
    cypher_utilizado: str
    evidencia_recuperada: str
    metodo_recuperacion: str

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

@app.on_event("startup")
def startup_event():
    try:
        log_info("Inicializando base de datos de grafos Neo4j...")
        inicializar_db()
        log_success("Base de datos de grafos e índices listos para operar.")
    except Exception as e:
        log_error(f"Error al inicializar la BD de grafos: {e}")

@app.get("/", response_model=HealthResponse)
def health_check():
    return {"status": "ok", "service": "Pharox DX LCEL Graph Engine Running"}

# -------------------------------------------------------------------------
# CONFIGURACIÓN DE FEW-SHOT PROMPTING PARA TEXT-TO-CYPHER
# -------------------------------------------------------------------------
EXAMPLES = [
    {
        "question": "¿Cuántos pacientes están diagnosticados con Cáncer de Mama?",
        "query": "MATCH (p:Paciente)-[:DIAGNOSTICADO_CON]->(t:Tumor {{tipo_cancer: 'Cáncer de Mama'}}) RETURN count(p) AS cantidad"
    },
    {
        "question": "¿Qué droga se prescribió para el Tumor de tipo Cáncer de Mama?",
        "query": "MATCH (t:Tumor {{tipo_cancer: 'Cáncer de Mama'}})-[:TRATADO_CON]->(tr:Tratamiento) RETURN DISTINCT tr.droga AS droga"
    },
    {
        "question": "Drogas usadas en pacientes menores de 50 años diagnosticados con Cáncer de Mama.",
        "query": "MATCH (p:Paciente)-[:DIAGNOSTICADO_CON]->(t:Tumor {{tipo_cancer: 'Cáncer de Mama'}})-[:TRATADO_CON]->(tr:Tratamiento) WHERE p.edad < 50 RETURN DISTINCT tr.droga AS droga"
    },
    {
        "question": "¿Qué edad promedio tienen los pacientes tratados con Pembrolizumab?",
        "query": "MATCH (p:Paciente)-[:DIAGNOSTICADO_CON]->(t:Tumor)-[:TRATADO_CON]->(tr:Tratamiento {{droga: 'Pembrolizumab'}}) RETURN avg(p.edad) AS edad_promedio"
    },
    {
        "question": "¿Qué evidencia clínica existe para la variante H1047R del gen PIK3CA?",
        "query": "MATCH (v:Variante {{gen: 'PIK3CA', nombre_variante: 'H1047R'}})-[:TIENE_EVIDENCIA]->(e:Evidencia) RETURN e.descripcion AS descripcion, e.nivel AS nivel, e.significancia AS significancia"
    },
    {
        "question": "¿Qué terapias están asociadas a la evidencia de HER2 Positive Breast Cancer?",
        "query": "MATCH (en:Enfermedad {{nombre_mostrado: 'HER2 Positive Breast Cancer'}})<-[:ASOCIADA_A_ENFERMEDAD]-(e:Evidencia)-[:INVOLUCRA_TERAPIA]->(t:Terapia) RETURN DISTINCT t.nombre AS terapia"
    },
    {
        "question": "¿En qué fuentes (papers) se basa la evidencia sobre el gen ESR1?",
        "query": "MATCH (v:Variante {{gen: 'ESR1'}})-[:TIENE_EVIDENCIA]->(e:Evidencia)-[:RESPALDADA_POR]->(f:Fuente) RETURN DISTINCT f.cita AS cita, f.journal AS journal, f.anio AS anio"
    },
    {
        "question": "¿Qué hallazgos patológicos se registraron en los casos clínicos de tumores de mama?",
        "query": "MATCH (c:CasoClinico)-[:INCLUYE_HALLAZGO]->(hp:HallazgoPatologico)-[:ASOCIADO_A_TUMOR]->(t:Tumor {{tipo_cancer: 'Cáncer de Mama'}}) RETURN hp.subtipo_molecular AS subtipo, hp.procedimiento AS procedimiento"
    },
    {
        "question": "¿Qué antecedentes médicos tienen los pacientes registrados?",
        "query": "MATCH (p:Paciente)-[:TIENE_ANTECEDENTE]->(a:AntecedenteMedico) RETURN p.hash AS paciente, a.tipo AS tipo, a.medicacion AS medicacion"
    },
    {
        "question": "¿Qué ensayos clínicos activos existen para cáncer de mama HER2 positivo?",
        "query": "MATCH (e:EnsayoClinico) WHERE e.estado = 'RECRUITING' AND ANY(c IN e.condiciones WHERE toLower(c) CONTAINS 'breast') AND ANY(c IN e.condiciones WHERE toLower(c) CONTAINS 'her2') RETURN e.nct_id AS nct_id, e.titulo AS titulo, e.fases AS fases"
    },
    {
        "question": "¿Cuál es la clasificación clínica de las variantes de BRCA1 en ClinVar?",
        "query": "MATCH (v:VarianteClinVar {{gen: 'BRCA1'}}) RETURN v.nombre AS variante, v.clasificacion_clinica AS clasificacion, v.review_status AS revision"
    },
    {
        "question": "¿Con qué frecuencia se altera el gen PIK3CA en estudios de cBioPortal de cáncer de mama?",
        "query": "MATCH (est:EstudioCBio)-[:REPORTA_FRECUENCIA]->(f:FrecuenciaGenCBio)-[:SOBRE_GEN]->(g:GenCBio {{hugo_symbol: 'PIK3CA'}}) RETURN est.nombre AS estudio, f.porcentaje AS porcentaje_alterado, f.tipo_alteracion AS tipo"
    }
]

example_prompt = PromptTemplate(
    input_variables=["question", "query"],
    template="Pregunta del médico: {question}\nCypher: {query}"
)

few_shot_prompt = FewShotPromptTemplate(
    examples=EXAMPLES,
    example_prompt=example_prompt,
    prefix="""Actúas como un experto traductor de Lenguaje Natural a consultas Cypher para Neo4j, especializado en oncología de precisión.
Basándote en el esquema de base de datos de grafos provisto, traduce la pregunta del médico a una consulta Cypher válida.

Reglas estrictas de generación:
1. Genera ÚNICAMENTE la consulta Cypher sin bloques de código (sin ```cypher), sin explicaciones, sin comentarios y sin texto adicional. Debe ser ejecutable directamente.
2. Solo se permiten operaciones de lectura (MATCH y RETURN). Está prohibido usar DELETE, CREATE, MERGE, SET, REMOVE o DETACH.
3. Asegúrate de respetar los nombres de nodos, propiedades y relaciones exactos del esquema.

Esquema de la base de datos de grafos:
{schema}
""",
    suffix="Pregunta del médico: {question}\nCypher: ",
    input_variables=["schema", "question"]
)

def clean_cypher_output(text: str) -> str:
    """
    Remueve bloques de código de markdown si el LLM los incluye, y cualquier
    rastro de razonamiento (<think>...</think>) que se filtre al contenido en
    vez de ir separado -- ver por qué en el comentario de llm_cypher: pasó de
    verdad con qwen3 sobre un caso clínico largo y complejo. Con
    reasoning=False no debería aparecer nunca, pero si aparece, mejor
    quedarse sin Cypher (falla el validador de abajo, cae al fallback
    híbrido) que ejecutar contra Neo4j el texto de un pensamiento cortado.
    """
    cleaned = text.strip()
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"</?think>", "", cleaned)
    cleaned = cleaned.replace("```cypher", "").replace("```", "")
    return cleaned.strip()

# -------------------------------------------------------------------------
# CADENA DE GENERACIÓN TEXT-TO-CYPHER (PASO 1)
# -------------------------------------------------------------------------
def log_schema_and_query_start(inputs):
    log_info(
        f"Nueva consulta recibida del médico: '{inputs['question']}' "
        f"(contexto cáncer: '{inputs.get('tipo_cancer', 'No Especificado')}')"
    )
    log_info("Generando consulta Cypher dinámica usando Few-Shot Prompting...")
    return inputs

llm_cypher = get_llm(
    task="cypher",
    data_sensitive=True,
    temperature=0.0,
    # qwen3 es un modelo hibrido con modo de razonamiento (<think>...</think>).
    # Generar Cypher es una traduccion corta y mecanica -- los pocos ejemplos
    # del few-shot son una sola linea cada uno -- que no se beneficia de
    # razonar en voz alta, y en la practica el modo pensamiento entro en un
    # loop de repeticion sobre un caso clinico largo (cientos de propiedades
    # inventadas repetidas) que termino filtrando un "</think>" suelto al
    # Cypher final. reasoning=False lo desactiva para esta tarea puntual.
    reasoning=False,
    # Red de seguridad ademas de apagar el razonamiento: ningun Cypher valido
    # de este esquema necesita mas de un puñado de clausulas, y un tope bajo
    # corta cualquier repeticion temprano en vez de dejarla correr.
    num_predict=400,
    repeat_penalty=1.3,
)

cypher_chain = (
    RunnableLambda(log_schema_and_query_start)
    | {
        "schema": lambda _: obtener_esquema_grafo(),
        "question": lambda x: x["question"]
    }
    | few_shot_prompt
    | llm_cypher
    | StrOutputParser()
    | RunnableLambda(clean_cypher_output)
)

# -------------------------------------------------------------------------
# FILTRO DE SEGURIDAD Y EJECUCIÓN CON FALLBACK INTEGRADO (PASO 2)
# -------------------------------------------------------------------------
def validate_cypher(cypher_query: str) -> str:
    """Filtro de seguridad estricto que bloquea cualquier operación de escritura/modificación."""
    cleaned = clean_cypher_output(cypher_query)
    forbidden = ["DELETE", "CREATE", "MERGE", "SET", "REMOVE", "DETACH"]
    upper_query = cleaned.upper()
    for word in forbidden:
        if word in upper_query:
            log_security(f"Palabra clave prohibida detectada: '{word}'", blocked=True)
            raise ValueError(f"Operación Cypher no permitida por seguridad: contiene la palabra clave '{word}'.")
    log_security("Consulta validada como de 'Solo Lectura'.")
    return cleaned

def ejecutar_y_filtrar_cypher(input_dict: dict) -> dict:
    """
    Recibe la consulta generada, la valida y la ejecuta contra Neo4j.
    La búsqueda híbrida (Literatura + Grafo Clínico + Casos Reales + CIViC) se ejecuta
    SIEMPRE como complemento, no solo cuando el Cypher dirigido falla o viene vacío:
    la razón de usar una base de datos de grafos es combinar todas las fuentes de
    conocimiento en una sola respuesta, no elegir una sola y descartar el resto.
    """
    cypher_query = input_dict["cypher_query"]
    question = input_dict["question"]
    tipo_cancer = input_dict.get("tipo_cancer", "Cáncer de Mama")

    log_info(f"Traducción completada. Cypher generado: {cypher_query}")

    evidencia_cypher = None
    try:
        # 1. Validar contra el filtro de seguridad
        validated_query = validate_cypher(cypher_query)

        # 2. Ejecutar en Neo4j a través de la instancia del grafo de LangChain
        g = get_graph()
        records = g.query(validated_query)

        if records:
            log_success(f"Consulta ejecutada en Neo4j. Se recuperaron {len(records)} registro(s).")
            evidencia_cypher = json.dumps(records, ensure_ascii=False, indent=2)
        else:
            log_warning("La consulta Cypher no retornó registros en la base de datos.")

    except Exception as e:
        log_error(f"Fallo en ejecución o bloqueo de seguridad: {e}")

    # 3. Búsqueda híbrida: Literatura (vectorial) + Grafo Clínico (relaciones) +
    #    Casos Clínicos Reales (vectorial) + Evidencia Molecular CIViC (grafo)
    log_info("Enriqueciendo con búsqueda híbrida (Literatura + Casos Reales + CIViC)...")
    contextos = buscar_contexto_hibrido(query=question, tipo_cancer=tipo_cancer)
    evidencia_hibrida = "\n".join([f"- {c}" for c in contextos]) if contextos else ""

    if contextos:
        log_success(f"Búsqueda híbrida completada. Se recuperaron {len(contextos)} fragmento(s) de evidencia.")
    else:
        log_warning("No se encontró evidencia adicional en la búsqueda híbrida.")

    # 4. Combinar ambas fuentes para la síntesis final
    partes_evidencia = []
    if evidencia_cypher:
        partes_evidencia.append(f"[DATOS ESTRUCTURADOS DEL GRAFO]\n{evidencia_cypher}")
    if evidencia_hibrida:
        partes_evidencia.append(f"[EVIDENCIA COMPLEMENTARIA: Literatura, Casos Reales y CIViC]\n{evidencia_hibrida}")

    evidencia_final = "\n\n".join(partes_evidencia) if partes_evidencia else "Sin evidencia local registrada en la base de datos."

    if evidencia_cypher and contextos:
        metodo = "Text-to-Cypher + Búsqueda Híbrida (combinados)"
    elif evidencia_cypher:
        metodo = "Text-to-Cypher (Neo4j Graph)"
    elif contextos:
        metodo = "Búsqueda Híbrida (Vectorial + Relaciones + CIViC)"
    else:
        metodo = "Sin evidencia encontrada"

    return {
        "evidencia": evidencia_final,
        "cypher_utilizado": cypher_query,
        "metodo_recuperacion": metodo
    }

# -------------------------------------------------------------------------
# CADENA DE SÍNTESIS CLÍNICA (PASO 3)
# -------------------------------------------------------------------------
prompt_clinico_template = """Actúas como un Copiloto Clínico Experto en Oncología de Precisión, escribiéndole
directamente a un médico que tiene poco tiempo entre pacientes. Tu respuesta va a ser leída en
la práctica clínica real, no es un reporte técnico ni un volcado de datos.

Tipo de cáncer: {tipo_cancer}
Consulta del profesional: "{question}"
Subtipo molecular detectado en la consulta a partir de RE/RP/HER2 o mención explícita: {perfil_detectado}

Evidencia recuperada de la base de datos de grafos (literatura, evidencia molecular CIViC,
ensayos clínicos, variantes, registros reales de pacientes, protocolos estándar y casos reales
que vos mismo redactaste):
{evidencia}

REGLA ABSOLUTA E INQUEBRANTABLE: Esto es una herramienta clínica real, no un ejercicio de redacción.
NUNCA inventes, extrapoles ni completes con imaginación pacientes, edades, líneas de tratamiento,
resultados o complicaciones que no estén literalmente presentes en la evidencia de arriba. Esto
aplica a CUALQUIER fragmento de evidencia, no solo a los casos clínicos: por ejemplo, los
"[REGISTRO REAL DE PACIENTE]" traen diagnóstico y estadificación pero NO traen qué tratamiento
recibió ese paciente — si el campo tratamiento no está en el fragmento, no digas ni insinúes que
"fue tratado con X" o "recibió quimioterapia", ni siquiera como suposición razonable. Si un dato
no está en la evidencia, la respuesta correcta es no mencionarlo, no inferirlo.
Si un campo (por ejemplo "Nivel de evidencia") aparece explícito en la evidencia, usá ese valor
exacto tal como figura — nunca digas "no especificado" si el dato está ahí.
El campo "Esquema" de un "[PROTOCOLO DE TRATAMIENTO ESTÁNDAR]" NO hace falta que lo transcribas
vos: el sistema ya lo agrega, textual y verificado, al final de la respuesta en una sección aparte
("Esquema exacto registrado"), después de que termines de escribir. Es la parte más sensible a
citar mal —el orden de las fases decide si una droga se da antes o después de la cirugía— así que
en vez de arriesgarte a resumirla de memoria, simplemente NOMBRÁ el protocolo (p. ej. "según
KEYNOTE-522") sin listar sus drogas ni fases una por una: esa lista exacta ya la va a ver el
médico en la sección que se agrega después de tu respuesta.
Si el bloque de evidencia NO contiene ningún fragmento marcado explícitamente como
"[CASO CLÍNICO REAL SIMILAR]", entonces NO EXISTE ningún caso real similar disponible: no
inventes uno, no redactes un "paciente de X años" ficticio bajo ningún concepto — decilo
explícitamente en vez de omitirlo o inventarlo.

DATOS DE ESTE PACIENTE VS. CONOCIMIENTO DE REFERENCIA — no los confundas: los ÚNICOS datos DE ESTE
paciente son los que están literalmente en "Consulta del profesional" arriba. Todo lo que aparece
en "Evidencia recuperada" — variantes de ClinVar, frecuencias de cBioPortal, evidencia CIViC,
otros "[REGISTRO REAL DE PACIENTE]", "[CASO CLÍNICO REAL SIMILAR]" — es conocimiento de la base de
datos sobre OTRAS personas o sobre la literatura, nunca un resultado de este paciente, aunque el
gen o el perfil coincidan. Ejemplo concreto: si la evidencia trae "[VARIANTE CLINVAR] Gen BRCA1 ...
Pathogenic", eso NO significa que este paciente tenga esa variante — significa que la base tiene
registrada una variante patogénica de BRCA1 como antecedente conocido, relevante para justificar
por qué correspondería un estudio, no como un resultado ya obtenido. Nunca redactes "el paciente
presenta la variante X" ni "se identifica en el paciente" a partir de estos fragmentos — la forma
correcta es "la base tiene registrada la variante X" o "esto es relevante como referencia, no como
hallazgo de este paciente". Lo mismo aplica a "[REGISTRO REAL DE PACIENTE]" y "[CASO CLÍNICO REAL
SIMILAR]": son otras personas, no la persona de la consulta — si además difieren mucho en edad o
estadio del caso consultado, decilo (“son de otro grupo etario/estadio, con valor limitado como
referencia”) en vez de presentarlos como comparables sin aclarar la diferencia.

VERIFICACIÓN DE SUBTIPO — chequealo antes de responder: "Subtipo molecular detectado" arriba es
el que corresponde a ESTA consulta según sus propios datos (RE/RP/HER2). Antes de citar un
protocolo, ensayo o evidencia, confirmá que el subtipo al que se refiere ese fragmento coincide
con el detectado. Si un fragmento de evidencia es de un subtipo distinto (por ejemplo, evidencia
de "HER2_positivo" cuando el detectado es "Triple_negativo"), NO lo presentes como aplicable a
esta consulta — descartalo o, si igual querés mencionarlo como referencia general, aclará
explícitamente que corresponde a otro perfil molecular. Si "Subtipo molecular detectado" dice
"no determinado", no le atribuyas a la consulta ningún subtipo que no haya dicho explícitamente.

TU ROL: SOS UNA HERRAMIENTA DE CONSULTA, NO QUIEN DECIDE. Reportás qué dice la evidencia
encontrada en la base de datos — nunca aconsejás, sugerís ni recomendás una conducta clínica. La
decisión es siempre del médico, vos solo le mostrás qué hay disponible. Por eso:
- NUNCA uses verbos ni frases de consejo: prohibido "se sugiere", "se recomienda", "debería",
  "lo ideal sería", "conviene", "hay que considerar". Redactá siempre en modo informativo,
  reportando lo que la evidencia dice, no lo que vos aconsejás hacer.
- En vez de "se sugiere iniciar tratamiento con X", escribí "La evidencia disponible para este
  perfil señala a X" o "El protocolo estándar registrado para este perfil es X" o "Los datos
  encontrados asocian este perfil con X".
- Si hay varias opciones en la evidencia, presentalas como lo que son (varias opciones que
  aparecen en los datos), sin elegir vos cuál es "mejor" ni ordenarlas por preferencia propia.

CÓMO ESCRIBIR LA RESPUESTA — leela dos veces antes de responder:
- Organizá el contenido por lo que le importa al médico (situación del paciente, qué encontró la
  base de datos, por qué), NUNCA por de qué tabla de la base de datos salió cada dato. Las etiquetas entre corchetes
  ("[EVIDENCIA CIViC]", "[REGISTRO REAL DE PACIENTE]", "[ENSAYO CLÍNICO]", "[PROTOCOLO DE
  TRATAMIENTO ESTÁNDAR]", "[VARIANTE CLINVAR]", "[FRECUENCIA CBIOPORTAL]", "[ACTUALIZACIÓN DE
  PROTOCOLO]", "[CASO CLÍNICO REAL SIMILAR]", etc.) son metadata interna para que vos sepas de dónde viene cada dato — NUNCA las
  repitas como títulos de sección ni las cites textualmente en la respuesta. En su lugar, atribuí
  la fuente de forma breve y natural entre paréntesis, por ejemplo: "(evidencia CIViC nivel A)",
  "(protocolo estándar del hospital)", "(2 pacientes similares en el registro del hospital)",
  "(ensayo NCT03150576, reclutando)".
- Si el mismo dato (misma droga, mismo ensayo, mismo protocolo) aparece en más de un fragmento de
  evidencia, mencionalo UNA sola vez, en el lugar más relevante — no lo repitas en varias secciones.
- Escribí en prosa clara con viñetas cortas donde ayude a escanear rápido, no una lista exhaustiva
  de todos los campos de cada fragmento.

Estructura sugerida (adaptala a lo que la consulta REALMENTE pregunta, no la fuerces si la
pregunta es más simple):
1. **Respuesta directa a cada parte de lo preguntado:** si la consulta pide varias cosas
   puntuales (por ejemplo estadificación, esquema sistémico, manejo quirúrgico/axilar, estudios
   adicionales, conducta según respuesta patológica), respondé cada una por separado, EN ESE
   ORDEN, con lo que la evidencia efectivamente sostiene. Esto va primero y es el cuerpo principal
   de la respuesta — no es un resumen de qué encontró la búsqueda, es la respuesta a la pregunta.
   Para cada parte que la evidencia NO cubre, decilo ahí mismo ("la base no tiene información
   registrada sobre el manejo axilar post-neoadyuvancia para este perfil") en vez de omitirla en
   silencio: el médico necesita saber qué quedó sin responder, no solo lo que sí se encontró.
2. **Evidencia que lo respalda:** la evidencia científica y el protocolo estándar encontrados,
   resumidos (no transcriptos campo por campo), citando nivel de evidencia cuando esté disponible.
3. **Contexto real:** qué muestran los pacientes reales similares del hospital (registros y/o
   casos clínicos reales) — si no hay ninguno razonablemente comparable, decilo con honestidad en
   vez de forzar una comparación floja (ver la regla de arriba sobre edad/estadio).
4. **Otros datos relevantes:** alternativas, ensayos clínicos activos relevantes, o
   mutaciones/variantes de referencia presentes en la evidencia — solo si aportan algo que el
   punto 1 no cubre, y siempre aclarando que son de la base, no de este paciente.
5. **Ausencia de evidencia:** si la evidencia está vacía o no responde nada de lo preguntado,
   decilo con honestidad — la base de datos no tiene información sobre X — en vez de inventar
   contenido.

CHECKLIST DE PLAN COMPLETO — cuando la consulta pide un plan de manejo o tratamiento de un caso
(no para preguntas puntuales de un solo dato): una respuesta que cubre bien lo que preguntaron
puede igual sonar más completa de lo que es si calla en silencio las partes de un plan oncológico
que la base no cubre. Antes de cerrar la respuesta, repasá esta lista y por cada ítem que no haya
quedado cubierto en el punto 1, nombralo explícitamente en "Ausencia de evidencia" — no lo dejes
afuera sin mencionarlo, aunque el médico no lo haya preguntado con esas palabras:
- Estadificación
- Tratamiento sistémico (neoadyuvante y adyuvante)
- Cirugía mamaria y manejo axilar
- Radioterapia
- Estudios genéticos indicados
- Conducta según la respuesta patológica (si hubo neoadyuvancia)
- Consideraciones especiales (edad, fertilidad, comorbilidades, estado menopáusico)
Un médico que lee la respuesta tiene que poder distinguir "esto no aplica a este caso" de "esto no
está en nuestra base" de "esto sí lo cubrimos" — las tres son respuestas válidas, la que no es
válida es el silencio.

Respondé siempre en español, con rigor oncológico.
"""

prompt_clinico = PromptTemplate(
    template=prompt_clinico_template,
    input_variables=["tipo_cancer", "evidencia", "question", "perfil_detectado"]
)

def log_sintesis_start(inputs):
    log_clinical(
        f"Sintetizando respuesta con el modelo '{DEFAULT_MODEL}' "
        f"(origen de evidencia: {inputs['metodo_recuperacion']})..."
    )
    return inputs

llm_sintesis = get_llm(task="sintesis", data_sensitive=True, temperature=0.1)
# Se probó qwen3:14b acá (2026-09-16): 10x más lento (236s vs. 22-30s) y sin
# mejora real -- en la prueba con el caso de referencia inventó un esquema
# completo ("CMF") que no existía en ninguna parte de la evidencia, omitiendo
# además el componente de inmunoterapia que sí estaba presente. El problema de
# fidelidad de lectura no se resuelve subiendo de modelo; ver
# PROTOCOLO_VERIFICADO_TEMPLATE más abajo para la solución que sí funciona:
# el esquema exacto lo inserta el código, no el LLM.

sintesis_chain = (
    prompt_clinico
    | llm_sintesis
    | StrOutputParser()
)

# -------------------------------------------------------------------------
# ORQUESTACIÓN GENERAL CON LCEL
# -------------------------------------------------------------------------
coordinador_chain = (
    # Paso 1: Generar el Cypher dinámicamente
    RunnablePassthrough.assign(
        cypher_query=cypher_chain
    )
    # Paso 2: Ejecutar y obtener evidencia (con fallback de seguridad y de no-registros)
    | RunnablePassthrough.assign(
        datos_recuperacion=RunnableLambda(ejecutar_y_filtrar_cypher)
    )
    # Paso 3: Mapear variables para la síntesis clínica final y resolverla
    | RunnableParallel({
        "respuesta": (
            lambda x: {
                "question": x["question"],
                "tipo_cancer": x["tipo_cancer"],
                "evidencia": x["datos_recuperacion"]["evidencia"],
                "metodo_recuperacion": x["datos_recuperacion"]["metodo_recuperacion"],
                "perfil_detectado": detectar_subtipo_molecular(x["question"]) or "no determinado a partir del texto",
            }
        )
        | RunnableLambda(log_sintesis_start)
        | sintesis_chain,
        
        "cypher_utilizado": lambda x: x["datos_recuperacion"]["cypher_utilizado"],
        "evidencia_recuperada": lambda x: x["datos_recuperacion"]["evidencia"],
        "metodo_recuperacion": lambda x: x["datos_recuperacion"]["metodo_recuperacion"]
    })
)

# -------------------------------------------------------------------------
# ENDPOINTS DE FASTAPI
# -------------------------------------------------------------------------
def _bloque_protocolo_verificado(consulta: str) -> str:
    """
    Arma el bloque con el esquema EXACTO del protocolo que matchea el subtipo
    molecular detectado en la consulta, tal como está en la base -- sin pasar
    por el LLM. Nace de que ni qwen3:8b ni qwen3:14b citaron el esquema de
    forma confiable con puro prompting: 8b lo parafraseaba mal (invertía el
    orden, se comía drogas), 14b llegó a inventar un esquema entero ("CMF")
    que no estaba en ninguna parte de la evidencia. La única forma de
    garantizar que el médico vea el esquema correcto es que el código lo
    agregue tal cual, no que el modelo lo redacte de memoria.
    """
    subtipo = detectar_subtipo_molecular(consulta)
    if not subtipo or subtipo == "desconocido":
        return ""
    try:
        protocolos = protocolo_estandar_por_subtipo(subtipo)
    except Exception as e:
        log_error(f"Error al armar el bloque de protocolo verificado: {e}")
        return ""
    if not protocolos:
        return ""

    lineas = [
        f"\n\n---\n**Esquema exacto registrado para el subtipo detectado ({subtipo}) "
        "— tal como figura en la base, no redactado por el modelo:**"
    ]
    for p in protocolos:
        lineas.append(
            f"- {p.get('protocolo_esquema') or '?'} "
            f"({p.get('modalidad') or '?'} · {p.get('intencion_linea') or '?'} · "
            f"estadio {p.get('estadio_tnm') or '?'})"
        )
    return "\n".join(lineas)


@app.post("/api/v1/consultar", dependencies=[Depends(verificar_api_key)], response_model=ConsultaResponse)
def consultar_copiloto(payload: ConsultaMedicaRequest):
    """
    Endpoint principal de consulta clínica. Orquesta todo el flujo LCEL.
    """
    try:
        inputs = {
            "question": payload.consulta,
            "tipo_cancer": payload.tipo_cancer
        }
        # Invocación de la cadena LangChain
        resultado = coordinador_chain.invoke(inputs)

        respuesta_final = resultado["respuesta"] + _bloque_protocolo_verificado(payload.consulta)

        return {
            "success": True,
            "respuesta": respuesta_final,
            "cypher_utilizado": resultado["cypher_utilizado"],
            "evidencia_recuperada": resultado["evidencia_recuperada"],
            "metodo_recuperacion": resultado["metodo_recuperacion"]
        }
    except Exception as e:
        log_error(f"Excepción en el endpoint: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error en el Copiloto Clínico (LangChain Graph RAG): {repr(e)}"
        )

@app.get("/api/v1/debug/graph_db", dependencies=[Depends(verificar_api_key)], response_model=DebugGraphResponse)
def ver_base_de_grafos():
    try:
        datos = obtener_estado_grafo()
        return {"success": True, "estado": datos}
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error al leer base de grafos: {repr(e)}"
        )

# -------------------------------------------------------------------------
# VIGILANCIA DE PROTOCOLOS (Pharox_Documento_v5 §11 Capa 2)
# -------------------------------------------------------------------------
SUBTIPOS_MOLECULARES_VALIDOS = {"HER2_positivo", "Triple_negativo", "RH_positivo_HER2_negativo", "desconocido"}

@app.get("/api/v1/protocolos/actualizaciones", dependencies=[Depends(verificar_api_key)], response_model=ActualizacionesProtocoloResponse)
def ver_actualizaciones_protocolo(subtipo_molecular: Optional[str] = None, limit: int = 20):
    """
    Lista actualizaciones de guías/protocolos de cáncer de mama (ASCO/ESMO)
    ingestadas por app/gold/vigilancia_protocolos_to_neo4j.py, más recientes
    primero. Filtrar por `subtipo_molecular` para ver solo lo relevante al
    perfil de un paciente activo (mismo vocabulario que RegistroTumor:
    HER2_positivo, Triple_negativo, RH_positivo_HER2_negativo, desconocido).
    """
    if subtipo_molecular and subtipo_molecular not in SUBTIPOS_MOLECULARES_VALIDOS:
        raise HTTPException(
            status_code=422,
            detail=f"subtipo_molecular inválido. Valores permitidos: {sorted(SUBTIPOS_MOLECULARES_VALIDOS)}"
        )
    try:
        actualizaciones = listar_actualizaciones_protocolo(subtipo_molecular=subtipo_molecular, limit=limit)
        return {"success": True, "total": len(actualizaciones), "actualizaciones": actualizaciones}
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error al leer actualizaciones de protocolo: {repr(e)}"
        )

# -------------------------------------------------------------------------
# REGISTRO REAL DE TUMORES (Hospital Central Mendoza) — listado para selector
# -------------------------------------------------------------------------
@app.get("/api/v1/registro_tumores", dependencies=[Depends(verificar_api_key)], response_model=RegistroTumoresResponse)
def ver_registros_tumor(subtipo_molecular: Optional[str] = None, limit: int = 50):
    """
    Lista pacientes reales (:RegistroTumor) del Hospital Central Mendoza, para
    que un cliente pueda ofrecer un selector sin conocer de antemano los ids
    RT_XXXX. Filtrado a mama (única población con elegibilidad a ensayos
    calculada por app/gold/reglas_elegibilidad_trials.py).
    """
    if subtipo_molecular and subtipo_molecular not in SUBTIPOS_MOLECULARES_VALIDOS:
        raise HTTPException(
            status_code=422,
            detail=f"subtipo_molecular inválido. Valores permitidos: {sorted(SUBTIPOS_MOLECULARES_VALIDOS)}"
        )
    try:
        registros = listar_registros_tumor(subtipo_molecular=subtipo_molecular, limit=limit)
        return {"success": True, "total": len(registros), "registros": registros}
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error al leer registros de tumor: {repr(e)}"
        )

@app.get("/api/v1/registro_tumores/{paciente_id}/protocolo_estandar", dependencies=[Depends(verificar_api_key)], response_model=ProtocolosEstandarResponse)
def ver_protocolo_estandar(paciente_id: str):
    """
    Protocolo(s) de tratamiento estándar (subgrafo :ProtocoloTratamiento,
    conocimiento de referencia -- ver app/gold/protocolos_tratamiento_to_neo4j.py)
    que matchean el subtipo molecular de un paciente real. No es el
    tratamiento que recibió ese paciente (RegistroTumor no tiene esa
    relación) -- es el esquema estándar que correspondería a su perfil.
    """
    try:
        subtipo = obtener_subtipo_molecular_registro(paciente_id)
        if subtipo is None:
            raise HTTPException(status_code=404, detail=f"No se encontró el paciente {paciente_id}.")
        protocolos = protocolo_estandar_por_subtipo(subtipo)
        return {"success": True, "subtipo_molecular": subtipo, "total": len(protocolos), "protocolos": protocolos}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error al leer protocolo estándar: {repr(e)}"
        )

# -------------------------------------------------------------------------
# COHORTE REAL — resumen agregado (RegistroTumor + cBioPortal METABRIC)
# -------------------------------------------------------------------------
@app.get("/api/v1/cohorte/resumen", dependencies=[Depends(verificar_api_key)], response_model=ResumenCohorteRealResponse)
def ver_resumen_cohorte_real():
    """
    Resumen agregado de la cohorte real (no del dataset demo TCGA): registro
    de tumores del Hospital Central Mendoza y frecuencias génicas reales de
    cBioPortal METABRIC (2.509 pacientes publicados).
    """
    try:
        resumen = resumen_cohorte_real()
        return {"success": True, **resumen}
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error al leer resumen de cohorte real: {repr(e)}"
        )

# -------------------------------------------------------------------------
# ELEGIBILIDAD A ENSAYOS (Pharox_Documento_v5 §11 Capa 1 — estado persistido)
# -------------------------------------------------------------------------
@app.get("/api/v1/pacientes/{paciente_id}/elegibilidad_trials", dependencies=[Depends(verificar_api_key)], response_model=ElegibilidadPacienteResponse)
def ver_elegibilidad_trials(paciente_id: str):
    """
    Lee el estado de elegibilidad a ensayos YA CALCULADO para un paciente
    (:RegistroTumor) por app/gold/reglas_elegibilidad_trials.py. A diferencia
    de /api/v1/consultar, esto no invoca al LLM ni recalcula nada: es lectura
    directa de las relaciones HABILITA_TRIAL/CONDICIONA_TRIAL/EXCLUYE_TRIAL
    persistidas en el grafo, por eso es instantáneo.
    """
    try:
        ensayos = obtener_elegibilidad_trials_paciente(paciente_id)
        return {"success": True, "paciente_id": paciente_id, "total": len(ensayos), "ensayos": ensayos}
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error al leer elegibilidad de ensayos: {repr(e)}"
        )

# -------------------------------------------------------------------------
# ENDPOINT DE INGESTA DE CASOS CLÍNICOS REALES (Case-Based Reasoning)
# -------------------------------------------------------------------------

@app.post("/api/v1/casos/ingestar", dependencies=[Depends(verificar_api_key)], response_model=IngestaCasoResponse)
async def ingestar_caso_clinico(
    texto: Optional[str] = Form(None, description="Texto libre del informe anatomopatológico o descripción clínica"),
    imagen: Optional[UploadFile] = File(None, description="Imagen (JPG/PNG) del informe médico escaneado")
):
    """
    Ingesta un caso clínico real anonimizado en el grafo de conocimiento de Pharox DX.
    
    El sistema acepta:
    - **texto**: Descripción libre del caso (anatomía patológica + post-operatorio)
    - **imagen**: Foto o scan del informe (JPG, PNG) — se procesará con OCR automático
    - **ambos**: Si se proveen imagen y texto, se combinarán para mayor precisión
    
    El caso queda anonimizado (hash SHA-256) y disponible para búsqueda de similitud
    cuando lleguen nuevos pacientes con características clínicas similares.
    """
    from app.etl_casos_clinicos import procesar_informe

    if not texto and not imagen:
        raise HTTPException(
            status_code=400,
            detail="Se requiere al menos texto o imagen del informe clínico."
        )

    log_info(f"Nueva ingesta de caso clínico — imagen: {'sí' if imagen else 'no'}, texto: {'sí' if texto else 'no'}")

    image_bytes = None
    if imagen:
        if not imagen.content_type.startswith("image/"):
            raise HTTPException(
                status_code=422,
                detail=f"El archivo '{imagen.filename}' no es una imagen válida. Sube JPG o PNG."
            )
        image_bytes = await imagen.read()
        log_info(f"Imagen recibida: {imagen.filename} ({len(image_bytes)} bytes)")

    try:
        resultado = procesar_informe(texto=texto, image_bytes=image_bytes)
        log_success(f"Caso clínico ingresado: ID={resultado['caso_id']}, hallazgos={resultado['hallazgos_extraidos']}, eventos_postop={resultado['eventos_postop_extraidos']}")
        return resultado
    except Exception as e:
        log_error(f"Error al ingestar caso clínico: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error al procesar el caso clínico: {repr(e)}"
        )


@app.get("/api/v1/casos/buscar", dependencies=[Depends(verificar_api_key)], response_model=BuscarCasosResponse)
def buscar_casos_similares_endpoint(consulta: str, n: int = 2):
    """
    Busca casos clínicos reales similares a la consulta de texto.
    Útil para que el médico pueda explorar el repositorio de casos directamente.
    """
    from app.graph_db import buscar_casos_similares
    try:
        casos = buscar_casos_similares(consulta, n_results=n)
        return {"success": True, "casos_encontrados": len(casos), "casos": casos}
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error en búsqueda de casos: {repr(e)}"
        )


class IngestaTextoRequest(BaseModel):
    texto: str

@app.post("/api/v1/casos/ingestar_texto", dependencies=[Depends(verificar_api_key)], response_model=IngestaCasoResponse)
def ingestar_caso_solo_texto(req: IngestaTextoRequest):
    """
    Ingesta un caso clínico usando únicamente texto (sin imagen).
    Evita los problemas de envío de archivos vacíos en Swagger UI.
    """
    from app.etl_casos_clinicos import procesar_informe
    try:
        resultado = procesar_informe(texto=req.texto, image_bytes=None)
        log_success(f"Caso clínico (solo texto) ingresado: ID={resultado['caso_id']}")
        return resultado
    except Exception as e:
        log_error(f"Error al ingestar caso de texto: {e}")
        raise HTTPException(status_code=500, detail=str(e))