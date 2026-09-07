import os
import json
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
    obtener_estado_grafo,
    obtener_esquema_grafo,
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
    """Remueve bloques de código de markdown si el LLM los incluye."""
    cleaned = text.strip()
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

llm_cypher = get_llm(temperature=0.0)

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
prompt_clinico_template = """Actúas como un Copiloto Clínico Experto en Oncología de Precisión.
Analiza la consulta médica relacionada con el tipo de cáncer: {tipo_cancer}.
Evidencia recuperada de nuestra base de datos de grafos de Neo4j (incluye literatura científica, evidencia molecular CIViC y casos clínicos reales similares):
{evidencia}
Consulta Médica del Profesional: "{question}"

REGLA ABSOLUTA E INQUEBRANTABLE: Esto es una herramienta clínica real, no un ejercicio de redacción.
NUNCA inventes, extrapoles ni completes con imaginación pacientes, edades, líneas de tratamiento,
resultados o complicaciones que no estén literalmente presentes en la evidencia de arriba.
Si el bloque de evidencia NO contiene ningún fragmento marcado explícitamente como
"[CASO CLÍNICO REAL SIMILAR]", entonces NO EXISTE ningún caso real similar disponible: no
inventes uno, no redactes un "paciente de X años" ficticio bajo ningún concepto. En ese caso,
en la sección de Casos Reales debés escribir textualmente que no hay casos clínicos reales
similares registrados en la base de datos.

Instrucciones para estructurar tu respuesta:
1. **Sugerencias Basadas en Casos Reales (CBR):** Solo si la evidencia contiene fragmentos marcados literalmente como "[CASO CLÍNICO REAL SIMILAR]", cita esos casos (edad, tratamientos aplicados, complicaciones post-operatorias y cómo se resolvieron) tal como aparecen en la evidencia, sin agregar detalles que no estén ahí. Si no hay ninguno, decilo explícitamente.
2. **Evidencia Molecular (CIViC):** Si la evidencia contiene fragmentos marcados como "[EVIDENCIA CIViC]", citalos indicando el gen/variante, la enfermedad, el nivel de evidencia y la(s) terapia(s) asociada(s) tal como figuran.
3. **Recomendaciones de la Literatura Científica:** Utiliza la evidencia de estudios y papers para justificar decisiones farmacológicas o clínicas con base científica, citando solo lo que efectivamente está en la evidencia.
4. **Claridad y Rigor:** Responde con rigor oncológico, de forma estructurada, usando viñetas claras y en español.
5. **Ausencia de Evidencia:** Si la evidencia está vacía o no responde a la pregunta, dilo con honestidad y sugiere estudios complementarios en vez de inventar contenido.
"""

prompt_clinico = PromptTemplate(
    template=prompt_clinico_template,
    input_variables=["tipo_cancer", "evidencia", "question"]
)

def log_sintesis_start(inputs):
    log_clinical(
        f"Sintetizando respuesta con el modelo '{DEFAULT_MODEL}' "
        f"(origen de evidencia: {inputs['metodo_recuperacion']})..."
    )
    return inputs

llm_sintesis = get_llm(temperature=0.1)

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
                "metodo_recuperacion": x["datos_recuperacion"]["metodo_recuperacion"]
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

        return {
            "success": True,
            "respuesta": resultado["respuesta"],
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