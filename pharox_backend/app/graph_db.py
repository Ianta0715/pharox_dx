import os
import json
import unicodedata
from neo4j import GraphDatabase
from langchain_neo4j import Neo4jGraph
from app.ai_gateway import generar_embedding
from app.logging_config import get_logger

logger = get_logger("pharox.graph_db")

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "pharoxpass")

# Inicializar driver original para compatibilidad con la inicialización y el ETL
driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

# Variable global para inicialización perezosa de Neo4jGraph
_graph_instance = None

def get_graph() -> Neo4jGraph:
    """
    Retorna o inicializa de manera perezosa (lazy load) la instancia global 
    de Neo4jGraph de LangChain.
    Desactivamos refresh_schema=True durante la inicialización para evitar
    que falle al arrancar si no está instalado el plugin APOC en Neo4j.
    """
    global _graph_instance
    if _graph_instance is None:
        _graph_instance = Neo4jGraph(
            url=NEO4J_URI,
            username=NEO4J_USER,
            password=NEO4J_PASSWORD,
            refresh_schema=False
        )
    return _graph_instance

NOTA_INDEPENDENCIA_SUBGRAFOS = (
    "\nNota importante: los subgrafos de conocimiento público (CIViC, EnsayoClinico, "
    "VarianteClinVar, EstudioCBio) son independientes entre sí y del subgrafo clínico de "
    "pacientes (Paciente -> Tumor/CasoClinico); no existen relaciones directas entre ellos. "
    "Para preguntas sobre pacientes o historiales, consulta el subgrafo clínico; para "
    "evidencia científica, ensayos, variantes o frecuencia poblacional, consulta el "
    "subgrafo público correspondiente.\n"
)


def obtener_esquema_nativo_fallback() -> str:
    """
    Construye dinámicamente una representación del esquema de base de datos
    utilizando consultas Cypher estándar que no dependen de APOC.
    """
    try:
        g = get_graph()
        # Obtener etiquetas de nodos existentes
        res_labels = g.query("CALL db.labels()")
        labels = [r.get("label") for r in res_labels if r.get("label")]
        
        # Obtener relaciones existentes
        res_rels = g.query("CALL db.relationshipTypes()")
        rels = [r.get("relationshipType") for r in res_rels if r.get("relationshipType")]
    except Exception as e:
        logger.error(f"[Schema Fallback] Error obteniendo metadatos nativos: {e}")
        labels = ["Paciente", "Tumor", "Tratamiento"]
        rels = ["DIAGNOSTICADO_CON", "TRATADO_CON"]
        
    # Descripciones completas conocidas de cada etiqueta/relación del grafo.
    # Se filtran contra las etiquetas/relaciones que realmente existen en la
    # base para no confundir al LLM con tipos que aún no fueron ingeridos.
    propiedades_por_label = {
        "Paciente": "hash (String), edad (Integer), anio_nacimiento (Integer)",
        "Tumor": "tipo_cancer (String), cie10 (String), descripcion (String)",
        "Tratamiento": "droga (String)",
        "Literatura": "id (String), text (String), tipo_cancer (String), categoria (String), drogas (String)",
        "CasoClinico": "id (String), resumen_clinico (String)",
        "HallazgoPatologico": "id (String), subtipo_molecular (String), lateralidad (String), procedimiento (String)",
        "EventoPostOperatorio": "id (String), tipo_evento (String), detalle_resolucion (String), resultado (String)",
        "AntecedenteMedico": "tipo (String), medicacion (String)",
        "Variante": "civic_id (Integer), nombre (String), nombre_variante (String), gen (String), link (String), tipos_so (List<String>), perfil_molecular_nombre (String), perfil_molecular_descripcion (String)",
        "Evidencia": "civic_id (Integer), nombre (String), descripcion (String), tipo (String), direccion (String), nivel (String), rating (String), significancia (String), estado (String), origen (String), interaccion_terapia (String)",
        "Enfermedad": "civic_id (Integer), nombre (String), nombre_mostrado (String), doid (String)",
        "Terapia": "civic_id (Integer), nombre (String), ncit_id (String)",
        "Fuente": "civic_id (Integer), pubmed_id (String), tipo_fuente (String), cita (String), anio (Integer), journal (String), url (String)",
        "EnsayoClinico": "nct_id (String), titulo (String), fases (List<String>), estado (String), condiciones (List<String>), intervenciones (List<String>), sponsor (String), resumen (String), paises (List<String>), url (String)",
        "VarianteClinVar": "variation_id (Integer), nombre (String), gen (String), tipo_variante (String), hgvs_c (String), hgvs_p (String), assembly (String), cromosoma (String), posicion (String), clasificacion_clinica (String), review_status (String), ultima_evaluacion (String)",
        "CondicionClinVar": "nombre (String), medgen_id (String)",
        "EstudioCBio": "study_id (String), nombre (String), descripcion (String), n_pacientes (Integer)",
        "GenCBio": "entrez_id (Integer), hugo_symbol (String)",
        "FrecuenciaGenCBio": "frecuencia_id (String), n_alterados (Integer), n_perfilados (Integer), porcentaje (Float), tipo_alteracion (String)",
    }

    relaciones_conocidas = [
        ("DIAGNOSTICADO_CON", "Paciente", "Tumor"),
        ("TRATADO_CON", "Tumor", "Tratamiento"),
        ("CORRESPONDE_A", "Paciente", "CasoClinico"),
        ("INCLUYE_HALLAZGO", "CasoClinico", "HallazgoPatologico"),
        ("TUVO_EVENTO_POSTOP", "CasoClinico", "EventoPostOperatorio"),
        ("TIENE_ANTECEDENTE", "Paciente", "AntecedenteMedico"),
        ("ASOCIADO_A_TUMOR", "HallazgoPatologico", "Tumor"),
        ("TIENE_EVIDENCIA", "Variante", "Evidencia"),
        ("ASOCIADA_A_ENFERMEDAD", "Evidencia", "Enfermedad"),
        ("INVOLUCRA_TERAPIA", "Evidencia", "Terapia"),
        ("RESPALDADA_POR", "Evidencia", "Fuente"),
        ("ASOCIADA_A_CONDICION", "VarianteClinVar", "CondicionClinVar"),
        ("REPORTA_FRECUENCIA", "EstudioCBio", "FrecuenciaGenCBio"),
        ("SOBRE_GEN", "FrecuenciaGenCBio", "GenCBio"),
    ]

    schema_str = "Nodos y propiedades en la base de datos de grafos:\n"
    for label, props in propiedades_por_label.items():
        if label in labels or not labels:
            schema_str += f"- {label}: {props}\n"

    schema_str += "\nRelaciones y direcciones permitidas en el grafo:\n"
    for rel, origen, destino in relaciones_conocidas:
        if rel in rels or not rels:
            schema_str += f"- (:{origen})-[:{rel}]->(:{destino})\n"

    schema_str += NOTA_INDEPENDENCIA_SUBGRAFOS

    return schema_str

def obtener_esquema_grafo() -> str:
    """
    Auto-inspección del Esquema: Utiliza Neo4jGraph para leer el esquema.
    Si falla debido a que APOC no está instalado o habilitado en Neo4j,
    aplica un fallback robusto utilizando consultas nativas.
    """
    g = get_graph()
    try:
        g.refresh_schema()
        # g.schema viene de la introspección automática de APOC (labels/relaciones/
        # tipos), pero no incluye la nota de independencia entre subgrafos — sin
        # esto el LLM podría alucinar joins entre, p. ej., Tumor y EnsayoClinico.
        return g.schema + NOTA_INDEPENDENCIA_SUBGRAFOS
    except Exception as e:
        logger.warning(f"[Schema Warning] No se pudo obtener el esquema mediante APOC de LangChain: {e}")
        logger.warning("[Schema Fallback] Usando inspección nativa como respaldo seguro.")
        return obtener_esquema_nativo_fallback()

def buscar_archivo(nombre_archivo, directorios_adicionales=None):
    """Busca un archivo en varias ubicaciones posibles."""
    candidatos = [
        nombre_archivo,
        os.path.join("data", nombre_archivo),
        os.path.join("..", "data", nombre_archivo),
        os.path.join("pharox_backend", "data", nombre_archivo),
        os.path.join("app", "data", nombre_archivo),
        os.path.join("/app/data", nombre_archivo)
    ]
    if directorios_adicionales:
        for d in directorios_adicionales:
            candidatos.append(os.path.join(d, nombre_archivo))
            candidatos.append(os.path.join("data", d, nombre_archivo))
            candidatos.append(os.path.join("..", "data", d, nombre_archivo))
            candidatos.append(os.path.join("pharox_backend", "data", d, nombre_archivo))
            candidatos.append(os.path.join("/app/data", d, nombre_archivo))

    for path in candidatos:
        if os.path.exists(path) and os.path.isfile(path):
            return path
    return None

def inicializar_esquema(tx):
    """Crea restricciones e índice vectorial en Neo4j si no existen."""
    # 1. Crear restricciones de unicidad existentes
    tx.run("CREATE CONSTRAINT unique_patient_hash IF NOT EXISTS FOR (p:Paciente) REQUIRE p.hash IS UNIQUE")
    tx.run("CREATE CONSTRAINT unique_tumor_type IF NOT EXISTS FOR (t:Tumor) REQUIRE t.tipo_cancer IS UNIQUE")

    # 2. Índice vectorial para literatura oncológica (768 dims para nomic-embed-text)
    tx.run("""
        CREATE VECTOR INDEX literature_vectors IF NOT EXISTS
        FOR (n:Literatura) ON (n.embedding)
        OPTIONS {indexConfig: {
            `vector.dimensions`: 768,
            `vector.similarity_function`: 'cosine'
        }}
    """)

    # 3. Nuevas restricciones para casos clínicos reales
    tx.run("CREATE CONSTRAINT unique_caso_id IF NOT EXISTS FOR (c:CasoClinico) REQUIRE c.id IS UNIQUE")
    tx.run("CREATE CONSTRAINT unique_hallazgo_id IF NOT EXISTS FOR (h:HallazgoPatologico) REQUIRE h.id IS UNIQUE")
    tx.run("CREATE CONSTRAINT unique_evento_id IF NOT EXISTS FOR (e:EventoPostOperatorio) REQUIRE e.id IS UNIQUE")

    # 3b. Restricción de unicidad para Literatura (faltaba: hoy la ingesta sintética
    # solo evita duplicados corriendo una vez sobre grafo vacío; los gold scripts que
    # hacen MERGE repetible, como europepmc_to_neo4j.py, la necesitan explícita).
    tx.run("CREATE CONSTRAINT unique_literatura_id IF NOT EXISTS FOR (l:Literatura) REQUIRE l.id IS UNIQUE")

    # 4. Índice vectorial para casos clínicos reales (búsqueda por similitud)
    tx.run("""
        CREATE VECTOR INDEX caso_clinico_vectors IF NOT EXISTS
        FOR (c:CasoClinico) ON (c.embedding)
        OPTIONS {indexConfig: {
            `vector.dimensions`: 768,
            `vector.similarity_function`: 'cosine'
        }}
    """)

def inicializar_db():
    """Inicializa el esquema e ingesta la literatura y los historiales en Neo4j."""
    logger.info("Inicializando base de datos de grafos Neo4j...")
    
    # 1. Crear esquema
    with driver.session() as session:
        session.execute_write(inicializar_esquema)
        
    # Verificar si ya existen nodos de Literatura para no duplicar
    with driver.session() as session:
        result = session.run("MATCH (n:Literatura) RETURN count(n) AS cnt")
        lit_count = result.single()["cnt"]
        
        result_gold = session.run("MATCH (p:Paciente) RETURN count(p) AS cnt")
        paciente_count = result_gold.single()["cnt"]
        
    path_lit = buscar_archivo("data_oncologica.json")
    path_gold = buscar_archivo("dataset_estructurado_seguro.json", ["gold"])
    if not path_gold:
        for rpath in ["dataset_estructurado_seguro.json", "../dataset_estructurado_seguro.json"]:
            if os.path.exists(rpath):
                path_gold = rpath
                break

    # Ingestar literatura si está vacía
    if lit_count == 0 and path_lit:
        logger.info(f"Cargando literatura en Neo4j desde {path_lit}...")
        try:
            with open(path_lit, "r", encoding="utf-8") as f:
                data_lit = json.load(f)
                
            with driver.session() as session:
                for idx, item in enumerate(data_lit):
                    tipo_cancer = item.get("tipo_cancer", "No Especificado")
                    categoria = item.get("categoria", "General")
                    drogas = item.get("agente_drogas", "")
                    mutacion = item.get("mutacion_biomarcador", "")
                    linea = item.get("linea_de_tratamiento", "")
                    resultado = item.get("resultado_clave", "")
                    toxicidad = item.get("toxicidad_limitante_dosis", "")
                    resistencia = item.get("resistencia_adquirida", "")
                    fuente = item.get("fuente_estudio", "Desconocida")
                    
                    texto = (
                        f"Cáncer: {tipo_cancer} | Categoría: {categoria} | "
                        f"Drogas/Agentes: {drogas} | Mutación/Biomarcador: {mutacion} | "
                        f"Línea de tratamiento: {linea} | Resultado Clave: {resultado} | "
                        f"Toxicidad limitante: {toxicidad} | Resistencia: {resistencia} | "
                        f"Fuente: {fuente}"
                    )
                    
                    logger.info(f"   Generating embedding for lit_{idx}...")
                    vector = generar_embedding(texto)
                    
                    session.run("""
                        CREATE (l:Literatura {
                            id: $id,
                            text: $text,
                            tipo_cancer: $tipo_cancer,
                            categoria: $categoria,
                            drogas: $drogas,
                            embedding: $embedding
                        })
                    """, {
                        "id": f"lit_{idx}",
                        "text": texto,
                        "tipo_cancer": tipo_cancer,
                        "categoria": categoria,
                        "drogas": drogas,
                        "embedding": vector
                    })
            logger.info("Literatura ingesta en Neo4j de forma correcta.")
        except Exception as e:
            logger.error(f"Error al ingestar literatura en Neo4j: {e}")
    else:
        logger.info(f"Omitiendo ingesta de literatura. Ya contiene {lit_count} registros.")

    # Ingestar historial clínico (Capa Gold) si está vacío
    if paciente_count == 0 and path_gold:
        logger.info(f"Cargando historial clínico en Neo4j desde {path_gold}...")
        try:
            with open(path_gold, "r", encoding="utf-8") as f:
                data_gold = json.load(f)
                
            with driver.session() as session:
                for idx, item in enumerate(data_gold):
                    clinicos = item.get("datos_clinicos", {})
                    tipo_cancer = clinicos.get("tipo_cancer", "No Especificado")
                    cie10 = clinicos.get("cie10_diagnostico", "Desconocido")
                    diag_desc = clinicos.get("diagnostico_descripcion", "Evaluación registrada")
                    
                    tratamientos = item.get("datos_tratamiento", {})
                    droga = tratamientos.get("droga_prescripta", "Esquema sistémico")
                    
                    demograficos = item.get("datos_demograficos", {})
                    edad = demograficos.get("edad_estimada", None)
                    anio_nac = demograficos.get("anio_nacimiento", None)
                    
                    p_hash = item.get("patient_hash_sha256", f"anon_{idx}")
                    
                    # Ejecutar merge en Neo4j para construir el Grafo Clínico
                    session.run("""
                        MERGE (p:Paciente {hash: $hash})
                        ON CREATE SET p.edad = $edad, p.anio_nacimiento = $anio_nac
                        
                        MERGE (t:Tumor {tipo_cancer: $tipo_cancer})
                        ON CREATE SET t.cie10 = $cie10, t.descripcion = $descripcion
                        
                        MERGE (tr:Tratamiento {droga: $droga})

                        MERGE (p)-[:DIAGNOSTICADO_CON]->(t)
                        MERGE (t)-[:TRATADO_CON]->(tr)
                    """, {
                        "hash": p_hash,
                        "edad": edad,
                        "anio_nac": anio_nac,
                        "tipo_cancer": tipo_cancer,
                        "cie10": cie10,
                        "descripcion": diag_desc,
                        "droga": droga
                    })
            logger.info("Historial clínico (Capa Gold) mapeado en Neo4j con éxito.")
        except Exception as e:
            logger.error(f"Error al mapear Capa Gold en Neo4j: {e}")
    else:
        logger.info(f"Omitiendo ingesta de historial clínico. Ya contiene {paciente_count} pacientes.")

def buscar_casos_similares(texto_consulta: str, n_results: int = 2) -> list[str]:
    """
    Busca casos clínicos reales similares a la consulta usando el índice vectorial
    de nodos :CasoClinico y enriquece el resultado con eventos post-operatorios asociados.
    Retorna una lista de strings formateados para incluir como contexto en la síntesis clínica.
    """
    contextos = []
    g = get_graph()

    try:
        query_vector = generar_embedding(texto_consulta)
        res = g.query("""
            CALL db.index.vector.queryNodes('caso_clinico_vectors', $n_results, $query_vector)
            YIELD node AS caso, score
            WHERE score > 0.6
            OPTIONAL MATCH (caso)-[:INCLUYE_HALLAZGO]->(hp:HallazgoPatologico)
            OPTIONAL MATCH (caso)-[:TUVO_EVENTO_POSTOP]->(ev:EventoPostOperatorio)
            RETURN
                caso.resumen_clinico AS resumen,
                collect(DISTINCT hp.subtipo_molecular + ' mama ' + hp.lateralidad + ' - ' + hp.procedimiento) AS hallazgos,
                collect(DISTINCT ev.tipo_evento + ': ' + ev.detalle_resolucion + ' → ' + ev.resultado) AS eventos,
                score
            ORDER BY score DESC
        """, {"n_results": n_results, "query_vector": query_vector})

        for record in res:
            hallazgos_str = "; ".join([h for h in record.get("hallazgos", []) if h])
            eventos_str = "; ".join([e for e in record.get("eventos", []) if e])
            score = record.get("score", 0)

            contexto = (
                f"[CASO CLÍNICO REAL SIMILAR — similitud: {score:.2f}] "
                f"{record.get('resumen', '')}"
            )
            if hallazgos_str:
                contexto += f" | Hallazgos: {hallazgos_str}"
            if eventos_str:
                contexto += f" | Complicaciones post-op: {eventos_str}"

            contextos.append(contexto)
            logger.info(f"[Casos Similares] Caso encontrado con score {score:.2f}")

    except Exception as e:
        logger.error(f"[Casos Similares] Error en búsqueda vectorial de casos: {e}")

    return contextos


_STOPWORDS_CIVIC = {
    "para", "como", "cual", "cuales", "sobre", "cancer", "tipo", "tumor",
    "paciente", "pacientes", "with", "that", "this", "from", "para", "cuál",
    "cáncer", "qué", "que", "los", "las", "del", "con", "una", "uno", "por",
}


def _normalizar_texto_comparacion(texto: str) -> str:
    """
    Normaliza (sin tildes, minúsculas, sin espacios extra) para comparar tipo_cancer
    sin depender de que cada fuente nueva escriba el literal exacto "Cáncer de Mama".
    """
    if not texto:
        return ""
    sin_tildes = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return sin_tildes.strip().lower()


def _extraer_palabras_clave(texto: str) -> list[str]:
    """Extrae tokens relevantes (>=4 letras, sin stopwords) de un texto libre."""
    if not texto:
        return []
    crudo = [t.strip(".,;:()¿?¡!").lower() for t in texto.split()]
    return [t for t in crudo if len(t) >= 4 and t not in _STOPWORDS_CIVIC]


def buscar_evidencia_civic(texto_consulta: str, tipo_cancer: str = None, n_results: int = 3) -> list[str]:
    """
    Busca evidencia clínico-molecular en el subgrafo de conocimiento CIViC
    (Variante -> Evidencia -> Enfermedad/Terapia/Fuente) por coincidencia de
    palabras clave (gen, variante, enfermedad, terapia). No usa embeddings
    porque los nodos CIViC no tienen índice vectorial propio.
    """
    contextos = []
    g = get_graph()

    palabras = _extraer_palabras_clave(texto_consulta) + _extraer_palabras_clave(tipo_cancer)
    if not palabras:
        return contextos

    try:
        res = g.query("""
            UNWIND $palabras AS kw
            MATCH (v:Variante)-[:TIENE_EVIDENCIA]->(e:Evidencia)
            OPTIONAL MATCH (e)-[:ASOCIADA_A_ENFERMEDAD]->(en:Enfermedad)
            OPTIONAL MATCH (e)-[:INVOLUCRA_TERAPIA]->(t:Terapia)
            OPTIONAL MATCH (e)-[:RESPALDADA_POR]->(f:Fuente)
            WHERE toLower(coalesce(v.gen, '')) CONTAINS kw
               OR toLower(coalesce(v.nombre_variante, '')) CONTAINS kw
               OR toLower(coalesce(en.nombre, '')) CONTAINS kw
               OR toLower(coalesce(en.nombre_mostrado, '')) CONTAINS kw
               OR toLower(coalesce(t.nombre, '')) CONTAINS kw
            WITH DISTINCT v, e, en, collect(DISTINCT t.nombre) AS terapias, collect(DISTINCT f.cita) AS fuentes
            RETURN
                v.gen AS gen,
                v.nombre_variante AS variante,
                e.descripcion AS descripcion,
                e.nivel AS nivel,
                e.significancia AS significancia,
                e.interaccion_terapia AS interaccion,
                en.nombre_mostrado AS enfermedad,
                terapias,
                fuentes
            LIMIT $n_results
        """, {"palabras": palabras, "n_results": n_results})

        for record in res:
            terapias_str = ", ".join([t for t in record.get("terapias", []) if t])
            fuentes_str = "; ".join([f for f in record.get("fuentes", []) if f][:2])

            contexto = (
                f"[EVIDENCIA CIViC] Gen {record.get('gen', '?')} "
                f"variante {record.get('variante', '?')}"
            )
            if record.get("enfermedad"):
                contexto += f" en {record.get('enfermedad')}"
            contexto += f": {record.get('descripcion', '')}"
            if record.get("significancia"):
                contexto += f" | Significancia: {record.get('significancia')}"
            if record.get("nivel"):
                contexto += f" | Nivel de evidencia: {record.get('nivel')}"
            if terapias_str:
                contexto += f" | Terapia(s) involucrada(s): {terapias_str}"
            if fuentes_str:
                contexto += f" | Fuente(s): {fuentes_str}"

            contextos.append(contexto)
            logger.info(f"[Evidencia CIViC] Coincidencia encontrada: gen={record.get('gen')}")

    except Exception as e:
        logger.error(f"[Evidencia CIViC] Error en búsqueda por palabras clave: {e}")

    return contextos


def buscar_ensayos_clinicos(texto_consulta: str, tipo_cancer: str = None, n_results: int = 3) -> list[str]:
    """
    Busca ensayos clinicos (subgrafo independiente :EnsayoClinico, ver
    app/gold/clinicaltrials_to_neo4j.py) por coincidencia de palabras clave en
    condiciones, intervenciones o titulo. condiciones/intervenciones son listas,
    por eso usa ANY(...) en vez de CONTAINS directo (que solo aplica a strings).
    """
    contextos = []
    g = get_graph()

    palabras = _extraer_palabras_clave(texto_consulta) + _extraer_palabras_clave(tipo_cancer)
    if not palabras:
        return contextos

    try:
        res = g.query("""
            UNWIND $palabras AS kw
            MATCH (e:EnsayoClinico)
            WHERE ANY(c IN e.condiciones WHERE toLower(c) CONTAINS kw)
               OR ANY(i IN e.intervenciones WHERE toLower(i) CONTAINS kw)
               OR toLower(coalesce(e.titulo, '')) CONTAINS kw
            WITH DISTINCT e
            RETURN
                e.nct_id AS nct_id,
                e.titulo AS titulo,
                e.fases AS fases,
                e.estado AS estado,
                e.condiciones AS condiciones,
                e.intervenciones AS intervenciones,
                e.url AS url
            LIMIT $n_results
        """, {"palabras": palabras, "n_results": n_results})

        for record in res:
            condiciones_str = ", ".join(record.get("condiciones") or [])
            intervenciones_str = ", ".join(record.get("intervenciones") or [])
            fases_str = ", ".join(record.get("fases") or [])

            contexto = (
                f"[ENSAYO CLÍNICO] {record.get('nct_id', '?')}: {record.get('titulo', '')} "
                f"| Estado: {record.get('estado', '?')}"
            )
            if fases_str:
                contexto += f" | Fase: {fases_str}"
            if condiciones_str:
                contexto += f" | Condición(es): {condiciones_str}"
            if intervenciones_str:
                contexto += f" | Intervención(es): {intervenciones_str}"
            if record.get("url"):
                contexto += f" | {record.get('url')}"

            contextos.append(contexto)
            logger.info(f"[Ensayos Clínicos] Coincidencia encontrada: {record.get('nct_id')}")

    except Exception as e:
        logger.error(f"[Ensayos Clínicos] Error en búsqueda por palabras clave: {e}")

    return contextos


def buscar_variantes_clinvar(texto_consulta: str, tipo_cancer: str = None, n_results: int = 3) -> list[str]:
    """
    Busca variantes clasificadas clinicamente en el subgrafo independiente
    :VarianteClinVar (ver app/gold/clinvar_to_neo4j.py) por coincidencia de
    palabras clave en gen, nombre de la variante o condicion asociada.
    """
    contextos = []
    g = get_graph()

    palabras = _extraer_palabras_clave(texto_consulta) + _extraer_palabras_clave(tipo_cancer)
    if not palabras:
        return contextos

    try:
        res = g.query("""
            UNWIND $palabras AS kw
            MATCH (v:VarianteClinVar)
            OPTIONAL MATCH (v)-[:ASOCIADA_A_CONDICION]->(c:CondicionClinVar)
            WHERE toLower(coalesce(v.gen, '')) CONTAINS kw
               OR toLower(coalesce(v.nombre, '')) CONTAINS kw
               OR toLower(coalesce(c.nombre, '')) CONTAINS kw
            WITH DISTINCT v, collect(DISTINCT c.nombre) AS condiciones
            RETURN
                v.gen AS gen,
                v.hgvs_c AS hgvs_c,
                v.hgvs_p AS hgvs_p,
                v.clasificacion_clinica AS clasificacion,
                v.review_status AS review_status,
                condiciones
            LIMIT $n_results
        """, {"palabras": palabras, "n_results": n_results})

        for record in res:
            condiciones_str = ", ".join([c for c in record.get("condiciones", []) if c][:3])

            contexto = (
                f"[VARIANTE CLINVAR] Gen {record.get('gen', '?')} "
                f"{record.get('hgvs_c', '')} ({record.get('hgvs_p', '')}) "
                f"| Clasificación: {record.get('clasificacion', 'No clasificada')}"
            )
            if record.get("review_status"):
                contexto += f" | Revisión: {record.get('review_status')}"
            if condiciones_str:
                contexto += f" | Condición(es): {condiciones_str}"

            contextos.append(contexto)
            logger.info(f"[Variantes ClinVar] Coincidencia encontrada: gen={record.get('gen')}")

    except Exception as e:
        logger.error(f"[Variantes ClinVar] Error en búsqueda por palabras clave: {e}")

    return contextos


def buscar_frecuencia_cbioportal(texto_consulta: str, tipo_cancer: str = None, n_results: int = 3) -> list[str]:
    """
    Busca frecuencia de alteracion (mutacion o CNA) por gen en el subgrafo
    independiente EstudioCBio/GenCBio/FrecuenciaGenCBio (ver
    app/gold/cbioportal_to_neo4j.py) por coincidencia de palabras clave en el
    simbolo del gen o el nombre del estudio.
    """
    contextos = []
    g = get_graph()

    palabras = _extraer_palabras_clave(texto_consulta) + _extraer_palabras_clave(tipo_cancer)
    if not palabras:
        return contextos

    try:
        res = g.query("""
            UNWIND $palabras AS kw
            MATCH (est:EstudioCBio)-[:REPORTA_FRECUENCIA]->(f:FrecuenciaGenCBio)-[:SOBRE_GEN]->(g:GenCBio)
            WHERE toLower(coalesce(g.hugo_symbol, '')) CONTAINS kw
               OR toLower(coalesce(est.nombre, '')) CONTAINS kw
            WITH DISTINCT est, f, g
            RETURN
                g.hugo_symbol AS gen,
                f.tipo_alteracion AS tipo_alteracion,
                f.porcentaje AS porcentaje,
                f.n_alterados AS n_alterados,
                f.n_perfilados AS n_perfilados,
                est.nombre AS estudio
            ORDER BY f.porcentaje DESC
            LIMIT $n_results
        """, {"palabras": palabras, "n_results": n_results})

        for record in res:
            contexto = (
                f"[FRECUENCIA CBIOPORTAL] Gen {record.get('gen', '?')}: "
                f"{record.get('tipo_alteracion', '?')} en {record.get('porcentaje', 0)}% "
                f"({record.get('n_alterados', 0)}/{record.get('n_perfilados', 0)} casos) "
                f"| Estudio: {record.get('estudio', '?')}"
            )
            contextos.append(contexto)
            logger.info(f"[Frecuencia cBioPortal] Coincidencia encontrada: gen={record.get('gen')}")

    except Exception as e:
        logger.error(f"[Frecuencia cBioPortal] Error en búsqueda por palabras clave: {e}")

    return contextos


def buscar_contexto_hibrido(query: str, tipo_cancer: str = None, n_results: int = 3) -> list[str]:
    """
    Realiza una búsqueda híbrida (Graph RAG) utilizando Neo4jGraph de LangChain:
    1. Búsqueda por Similitud de Vectores en el índice de Literatura.
    2. Recorrido de Relaciones en el Grafo Clínico de Pacientes reales.
    3. Casos clínicos reales similares (búsqueda vectorial de CasoClinico).
    4. Evidencia clínico-molecular del subgrafo CIViC (Variante/Evidencia/Terapia).
    5. Ensayos clínicos activos del subgrafo EnsayoClinico (ClinicalTrials.gov).
    6. Variantes clasificadas del subgrafo VarianteClinVar (ClinVar).
    7. Frecuencia de alteración por gen del subgrafo EstudioCBio (cBioPortal).
    """
    contextos = []
    g = get_graph()
    
    # 1. Búsqueda Vectorial
    try:
        query_vector = generar_embedding(query)
        res = g.query("""
            CALL db.index.vector.queryNodes('literature_vectors', $n_results, $query_vector)
            YIELD node, score
            RETURN node.text AS text, node.tipo_cancer AS tipo_cancer, score
        """, {"n_results": n_results * 2, "query_vector": query_vector})
        
        vec_count = 0
        for record in res:
            node_cancer = record.get("tipo_cancer")
            node_text = record.get("text")
            
            # Filtrar por tipo de cáncer si es necesario (comparación normalizada:
            # no depende de que la fuente haya escrito el literal exacto)
            if (
                tipo_cancer
                and tipo_cancer != "No Especificado"
                and _normalizar_texto_comparacion(node_cancer) != _normalizar_texto_comparacion(tipo_cancer)
            ):
                continue
                
            contextos.append(node_text)
            vec_count += 1
            if vec_count >= n_results:
                break
    except Exception as e:
        logger.error(f"Error en búsqueda vectorial Neo4j: {e}")

    # 2. Búsqueda de Relaciones en el Grafo Clínico (Graph Traversing)
    if tipo_cancer and tipo_cancer != "No Especificado":
        try:
            res_grafo = g.query("""
                MATCH (p:Paciente)-[:DIAGNOSTICADO_CON]->(t:Tumor)-[:TRATADO_CON]->(tr:Tratamiento)
                WHERE t.tipo_cancer = $tipo_cancer
                RETURN DISTINCT t.tipo_cancer AS cancer, t.descripcion AS desc, tr.droga AS droga
                LIMIT 2
            """, {"tipo_cancer": tipo_cancer})
            
            for record in res_grafo:
                historial_str = (
                    f"Historial Clínico: Paciente diagnosticado de '{record['cancer']}' "
                    f"({record['desc']}) recibió tratamiento con: '{record['droga']}'"
                )
                contextos.append(historial_str)
        except Exception as e:
            logger.error(f"Error en consulta de relaciones Neo4j: {e}")

    # 3. Búsqueda de Casos Clínicos Reales Similares (Case-Based Reasoning)
    try:
        casos_similares = buscar_casos_similares(query, n_results=2)
        if casos_similares:
            contextos.extend(casos_similares)
            logger.info(f"[Híbrida] {len(casos_similares)} caso(s) clínico(s) real(es) recuperado(s).")
    except Exception as e:
        logger.error(f"Error en búsqueda de casos clínicos similares: {e}")

    # 4. Búsqueda de Evidencia Clínico-Molecular en el subgrafo CIViC
    try:
        evidencia_civic = buscar_evidencia_civic(query, tipo_cancer=tipo_cancer, n_results=n_results)
        if evidencia_civic:
            contextos.extend(evidencia_civic)
            logger.info(f"[Híbrida] {len(evidencia_civic)} evidencia(s) CIViC recuperada(s).")
    except Exception as e:
        logger.error(f"Error en búsqueda de evidencia CIViC: {e}")

    # 5. Búsqueda de Ensayos Clínicos Activos (subgrafo EnsayoClinico)
    try:
        ensayos = buscar_ensayos_clinicos(query, tipo_cancer=tipo_cancer, n_results=2)
        if ensayos:
            contextos.extend(ensayos)
            logger.info(f"[Híbrida] {len(ensayos)} ensayo(s) clínico(s) recuperado(s).")
    except Exception as e:
        logger.error(f"Error en búsqueda de ensayos clínicos: {e}")

    # 6. Búsqueda de Variantes Clasificadas (subgrafo VarianteClinVar)
    try:
        variantes_clinvar = buscar_variantes_clinvar(query, tipo_cancer=tipo_cancer, n_results=2)
        if variantes_clinvar:
            contextos.extend(variantes_clinvar)
            logger.info(f"[Híbrida] {len(variantes_clinvar)} variante(s) ClinVar recuperada(s).")
    except Exception as e:
        logger.error(f"Error en búsqueda de variantes ClinVar: {e}")

    # 7. Búsqueda de Frecuencia de Alteración (subgrafo EstudioCBio/GenCBio)
    try:
        frecuencias = buscar_frecuencia_cbioportal(query, tipo_cancer=tipo_cancer, n_results=2)
        if frecuencias:
            contextos.extend(frecuencias)
            logger.info(f"[Híbrida] {len(frecuencias)} frecuencia(s) cBioPortal recuperada(s).")
    except Exception as e:
        logger.error(f"Error en búsqueda de frecuencia cBioPortal: {e}")

    return contextos

def obtener_estado_grafo():
    """Retorna métricas básicas de los nodos y relaciones en el grafo para depuración usando Neo4jGraph."""
    try:
        g = get_graph()
        
        res_nodos = g.query("""
            MATCH (n) 
            RETURN labels(n)[0] AS label, count(n) AS cantidad
        """)
        nodos = {record.get("label", "Sin Etiqueta") or "Sin Etiqueta": record.get("cantidad", 0) for record in res_nodos}
        
        res_rels = g.query("""
            MATCH ()-[r]->() 
            RETURN type(r) AS tipo, count(r) AS cantidad
        """)
        relaciones = {record.get("tipo", "Sin Tipo"): record.get("cantidad", 0) for record in res_rels}
        
        res_lit = g.query("""
            MATCH (l:Literatura) 
            RETURN l.id AS id, l.tipo_cancer AS cancer, l.drogas AS drogas 
            LIMIT 20
        """)
        detalles_lit = [{"id": r.get("id"), "tipo_cancer": r.get("cancer"), "drogas": r.get("drogas")} for r in res_lit]
        
        return {
            "nodos": nodos,
            "relaciones": relaciones,
            "detalles_literatura": detalles_lit
        }
    except Exception as e:
        logger.error(f"Error al obtener estado del grafo: {e}")
        return {"error": str(e)}
