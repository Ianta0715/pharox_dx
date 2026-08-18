import os
import json
from typing import Optional
from fastapi import FastAPI, HTTPException, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Importaciones nativas de LangChain
from langchain_core.prompts import PromptTemplate, FewShotPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough, RunnableParallel, RunnableLambda

# Importaciones de Pharox DX
from app.graph_db import (
    inicializar_db, 
    buscar_contexto_hibrido, 
    obtener_estado_grafo, 
    obtener_esquema_grafo,
    get_graph
)
from app.ai_gateway import get_llm, DEFAULT_MODEL

app = FastAPI(
    title="Pharox DX - Core Engine (LangChain Graph RAG)",
    version="3.0.0",
    description="Backend Server orquestado con LangChain (LCEL), Neo4j y Ollama"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------------------------------------------------------
# SISTEMA DE LOGS VISUALES EN TIEMPO REAL (Terminal Colors & Emojis)
# -------------------------------------------------------------------------
class TerminalColors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'

def log_info(msg: str):
    print(f"{TerminalColors.OKCYAN}[INFO] {msg}{TerminalColors.ENDC}")

def log_success(msg: str):
    print(f"{TerminalColors.OKGREEN}[OK] {msg}{TerminalColors.ENDC}")

def log_warning(msg: str):
    print(f"{TerminalColors.WARNING}[WARN] {msg}{TerminalColors.ENDC}")

def log_error(msg: str):
    print(f"{TerminalColors.FAIL}[ERROR] {msg}{TerminalColors.ENDC}")

def log_security(msg: str, blocked: bool = False):
    color = TerminalColors.FAIL if blocked else TerminalColors.OKGREEN
    tag = "[BLOQUEADO]" if blocked else "[APROBADO]"
    print(f"{TerminalColors.BOLD}{color}[SEGURIDAD] {tag} {msg}{TerminalColors.ENDC}")

def log_clinical(msg: str):
    print(f"{TerminalColors.HEADER}[CLINICA] {msg}{TerminalColors.ENDC}")


class ConsultaMedicaRequest(BaseModel):
    consulta: str
    tipo_cancer: str = "Cáncer de Pulmón (NSCLC)"

@app.on_event("startup")
def startup_event():
    try:
        log_info("Inicializando base de datos de grafos Neo4j...")
        inicializar_db()
        log_success("Base de datos de grafos e índices listos para operar.")
    except Exception as e:
        log_error(f"Error al inicializar la BD de grafos: {e}")

@app.get("/")
def health_check():
    return {"status": "ok", "service": "Pharox DX LCEL Graph Engine Running"}

# -------------------------------------------------------------------------
# CONFIGURACIÓN DE FEW-SHOT PROMPTING PARA TEXT-TO-CYPHER
# -------------------------------------------------------------------------
EXAMPLES = [
    {
        "question": "¿Cuántos pacientes están diagnosticados con Cáncer de Pulmón (NSCLC)?",
        "query": "MATCH (p:Paciente)-[:DIAGNOSTICADO_CON]->(t:Tumor {{tipo_cancer: 'Cáncer de Pulmón (NSCLC)'}}) RETURN count(p) AS cantidad"
    },
    {
        "question": "¿Qué droga se prescribió para el Tumor de tipo Cáncer de Mama?",
        "query": "MATCH (t:Tumor {{tipo_cancer: 'Cáncer de Mama'}})-[:TRATADO_CON]->(tr:Tratamiento) RETURN DISTINCT tr.droga AS droga"
    },
    {
        "question": "Drogas usadas en pacientes menores de 50 años diagnosticados con Cáncer de Colon.",
        "query": "MATCH (p:Paciente)-[:DIAGNOSTICADO_CON]->(t:Tumor {{tipo_cancer: 'Cáncer de Colon'}})-[:TRATADO_CON]->(tr:Tratamiento) WHERE p.edad < 50 RETURN DISTINCT tr.droga AS droga"
    },
    {
        "question": "¿Qué edad promedio tienen los pacientes tratados con Pembrolizumab?",
        "query": "MATCH (p:Paciente)-[:DIAGNOSTICADO_CON]->(t:Tumor)-[:TRATADO_CON]->(tr:Tratamiento {{droga: 'Pembrolizumab'}}) RETURN avg(p.edad) AS edad_promedio"
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
3. Asegúrate de respetar loscopy nombres de nodos, propiedades y relaciones exactos del esquema.

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
    print("\n" + "=" * 80)
    log_info(f"Nueva consulta recibida del médico:")
    print(f"   {TerminalColors.BOLD}Pregunta:{TerminalColors.ENDC} '{inputs['question']}'")
    print(f"   {TerminalColors.BOLD}Contexto Cáncer:{TerminalColors.ENDC} '{inputs.get('tipo_cancer', 'No Especificado')}'")
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
    Recibe la consulta generada, la valida, la ejecuta y aplica fallback si:
    - Ocurre algún error sintáctico o de conexión.
    - La consulta es bloqueada por seguridad.
    - La consulta no devuelve registros (lista vacía).
    """
    cypher_query = input_dict["cypher_query"]
    question = input_dict["question"]
    tipo_cancer = input_dict.get("tipo_cancer", "Cáncer de Pulmón (NSCLC)")
    
    log_info("Traducción completada. Cypher generado:")
    print(f"   {TerminalColors.OKBLUE}{cypher_query}{TerminalColors.ENDC}")
    
    try:
        # 1. Validar contra el filtro de seguridad
        validated_query = validate_cypher(cypher_query)
        
        # 2. Ejecutar en Neo4j a través de la instancia del grafo de LangChain
        g = get_graph()
        records = g.query(validated_query)
        
        # 3. Si devuelve registros, los guardamos como evidencia estructurada
        if records:
            log_success(f"Consulta ejecutada en Neo4j. Se recuperaron {len(records)} registro(s).")
            evidencia = json.dumps(records, ensure_ascii=False, indent=2)
            return {
                "evidencia": evidencia,
                "cypher_utilizado": validated_query,
                "metodo_recuperacion": "Text-to-Cypher (Neo4j Graph)"
            }
        else:
            log_warning("La consulta Cypher no retornó registros en la base de datos.")
            
    except Exception as e:
        log_error(f"Fallo en ejecución o bloqueo de seguridad: {e}")
        
    # 4. Fallback: Búsqueda híbrida (vectorial en Literatura + relaciones en Grafo Clínico)
    log_warning("Activando Fallback: Ejecutando búsqueda híbrida estándar (Vectores + Relaciones)...")
    contextos = buscar_contexto_hibrido(query=question, tipo_cancer=tipo_cancer)
    evidencia = "\n".join([f"- {c}" for c in contextos]) if contextos else "Sin evidencia local registrada en la base de datos."
    
    if contextos:
        log_success(f"Búsqueda híbrida completada. Se recuperaron {len(contextos)} fragmento(s) de evidencia.")
    else:
        log_warning("No se encontró evidencia relevante tampoco en la búsqueda de fallback.")
        
    return {
        "evidencia": evidencia,
        "cypher_utilizado": cypher_query, 
        "metodo_recuperacion": "Fallback (Búsqueda Híbrida: Vectorial + Relaciones)"
    }

# -------------------------------------------------------------------------
# CADENA DE SÍNTESIS CLÍNICA (PASO 3)
# -------------------------------------------------------------------------
prompt_clinico_template = """Actúas como un Copiloto Clínico Experto en Oncología de Precisión.
Analiza la consulta médica relacionada con el tipo de cáncer: {tipo_cancer}.
Evidencia recuperada de nuestra base de datos de grafos de Neo4j (incluye literatura científica, estadísticas de grafos y casos clínicos reales similares):
{evidencia}
Consulta Médica del Profesional: "{question}"
Instrucciones para estructurar tu respuesta:
1. **Sugerencias Basadas en Casos Reales (CBR):** Si la evidencia contiene fragmentos marcados como "[CASO CLÍNICO REAL SIMILAR]", cita estos casos (mencionando edad, tratamientos aplicados, complicaciones post-operatorias y cómo se resolvieron) como sugerencias empíricas para el médico.
2. **Recomendaciones de la Literatura Científica:** Utiliza la evidencia de estudios y papers para justificar decisiones farmacológicas o clínicas con base científica.
3. **Claridad y Rigor:** Responde con rigor oncológico, de forma estructurada, usando viñetas claras y en español.
4. **Ausencia de Evidencia:** Si la evidencia está vacía o no responde a la pregunta, dilo con honestidad y sugiere estudios complementarios.
"""

prompt_clinico = PromptTemplate(
    template=prompt_clinico_template,
    input_variables=["tipo_cancer", "evidencia", "question"]
)

def log_sintesis_start(inputs):
    log_clinical(f"Sintetizando respuesta con el modelo '{DEFAULT_MODEL}'...")
    print(f"   {TerminalColors.BOLD}Origen de Evidencia:{TerminalColors.ENDC} {inputs['metodo_recuperacion']}")
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
@app.post("/api/v1/consultar")
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
        
        print("\n" + "=" * 80)
        return {
            "success": True,
            "respuesta": resultado["respuesta"],
            "cypher_utilizado": resultado["cypher_utilizado"],
            "evidencia_recuperada": resultado["evidencia_recuperada"],
            "metodo_recuperacion": resultado["metodo_recuperacion"]
        }
    except Exception as e:
        log_error(f"Excepción en el endpoint: {e}")
        print("\n" + "=" * 80)
        raise HTTPException(
            status_code=500, 
            detail=f"Error en el Copiloto Clínico (LangChain Graph RAG): {repr(e)}"
        )

@app.get("/api/v1/debug/graph_db")
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

@app.post("/api/v1/casos/ingestar")
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
        print("\n" + "=" * 80)
        return resultado
    except Exception as e:
        log_error(f"Error al ingestar caso clínico: {e}")
        print("\n" + "=" * 80)
        raise HTTPException(
            status_code=500,
            detail=f"Error al procesar el caso clínico: {repr(e)}"
        )


@app.get("/api/v1/casos/buscar")
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

@app.post("/api/v1/casos/ingestar_texto")
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