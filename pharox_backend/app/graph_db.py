import os
import json
import re
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
    "VarianteClinVar, EstudioCBio), los subgrafos reales de mama (RegistroTumor, "
    "ProtocoloTratamiento, ActualizacionProtocolo) y el subgrafo clínico sintético de "
    "pacientes (Paciente -> Tumor/CasoClinico) son independientes ENTRE SÍ; no existen "
    "relaciones directas entre ellos (con la única excepción marcada más abajo). Para "
    "preguntas sobre pacientes o historiales sintéticos, consulta el subgrafo clínico; para "
    "evidencia científica, ensayos, variantes o frecuencia poblacional, consulta el subgrafo "
    "público correspondiente; para pacientes reales, protocolos estándar o actualizaciones de "
    "guías, consulta el subgrafo real correspondiente y cruza por coincidencia EXACTA de "
    "subtipo molecular (o CONTAINS si la propiedad es una lista/string separado por comas, "
    "como ActualizacionProtocolo.subtipos_detectados o EnsayoClinico.subtipos_relacionados), "
    "nunca por texto libre.\n"
    "ÚNICA EXCEPCIÓN: (:RegistroTumor)-[:HABILITA_TRIAL|:CONDICIONA_TRIAL|:EXCLUYE_TRIAL]->"
    "(:EnsayoClinico) SÍ es una relación directa real -- elegibilidad a ensayos ya evaluada y "
    "persistida (ver app/gold/reglas_elegibilidad_trials.py). El tipo de relación no se puede "
    "parametrizar en Cypher: usar WHERE type(rel) IN [...] o type(rel) para leerlo.\n"
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
        "EnsayoClinico": "nct_id (String), titulo (String), fases (List<String>), estado (String), condiciones (List<String>), intervenciones (List<String>), sponsor (String), resumen (String), paises (List<String>), url (String), criterios_elegibilidad (String, texto libre del protocolo), sexo (String: ALL/FEMALE/MALE), edad_minima_anios (Integer), edad_maxima_anios (Integer), acepta_voluntarios_sanos (Boolean), subtipos_relacionados (List<String>, vocabulario de subtipo_molecular, vacío si el ensayo no restringe por subtipo)",
        "VarianteClinVar": "variation_id (Integer), nombre (String), gen (String), tipo_variante (String), hgvs_c (String), hgvs_p (String), assembly (String), cromosoma (String), posicion (String), clasificacion_clinica (String), review_status (String), ultima_evaluacion (String)",
        "CondicionClinVar": "nombre (String), medgen_id (String)",
        "EstudioCBio": "study_id (String), nombre (String), descripcion (String), n_pacientes (Integer)",
        "GenCBio": "entrez_id (Integer), hugo_symbol (String)",
        "FrecuenciaGenCBio": "frecuencia_id (String), n_alterados (Integer), n_perfilados (Integer), porcentaje (Float), tipo_alteracion (String)",
        "RegistroTumor": "id (String), edad (Integer), sexo (String), topografia_codigo (String, filtrar SIEMPRE por STARTS WITH 'C50' para mama), topografia_nombre (String), estadio_clinico (String), estadio_patologico (String), subtipo_molecular (String, vocabulario cerrado: HER2_positivo / Triple_negativo / RH_positivo_HER2_negativo / desconocido), receptor_estrogeno (String), receptor_progesterona (String), her2 (String), ecog (String), hospital (String), fecha_diagnostico (String, formato ISO YYYY-MM-DD)",
        "ProtocoloTratamiento": "id (String), topografia_codigo (String, filtrar SIEMPRE por CONTAINS 'C50' para mama), histologia_subtipo (String), subtipo_molecular_match (String, mismo vocabulario cerrado que RegistroTumor.subtipo_molecular -- OJO: acá la propiedad se llama subtipo_molecular_match, no subtipo_molecular), estadio_tnm (String), intencion_linea (String), protocolo_esquema (String), modalidad (String), biomarcadores_criticos (String)",
        "ActualizacionProtocolo": "id (String), titulo (String), resumen (String), sociedad (String: ASCO/ESMO/NCCN/otro), fuente (String), fecha_publicacion (String, formato ISO), subtipos_detectados (String, lista separada por comas del mismo vocabulario -- usar CONTAINS, nunca igualdad exacta), url (String)",
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
        ("HABILITA_TRIAL", "RegistroTumor", "EnsayoClinico"),
        ("CONDICIONA_TRIAL", "RegistroTumor", "EnsayoClinico"),
        ("EXCLUYE_TRIAL", "RegistroTumor", "EnsayoClinico"),
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
    """Inicializa el esquema e ingesta los historiales clínicos sintéticos en Neo4j."""
    logger.info("Inicializando base de datos de grafos Neo4j...")

    # 1. Crear esquema
    with driver.session() as session:
        session.execute_write(inicializar_esquema)

    # Literatura real (:Literatura) NO se siembra acá -- la única fuente es
    # app/gold/europepmc_to_neo4j.py (PMIDs reales, filtrado a mama). Hubo una
    # semilla sintética (data_oncologica.json, 16 fichas sin cita real, ni
    # siquiera todas de mama) que se sacó a propósito: no aportaba evidencia
    # utilizable y podía aparecer en la búsqueda vectorial antes del filtro de
    # tipo_cancer. Si tu Neo4j local todavía tiene esos nodos de una corrida
    # vieja, limpialos con:
    #   MATCH (n:Literatura) WHERE n.id STARTS WITH 'lit_' DETACH DELETE n

    with driver.session() as session:
        result_gold = session.run("MATCH (p:Paciente) RETURN count(p) AS cnt")
        paciente_count = result_gold.single()["cnt"]

    path_gold = buscar_archivo("dataset_estructurado_seguro.json", ["gold"])
    if not path_gold:
        for rpath in ["dataset_estructurado_seguro.json", "../dataset_estructurado_seguro.json"]:
            if os.path.exists(rpath):
                path_gold = rpath
                break

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


_PATRONES_SUBTIPO_MOLECULAR = [
    ("HER2_positivo", ["her2 positivo", "her2+", "her2 +", "her2-positivo"]),
    ("Triple_negativo", ["triple negativo", "triple-negativo", "tnbc"]),
    ("RH_positivo_HER2_negativo", ["luminal", "receptor hormonal positivo", "rh positivo", "hormonal positivo", "re+", "rp+"]),
]

# Patrones para leer RE/RP/HER2 como el médico realmente los escribe en una
# nota clínica (porcentaje o score de IHQ/FISH), no como una frase armada.
_PATRON_RECEPTOR_PORCENTAJE = r"\b{sigla}\b\D{{0,10}}?(\d{{1,3}})\s*%"
_PATRON_HER2_IHQ = r"\bher-?2\b(?:\s*(?:ihq|ihc|por\s+inmunohistoqu[ií]mica))?\D{0,10}?([0-3])\s*\+"
_PATRON_HER2_FISH_POSITIVO = r"\bher-?2\b.{0,20}?fish.{0,15}?(?:positiv|amplific)"


def _extraer_valor_receptor(texto: str, sigla_regex: str) -> int | None:
    m = re.search(_PATRON_RECEPTOR_PORCENTAJE.format(sigla=sigla_regex), texto, re.IGNORECASE)
    return int(m.group(1)) if m else None


def _inferir_subtipo_de_valores_numericos(texto: str) -> str | None:
    """
    Deriva el subtipo molecular a partir de los valores estructurados que trae
    una nota clínica real -- "RE 0%", "HER2 IHQ 1+" -- en vez de depender de
    que alguien escriba la frase "triple negativo". Mismo criterio de
    clasificacion que derivar_subtipo_molecular() en
    app/gold/registro_tumores_to_neo4j.py, adaptado a texto libre en vez de
    columnas de excel: positivo si RE/RP >= 1% (convencion ASCO/CAP), HER2
    positivo si IHQ 3+ o FISH amplificado. IHQ 2+ sin FISH queda equivoco a
    proposito -- no se fuerza una clasificacion que el dato no sostiene, igual
    que el front trata "Equivocal" como su propio estado.

    Nacio de un caso real que se clasifico mal: el texto solo traia "RE 0%,
    RP 0%, HER2 IHQ 1+" (nunca la palabra "triple negativo"), y sin esto la
    deteccion por frase no encontraba nada -- ninguna busqueda se filtraba por
    subtipo y el sistema terminaba citando protocolos y ensayos de otros
    perfiles moleculares.
    """
    re_valor = _extraer_valor_receptor(texto, "re")
    rp_valor = _extraer_valor_receptor(texto, "rp")
    her2_match = re.search(_PATRON_HER2_IHQ, texto, re.IGNORECASE)
    her2_ihq = int(her2_match.group(1)) if her2_match else None
    her2_fish_positivo = bool(re.search(_PATRON_HER2_FISH_POSITIVO, texto, re.IGNORECASE))

    if her2_ihq is None and not her2_fish_positivo:
        return None  # sin HER2 no se puede descartar HER2_positivo con confianza

    her2_positivo = her2_fish_positivo or (her2_ihq is not None and her2_ihq == 3)
    her2_negativo = her2_ihq is not None and her2_ihq <= 1
    her2_equivoco = her2_ihq == 2 and not her2_fish_positivo

    if her2_positivo:
        return "HER2_positivo"
    if her2_equivoco:
        return None  # HER2 sin determinar: no es HER2 negativo, no inventar
    if not her2_negativo:
        return None

    if re_valor is None or rp_valor is None:
        return None  # HER2 negativo pero RE/RP no informados: no alcanza

    if re_valor < 1 and rp_valor < 1:
        return "Triple_negativo"
    return "RH_positivo_HER2_negativo"


def detectar_subtipo_molecular(texto: str) -> str | None:
    """
    Detecta el subtipo molecular de mama de un texto libre, usando el mismo
    vocabulario de 4 categorias que RegistroTumor y ProtocoloTratamiento
    (HER2_positivo / Triple_negativo / RH_positivo_HER2_negativo). Primero
    intenta la frase explicita ("triple negativo"); si no hay ninguna,
    intenta derivarlo de los valores numericos de RE/RP/HER2 si estan
    presentes. None si ninguno de los dos caminos encuentra nada -- en ese
    caso quien llama NO debe inventar un filtro, debe traer resultados sin
    filtrar por subtipo.

    Publica (sin "_" inicial) porque además de usarla las funciones de
    búsqueda de este módulo, app/main.py la necesita para que el paso de
    Text-to-Cypher tenga el subtipo ya resuelto en vez de tener que inferirlo
    él mismo de los porcentajes crudos.
    """
    texto_norm = _normalizar_texto_comparacion(texto or "")
    for subtipo, patrones in _PATRONES_SUBTIPO_MOLECULAR:
        if any(p in texto_norm for p in patrones):
            return subtipo
    return _inferir_subtipo_de_valores_numericos(texto or "")


# Siglas clínicas de uso habitual en español que colisionan con símbolos de
# genes reales si se buscan por coincidencia de subcadena -- nació de un bug
# real: "PAAF" (punción-aspiración con aguja fina) matcheaba por CONTAINS
# contra el gen real PAAF1, y el sistema citaba datos genómicos irrelevantes.
_ABREVIATURAS_CLINICAS_EXCLUIDAS = {
    "PAAF", "IHQ", "IHC", "ECOG", "TNM", "RMN", "TAC", "PET", "FISH", "ASCO",
    "ESMO", "NCCN", "AJCC", "OMS", "RE", "RP", "CSE", "CSI", "CII", "CIE",
}


def _extraer_genes_mencionados(texto: str) -> list[str]:
    """
    Extrae tokens con forma de símbolo génico (mayúsculas, 2-10 caracteres,
    puede llevar dígitos) del texto ORIGINAL -- a diferencia de
    _extraer_palabras_clave, no baja a minúsculas: la mayúscula es la señal
    de que es un símbolo (BRCA1, TP53, ERBB2), no una palabra común. Se usa
    para matchear genes por IGUALDAD exacta contra hugo_symbol/gen en vez de
    CONTAINS de subcadena -- ver _ABREVIATURAS_CLINICAS_EXCLUIDAS.
    """
    if not texto:
        return []
    vistos: list[str] = []
    for token in re.findall(r"\b[A-Z][A-Z0-9]{1,9}\b", texto):
        if token not in _ABREVIATURAS_CLINICAS_EXCLUIDAS and token not in vistos:
            vistos.append(token)
    return vistos


def buscar_registros_tumores_mama(texto_consulta: str, n_results: int = 3) -> list[str]:
    """
    Busca registros reales de pacientes de cancer de mama (subgrafo
    independiente :RegistroTumor, ver app/gold/registro_tumores_to_neo4j.py).

    SIEMPRE filtra por topografia_codigo STARTS WITH 'C50': la planilla origen
    (hoja "MAMA") trae 3 casos con codigo C44 (piel de mama, uno de ellos un
    melanoma) que no son carcinoma de mama -- se excluyen aca en vez de
    borrarlos del grafo, para no perder el dato real pero tampoco mezclarlo
    con razonamiento de cancer de mama.

    Si la consulta menciona un subtipo molecular reconocible, filtra ademas
    por coincidencia EXACTA de subtipo_molecular (nunca por texto libre sobre
    biomarcadores -- ver la nota en protocolos_tratamiento_to_neo4j.py sobre
    por que CONTAINS de texto libre trajo resultados de otro perfil en las
    pruebas iniciales).
    """
    contextos = []
    g = get_graph()

    subtipo = detectar_subtipo_molecular(texto_consulta)

    try:
        cypher = """
            MATCH (r:RegistroTumor)
            WHERE r.topografia_codigo STARTS WITH 'C50'
        """
        params = {"n_results": n_results}
        if subtipo:
            cypher += " AND r.subtipo_molecular = $subtipo"
            params["subtipo"] = subtipo
        cypher += """
            RETURN r.edad AS edad, r.lateralidad AS lateralidad,
                   r.morfologia_nombre AS morfologia, r.grado_diferenciacion AS grado,
                   r.estadio_clinico AS estadio_clinico, r.estadio_patologico AS estadio_patologico,
                   r.subtipo_molecular AS subtipo, r.receptor_estrogeno AS re,
                   r.receptor_progesterona AS rp, r.her2 AS her2, r.hospital AS hospital
            LIMIT $n_results
        """
        res = g.query(cypher, params)

        for record in res:
            contexto = (
                f"[REGISTRO REAL DE PACIENTE - {record.get('hospital') or '?'}] "
                f"Edad {record.get('edad') or '?'} años, tumor {record.get('lateralidad') or '?'}, "
                f"{record.get('morfologia') or '?'}, grado: {record.get('grado') or '?'}, "
                f"estadio clínico {record.get('estadio_clinico') or '?'} / patológico {record.get('estadio_patologico') or 'sin estadificación patológica'}, "
                f"subtipo molecular: {record.get('subtipo') or '?'} "
                f"(RE:{record.get('re') or '?'} RP:{record.get('rp') or '?'} HER2:{record.get('her2') or '?'})"
            )
            contextos.append(contexto)
            logger.info(f"[Registros Tumor Mama] Coincidencia encontrada: subtipo={record.get('subtipo')}")

    except Exception as e:
        logger.error(f"[Registros Tumor Mama] Error en búsqueda: {e}")

    return contextos


def buscar_protocolos_tratamiento_mama(texto_consulta: str, n_results: int = 2) -> list[str]:
    """
    Busca protocolos de tratamiento estandar de cancer de mama (subgrafo
    independiente :ProtocoloTratamiento, ver
    app/gold/protocolos_tratamiento_to_neo4j.py). Ya viene filtrado a mama
    desde la ingesta; se repite el filtro por topografia aca como defensa
    extra por si en el futuro se cargan protocolos de otros canceres bajo el
    mismo label. Si la consulta menciona un subtipo molecular reconocible,
    filtra por coincidencia EXACTA de subtipo_molecular_match.
    """
    contextos = []
    g = get_graph()

    subtipo = detectar_subtipo_molecular(texto_consulta)

    try:
        cypher = """
            MATCH (p:ProtocoloTratamiento)
            WHERE p.topografia_codigo CONTAINS 'C50'
        """
        params = {"n_results": n_results}
        if subtipo:
            cypher += " AND p.subtipo_molecular_match = $subtipo"
            params["subtipo"] = subtipo
        cypher += """
            RETURN p.histologia_subtipo AS histologia, p.biomarcadores_criticos AS biomarcadores,
                   p.estadio_tnm AS estadio, p.intencion_linea AS intencion,
                   p.protocolo_esquema AS esquema, p.modalidad AS modalidad
            LIMIT $n_results
        """
        res = g.query(cypher, params)

        for record in res:
            contexto = (
                f"[PROTOCOLO DE TRATAMIENTO ESTÁNDAR - Cáncer de Mama] {record.get('histologia') or '?'} | "
                f"Biomarcadores: {record.get('biomarcadores') or '?'} | Estadio: {record.get('estadio') or '?'} | "
                f"{record.get('intencion') or '?'} | Esquema: {record.get('esquema') or '?'} | "
                f"Modalidad: {record.get('modalidad') or '?'}"
            )
            contextos.append(contexto)
            logger.info(f"[Protocolos Tratamiento Mama] Coincidencia encontrada: {record.get('histologia')}")

    except Exception as e:
        logger.error(f"[Protocolos Tratamiento Mama] Error en búsqueda: {e}")

    return contextos


def buscar_actualizaciones_protocolo(texto_consulta: str, n_results: int = 2) -> list[str]:
    """
    Busca actualizaciones recientes de guias/protocolos de cancer de mama
    (subgrafo independiente :ActualizacionProtocolo, ver
    app/gold/vigilancia_protocolos_to_neo4j.py) relevantes a la consulta.

    Si la consulta menciona un subtipo molecular reconocible, prioriza
    actualizaciones cuyo subtipos_detectados lo incluya (coincidencia exacta
    de token dentro de la lista comma-joined, nunca CONTAINS de texto libre).
    Si no hay subtipo reconocible en la consulta, trae las mas recientes sin
    filtrar -- el objetivo de vigilancia es que el medico vea que algo cambio
    aunque no lo haya preguntado explicitamente.
    """
    contextos = []
    g = get_graph()

    subtipo = detectar_subtipo_molecular(texto_consulta)

    try:
        cypher = "MATCH (a:ActualizacionProtocolo)"
        params = {"n_results": n_results}
        if subtipo:
            cypher += " WHERE a.subtipos_detectados CONTAINS $subtipo"
            params["subtipo"] = subtipo
        cypher += """
            RETURN a.titulo AS titulo, a.resumen AS resumen, a.sociedad AS sociedad,
                   a.fuente AS fuente, a.fecha_publicacion AS fecha, a.subtipos_detectados AS subtipos,
                   a.url AS url
            ORDER BY a.fecha_publicacion DESC
            LIMIT $n_results
        """
        res = g.query(cypher, params)

        for record in res:
            contexto = (
                f"[ACTUALIZACIÓN DE PROTOCOLO - {record.get('sociedad') or '?'}, {record.get('fecha') or 'fecha desconocida'}] "
                f"{record.get('titulo') or '?'} (subtipos: {record.get('subtipos') or 'desconocido'}) "
                f"— {record.get('resumen') or 'sin resumen'} ({record.get('url') or record.get('fuente') or '?'})"
            )
            contextos.append(contexto)
            logger.info(f"[Vigilancia Protocolos] Coincidencia encontrada: {record.get('titulo')}")

    except Exception as e:
        logger.error(f"[Vigilancia Protocolos] Error en búsqueda: {e}")

    return contextos


def listar_actualizaciones_protocolo(subtipo_molecular: str | None = None, limit: int = 20) -> list[dict]:
    """
    Lista actualizaciones de protocolo para el endpoint dedicado de vigilancia
    (/api/v1/protocolos/actualizaciones) -- a diferencia de
    buscar_actualizaciones_protocolo (pensada para enriquecer una consulta
    puntual del medico), esta funcion devuelve los campos estructurados
    completos, no un string armado para el LLM.
    """
    g = get_graph()
    cypher = "MATCH (a:ActualizacionProtocolo)"
    params = {"limit": limit}
    if subtipo_molecular:
        cypher += " WHERE a.subtipos_detectados CONTAINS $subtipo_molecular"
        params["subtipo_molecular"] = subtipo_molecular
    cypher += """
        RETURN a.id AS id, a.titulo AS titulo, a.resumen AS resumen, a.sociedad AS sociedad,
               a.fuente AS fuente, a.fecha_publicacion AS fecha_publicacion,
               a.subtipos_detectados AS subtipos_detectados, a.url AS url
        ORDER BY a.fecha_publicacion DESC
        LIMIT $limit
    """
    return g.query(cypher, params)


def listar_registros_tumor(subtipo_molecular: str | None = None, limit: int = 50) -> list[dict]:
    """
    Lista pacientes reales (:RegistroTumor) del registro del Hospital Central
    Mendoza -- necesario para que el front pueda ofrecer un selector de
    pacientes reales sin conocer de antemano los ids RT_XXXX. Filtra a mama
    (topografia C50) igual que app/gold/reglas_elegibilidad_trials.py, ya que
    es el unico subconjunto para el que hay elegibilidad a ensayos calculada.
    """
    g = get_graph()
    cypher = "MATCH (r:RegistroTumor) WHERE r.topografia_codigo STARTS WITH 'C50'"
    params = {"limit": limit}
    if subtipo_molecular:
        cypher += " AND r.subtipo_molecular = $subtipo_molecular"
        params["subtipo_molecular"] = subtipo_molecular
    cypher += """
        RETURN r.id AS id, r.edad AS edad, r.topografia_nombre AS topografia_nombre,
               r.estadio_clinico AS estadio_clinico, r.subtipo_molecular AS subtipo_molecular,
               r.receptor_estrogeno AS receptor_estrogeno, r.receptor_progesterona AS receptor_progesterona,
               r.her2 AS her2, r.hospital AS hospital, r.fecha_diagnostico AS fecha_diagnostico
        ORDER BY r.id
        LIMIT $limit
    """
    return g.query(cypher, params)


def obtener_subtipo_molecular_registro(paciente_id: str) -> str | None:
    """
    Lee solo el subtipo_molecular de un :RegistroTumor por id -- usado para
    cruzar contra ProtocoloTratamiento sin traer el registro completo.
    Devuelve None si el paciente no existe (a diferencia de "desconocido",
    que es un subtipo real cuando el dato está pero no es concluyente).
    """
    g = get_graph()
    resultado = g.query(
        "MATCH (r:RegistroTumor {id: $paciente_id}) RETURN r.subtipo_molecular AS subtipo_molecular",
        {"paciente_id": paciente_id},
    )
    if not resultado:
        return None
    return resultado[0]["subtipo_molecular"] or "desconocido"


def protocolo_estandar_por_subtipo(subtipo_molecular: str) -> list[dict]:
    """
    Cruza un subtipo molecular (vocabulario de RegistroTumor.subtipo_molecular)
    contra el subgrafo independiente :ProtocoloTratamiento (ver
    app/gold/protocolos_tratamiento_to_neo4j.py) por coincidencia EXACTA de
    subtipo_molecular_match -- a diferencia de buscar_protocolos_tratamiento_mama
    (pensada para texto libre de una consulta), acá el subtipo ya es un dato
    estructurado conocido (el de un :RegistroTumor real), así que no hace
    falta detectarlo de un texto.
    """
    g = get_graph()
    cypher = """
        MATCH (p:ProtocoloTratamiento {subtipo_molecular_match: $subtipo_molecular})
        RETURN p.id AS id, p.histologia_subtipo AS histologia_subtipo,
               p.biomarcadores_criticos AS biomarcadores_criticos, p.estadio_tnm AS estadio_tnm,
               p.intencion_linea AS intencion_linea, p.protocolo_esquema AS protocolo_esquema,
               p.modalidad AS modalidad
        ORDER BY p.id
    """
    return g.query(cypher, {"subtipo_molecular": subtipo_molecular})


def resumen_cohorte_real() -> dict:
    """
    Resumen agregado de la cohorte real para el panel "Cohorte real" del
    front -- dos fuentes independientes en un solo viaje:

    1. RegistroTumor (Hospital Central Mendoza, mama): total y distribución
       por subtipo molecular y por estadio clínico.
    2. EstudioCBio (cBioPortal METABRIC): metadata del estudio y los genes
       con mayor frecuencia de alteración (misma relación REPORTA_FRECUENCIA/
       SOBRE_GEN que usan los few-shot de text-to-cypher en app/main.py).
    """
    g = get_graph()

    registro = g.query("""
        MATCH (r:RegistroTumor) WHERE r.topografia_codigo STARTS WITH 'C50'
        RETURN count(r) AS total,
               avg(r.edad) AS edad_promedio,
               collect(DISTINCT r.subtipo_molecular) AS subtipos_presentes
    """)[0]

    por_subtipo = g.query("""
        MATCH (r:RegistroTumor) WHERE r.topografia_codigo STARTS WITH 'C50'
        RETURN coalesce(r.subtipo_molecular, 'desconocido') AS subtipo, count(r) AS total
        ORDER BY total DESC
    """)

    por_estadio = g.query("""
        MATCH (r:RegistroTumor) WHERE r.topografia_codigo STARTS WITH 'C50'
        RETURN coalesce(r.estadio_clinico, 'sin registrar') AS estadio, count(r) AS total
        ORDER BY total DESC
    """)

    estudios = g.query("MATCH (e:EstudioCBio) RETURN e.study_id AS study_id, e.nombre AS nombre, e.descripcion AS descripcion, e.n_pacientes AS n_pacientes")

    top_genes = g.query("""
        MATCH (est:EstudioCBio)-[:REPORTA_FRECUENCIA]->(f:FrecuenciaGenCBio)-[:SOBRE_GEN]->(g:GenCBio)
        RETURN g.hugo_symbol AS gen, f.porcentaje AS porcentaje, f.tipo_alteracion AS tipo_alteracion
        ORDER BY f.porcentaje DESC
        LIMIT 15
    """)

    return {
        "registro_tumores": {
            "total": registro["total"],
            "edad_promedio": round(registro["edad_promedio"], 1) if registro["edad_promedio"] is not None else None,
            "por_subtipo": por_subtipo,
            "por_estadio": por_estadio,
        },
        "cbioportal": {
            "estudio": estudios[0] if estudios else None,
            "top_genes": top_genes,
        },
    }


def obtener_elegibilidad_trials_paciente(paciente_id: str) -> list[dict]:
    """
    Lee las relaciones de elegibilidad a ensayos PERSISTIDAS para un paciente
    (:RegistroTumor)-[:HABILITA_TRIAL|CONDICIONA_TRIAL|EXCLUYE_TRIAL]->(:EnsayoClinico),
    creadas por app/gold/reglas_elegibilidad_trials.py -- a diferencia de
    buscar_ensayos_clinicos (que re-evalúa desde cero en cada consulta), esto
    es una simple lectura de estado ya calculado, instantánea.
    """
    g = get_graph()
    cypher = """
        MATCH (p:RegistroTumor {id: $paciente_id})-[r]->(e:EnsayoClinico)
        WHERE type(r) IN ['HABILITA_TRIAL', 'CONDICIONA_TRIAL', 'EXCLUYE_TRIAL']
        RETURN type(r) AS veredicto, e.nct_id AS nct_id, e.titulo AS titulo, e.url AS url,
               r.motivo AS motivo, r.criterio_pendiente AS criterio_pendiente,
               r.regla AS regla, r.fecha_evaluacion AS fecha_evaluacion
        ORDER BY r.fecha_evaluacion DESC
    """
    return g.query(cypher, {"paciente_id": paciente_id})


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
    genes = _extraer_genes_mencionados(texto_consulta)
    if not palabras and not genes:
        return contextos

    try:
        # El gen se matchea por IGUALDAD exacta contra $genes (símbolos en
        # mayúsculas extraídos del texto original), nunca por CONTAINS de
        # palabra clave -- un CONTAINS de subcadena sobre un símbolo corto
        # (p. ej. "PAAF" de punción-aspiración vs. el gen real PAAF1) trae
        # evidencia genómica que no tiene nada que ver con la consulta.
        res = g.query("""
            UNWIND (CASE WHEN size($palabras) = 0 THEN [null] ELSE $palabras END) AS kw
            MATCH (v:Variante)-[:TIENE_EVIDENCIA]->(e:Evidencia)
            OPTIONAL MATCH (e)-[:ASOCIADA_A_ENFERMEDAD]->(en:Enfermedad)
            OPTIONAL MATCH (e)-[:INVOLUCRA_TERAPIA]->(t:Terapia)
            OPTIONAL MATCH (e)-[:RESPALDADA_POR]->(f:Fuente)
            WHERE v.gen IN $genes
               OR (kw IS NOT NULL AND (
                    toLower(coalesce(v.nombre_variante, '')) CONTAINS kw
                 OR toLower(coalesce(en.nombre, '')) CONTAINS kw
                 OR toLower(coalesce(en.nombre_mostrado, '')) CONTAINS kw
                 OR toLower(coalesce(t.nombre, '')) CONTAINS kw
               ))
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
        """, {"palabras": palabras, "genes": genes, "n_results": n_results})

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

    # Si la consulta trae un subtipo molecular reconocible (frase explícita o
    # valores de RE/RP/HER2), no alcanza con la coincidencia de palabras
    # clave: un ensayo puede mencionar "cáncer de mama" y aun así ser para un
    # perfil molecular opuesto al de la paciente (nació de un caso real:
    # triple negativo terminó citando un ensayo de solo-endocrino para RH+).
    subtipo = detectar_subtipo_molecular(texto_consulta)

    try:
        res = g.query("""
            UNWIND $palabras AS kw
            MATCH (e:EnsayoClinico)
            WHERE (
                ANY(c IN e.condiciones WHERE toLower(c) CONTAINS kw)
                OR ANY(i IN e.intervenciones WHERE toLower(i) CONTAINS kw)
                OR toLower(coalesce(e.titulo, '')) CONTAINS kw
              )
              AND ($subtipo IS NULL OR size(e.subtipos_relacionados) = 0 OR $subtipo IN e.subtipos_relacionados)
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
        """, {"palabras": palabras, "subtipo": subtipo, "n_results": n_results})

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
    genes = _extraer_genes_mencionados(texto_consulta)
    if not palabras and not genes:
        return contextos

    try:
        # Mismo criterio que buscar_evidencia_civic: el gen se matchea por
        # igualdad exacta, nunca CONTAINS -- ver _extraer_genes_mencionados.
        res = g.query("""
            UNWIND (CASE WHEN size($palabras) = 0 THEN [null] ELSE $palabras END) AS kw
            MATCH (v:VarianteClinVar)
            OPTIONAL MATCH (v)-[:ASOCIADA_A_CONDICION]->(c:CondicionClinVar)
            WHERE v.gen IN $genes
               OR (kw IS NOT NULL AND (
                    toLower(coalesce(v.nombre, '')) CONTAINS kw
                 OR toLower(coalesce(c.nombre, '')) CONTAINS kw
               ))
            WITH DISTINCT v, collect(DISTINCT c.nombre) AS condiciones
            RETURN
                v.gen AS gen,
                v.hgvs_c AS hgvs_c,
                v.hgvs_p AS hgvs_p,
                v.clasificacion_clinica AS clasificacion,
                v.review_status AS review_status,
                condiciones
            LIMIT $n_results
        """, {"palabras": palabras, "genes": genes, "n_results": n_results})

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
    genes = _extraer_genes_mencionados(texto_consulta)
    if not palabras and not genes:
        return contextos

    try:
        # Mismo criterio que buscar_evidencia_civic -- el gen se matchea por
        # igualdad exacta contra $genes, nunca CONTAINS. Este era exactamente
        # el punto donde "PAAF" (punción-aspiración) colisionaba con el gen
        # real PAAF1 por coincidencia de subcadena.
        res = g.query("""
            UNWIND (CASE WHEN size($palabras) = 0 THEN [null] ELSE $palabras END) AS kw
            MATCH (est:EstudioCBio)-[:REPORTA_FRECUENCIA]->(f:FrecuenciaGenCBio)-[:SOBRE_GEN]->(g:GenCBio)
            WHERE g.hugo_symbol IN $genes
               OR (kw IS NOT NULL AND toLower(coalesce(est.nombre, '')) CONTAINS kw)
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
        """, {"palabras": palabras, "genes": genes, "n_results": n_results})

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
    8. Registros reales de pacientes de mama del subgrafo RegistroTumor (Hospital Central - Mendoza).
    9. Protocolos de tratamiento estándar de mama del subgrafo ProtocoloTratamiento.
    10. Actualizaciones recientes de guías/protocolos (ASCO/ESMO) del subgrafo ActualizacionProtocolo.
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

    # 8. Búsqueda de Registros Reales de Pacientes de Mama (subgrafo RegistroTumor)
    try:
        registros = buscar_registros_tumores_mama(query, n_results=2)
        if registros:
            contextos.extend(registros)
            logger.info(f"[Híbrida] {len(registros)} registro(s) real(es) de tumor recuperado(s).")
    except Exception as e:
        logger.error(f"Error en búsqueda de registros de tumores: {e}")

    # 9. Búsqueda de Protocolos de Tratamiento Estándar (subgrafo ProtocoloTratamiento)
    try:
        protocolos = buscar_protocolos_tratamiento_mama(query, n_results=2)
        if protocolos:
            contextos.extend(protocolos)
            logger.info(f"[Híbrida] {len(protocolos)} protocolo(s) de tratamiento recuperado(s).")
    except Exception as e:
        logger.error(f"Error en búsqueda de protocolos de tratamiento: {e}")

    # 10. Búsqueda de Actualizaciones de Protocolo (subgrafo ActualizacionProtocolo)
    try:
        actualizaciones = buscar_actualizaciones_protocolo(query, n_results=2)
        if actualizaciones:
            contextos.extend(actualizaciones)
            logger.info(f"[Híbrida] {len(actualizaciones)} actualización(es) de protocolo recuperada(s).")
    except Exception as e:
        logger.error(f"Error en búsqueda de actualizaciones de protocolo: {e}")

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
