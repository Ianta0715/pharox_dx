"""
Explorador text-to-Cypher del grafo (/api/v1/explorar).

Sale del camino de decisión clínica: sirve para que un analista o un médico
haga preguntas ad hoc a la base ("¿cuántas pacientes triple negativo hay en el
registro?") y vea las filas crudas junto con el Cypher que las produjo.

Dos barreras, en este orden de importancia:
1. La consulta corre en una transacción de SOLO LECTURA (conocimiento/grafo.leer):
   Neo4j rechaza cualquier escritura o cambio de esquema aunque el filtro
   de abajo fallara.
2. validate_cypher: rechaza por palabra completa (no por subcadena: "OFFSET"
   ya no dispara "SET") cláusulas de escritura, DROP, LOAD CSV, CALL y
   FOREACH, ignorando lo que esté dentro de strings literales.
"""
from __future__ import annotations

import re
from typing import Any

EXAMPLES = [
    {
        "question": "¿Cuántos pacientes están diagnosticados con Cáncer de Mama?",
        "query": "MATCH (p:Paciente)-[:DIAGNOSTICADO_CON]->(t:Tumor {tipo_cancer: 'Cáncer de Mama'}) RETURN count(p) AS cantidad",
    },
    {
        "question": "¿Qué evidencia clínica existe para la variante H1047R del gen PIK3CA?",
        "query": "MATCH (v:Variante {gen: 'PIK3CA', nombre_variante: 'H1047R'})-[:TIENE_EVIDENCIA]->(e:Evidencia) RETURN e.descripcion AS descripcion, e.nivel AS nivel, e.significancia AS significancia",
    },
    {
        "question": "¿Qué terapias están asociadas a la evidencia de HER2 Positive Breast Cancer?",
        "query": "MATCH (en:Enfermedad {nombre_mostrado: 'HER2 Positive Breast Cancer'})<-[:ASOCIADA_A_ENFERMEDAD]-(e:Evidencia)-[:INVOLUCRA_TERAPIA]->(t:Terapia) RETURN DISTINCT t.nombre AS terapia",
    },
    {
        "question": "¿En qué fuentes (papers) se basa la evidencia sobre el gen ESR1?",
        "query": "MATCH (v:Variante {gen: 'ESR1'})-[:TIENE_EVIDENCIA]->(e:Evidencia)-[:RESPALDADA_POR]->(f:Fuente) RETURN DISTINCT f.cita AS cita, f.journal AS journal, f.anio AS anio",
    },
    {
        "question": "¿Qué hallazgos patológicos se registraron en los casos clínicos de tumores de mama?",
        "query": "MATCH (c:CasoClinico)-[:INCLUYE_HALLAZGO]->(hp:HallazgoPatologico)-[:ASOCIADO_A_TUMOR]->(t:Tumor {tipo_cancer: 'Cáncer de Mama'}) RETURN hp.subtipo_molecular AS subtipo, hp.procedimiento AS procedimiento",
    },
    {
        "question": "¿Qué ensayos clínicos activos existen para cáncer de mama HER2 positivo?",
        "query": "MATCH (e:EnsayoClinico) WHERE e.estado = 'RECRUITING' AND ANY(c IN e.condiciones WHERE toLower(c) CONTAINS 'breast') AND ANY(c IN e.condiciones WHERE toLower(c) CONTAINS 'her2') RETURN e.nct_id AS nct_id, e.titulo AS titulo, e.fases AS fases",
    },
    {
        "question": "¿Cuál es la clasificación clínica de las variantes de BRCA1 en ClinVar?",
        "query": "MATCH (v:VarianteClinVar {gen: 'BRCA1'}) RETURN v.nombre AS variante, v.clasificacion_clinica AS clasificacion, v.review_status AS revision",
    },
    {
        "question": "¿Con qué frecuencia se altera el gen PIK3CA en estudios de cBioPortal de cáncer de mama?",
        "query": "MATCH (est:EstudioCBio)-[:REPORTA_FRECUENCIA]->(f:FrecuenciaGenCBio)-[:SOBRE_GEN]->(g:GenCBio {hugo_symbol: 'PIK3CA'}) RETURN est.nombre AS estudio, f.porcentaje AS porcentaje_alterado, f.tipo_alteracion AS tipo",
    },
    {
        "question": "¿Cuántas pacientes reales del registro de tumores tienen subtipo molecular Triple Negativo?",
        "query": "MATCH (r:RegistroTumor) WHERE r.topografia_codigo STARTS WITH 'C50' AND r.subtipo_molecular = 'Triple_negativo' RETURN count(r) AS cantidad",
    },
    {
        "question": "¿Cuál es el protocolo de tratamiento estándar registrado para un perfil HER2 positivo?",
        "query": "MATCH (p:ProtocoloTratamiento) WHERE p.topografia_codigo CONTAINS 'C50' AND p.subtipo_molecular_match = 'HER2_positivo' RETURN p.protocolo_esquema AS esquema, p.intencion_linea AS intencion, p.modalidad AS modalidad",
    },
    {
        "question": "¿Hay actualizaciones recientes de guías ASCO o ESMO para perfil Triple Negativo?",
        "query": "MATCH (a:ActualizacionProtocolo) WHERE a.subtipos_detectados CONTAINS 'Triple_negativo' RETURN a.titulo AS titulo, a.sociedad AS sociedad, a.fecha_publicacion AS fecha ORDER BY a.fecha_publicacion DESC LIMIT 5",
    },
    {
        "question": "¿Para qué ensayos clínicos ya se evaluó la elegibilidad de la paciente RT_0001?",
        "query": "MATCH (r:RegistroTumor {id: 'RT_0001'})-[rel]->(e:EnsayoClinico) WHERE type(rel) IN ['HABILITA_TRIAL', 'CONDICIONA_TRIAL', 'EXCLUYE_TRIAL'] RETURN type(rel) AS veredicto, e.nct_id AS nct_id, e.titulo AS titulo, rel.motivo AS motivo",
    },
]

_PREFIJO = """Actúas como un experto traductor de Lenguaje Natural a consultas Cypher para Neo4j, especializado en oncología de precisión.
Basándote en el esquema de base de datos de grafos provisto, traduce la pregunta a una consulta Cypher válida.

Reglas estrictas de generación:
1. Genera ÚNICAMENTE la consulta Cypher sin bloques de código (sin ```cypher), sin explicaciones, sin comentarios y sin texto adicional.
2. Solo se permiten operaciones de lectura (MATCH, OPTIONAL MATCH, WHERE, WITH, UNWIND, RETURN, ORDER BY, LIMIT).
3. Respetá los nombres de nodos, propiedades y relaciones exactos del esquema.

Esquema de la base de datos de grafos:
{esquema}
"""

PALABRAS_PROHIBIDAS = ("CREATE", "MERGE", "DELETE", "DETACH", "SET", "REMOVE", "DROP", "LOAD", "CALL", "FOREACH")
LIMITE_FILAS = 100


def construir_prompt(esquema: str, pregunta: str) -> str:
    ejemplos = "\n\n".join(f"Pregunta: {e['question']}\nCypher: {e['query']}" for e in EXAMPLES)
    return f"{_PREFIJO.format(esquema=esquema)}\n{ejemplos}\n\nPregunta: {pregunta}\nCypher: "


def clean_cypher_output(text: str) -> str:
    """Quita bloques markdown y restos de razonamiento (<think>) que qwen3 a veces filtra al contenido."""
    cleaned = re.sub(r"<think>.*?</think>", "", text.strip(), flags=re.DOTALL)
    cleaned = re.sub(r"</?think>", "", cleaned)
    cleaned = cleaned.replace("```cypher", "").replace("```", "")
    return cleaned.strip()


def _sin_literales(cypher: str) -> str:
    """Reemplaza el contenido de strings y comentarios, para no bloquear 'set' dentro de un valor."""
    sin_strings = re.sub(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"", "''", cypher)
    return re.sub(r"//[^\n]*|/\*.*?\*/", " ", sin_strings, flags=re.DOTALL)


def validate_cypher(cypher_query: str) -> str:
    """Valida que la consulta sea de solo lectura. Devuelve la consulta limpia o levanta ValueError."""
    cleaned = clean_cypher_output(cypher_query)
    codigo = _sin_literales(cleaned).upper()
    for palabra in PALABRAS_PROHIBIDAS:
        if re.search(rf"\b{palabra}\b", codigo):
            raise ValueError(f"Operación Cypher no permitida por seguridad: contiene la palabra clave '{palabra}'.")
    if ";" in codigo.rstrip().rstrip(";"):
        raise ValueError("Operación Cypher no permitida por seguridad: más de una sentencia.")
    return cleaned


def con_limite(cypher: str, limite: int = LIMITE_FILAS) -> str:
    """Agrega LIMIT si la consulta no lo trae, para no devolver el grafo entero."""
    consulta = cypher.strip().rstrip(";").strip()
    if re.search(r"\bLIMIT\b", _sin_literales(consulta), re.IGNORECASE):
        return consulta
    return f"{consulta} LIMIT {limite}"


def generar_cypher(pregunta: str, esquema: str, llm: Any) -> str:
    respuesta = llm.invoke(construir_prompt(esquema, pregunta))
    contenido = respuesta.content if hasattr(respuesta, "content") else str(respuesta)
    return clean_cypher_output(contenido)
