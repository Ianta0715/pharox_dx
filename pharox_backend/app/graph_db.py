import os
import json
from neo4j import GraphDatabase
from app.ai_gateway import generar_embedding

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "pharoxpass")

# Inicializar driver
driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

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
    # 1. Crear restricciones de unicidad
    tx.run("CREATE CONSTRAINT unique_patient_hash IF NOT EXISTS FOR (p:Paciente) REQUIRE p.hash IS UNIQUE")
    tx.run("CREATE CONSTRAINT unique_tumor_type IF NOT EXISTS FOR (t:Tumor) REQUIRE t.tipo_cancer IS UNIQUE")
    
    # 2. Crear índice vectorial para el campo embedding en nodos :Literatura (dimensión 768 para nomic-embed-text)
    tx.run("""
        CREATE VECTOR INDEX literature_vectors IF NOT EXISTS
        FOR (n:Literatura) ON (n.embedding)
        OPTIONS {indexConfig: {
            `vector.dimensions`: 768,
            `vector.similarity_function`: 'cosine'
        }}
    """)

def inicializar_db():
    """Inicializa el esquema e ingesta la literatura y los historiales en Neo4j."""
    print("Inicializando base de datos de grafos Neo4j...")
    
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
        print(f"Cargando literatura en Neo4j desde {path_lit}...")
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
                    
                    print(f"   Generating embedding for lit_{idx}...")
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
            print("Literatura ingesta en Neo4j de forma correcta.")
        except Exception as e:
            print(f"Error al ingestar literatura en Neo4j: {e}")
    else:
        print(f"Omitiendo ingesta de literatura. Ya contiene {lit_count} registros.")

    # Ingestar historial clínico (Capa Gold) si está vacío
    if paciente_count == 0 and path_gold:
        print(f"Cargando historial clínico en Neo4j desde {path_gold}...")
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
                        
                        // EL NUEVO DISEÑO SEMÁNTICO (Paciente -> Tumor -> Tratamiento)
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
            print("Historial clínico (Capa Gold) mapeado en Neo4j con éxito.")
        except Exception as e:
            print(f"Error al mapear Capa Gold en Neo4j: {e}")
    else:
        print(f"Omitiendo ingesta de historial clínico. Ya contiene {paciente_count} pacientes.")

def buscar_contexto_hibrido(query: str, tipo_cancer: str = None, n_results: int = 3) -> list[str]:
    """
    Realiza una búsqueda híbrida (Graph RAG):
    1. Búsqueda por Similitud de Vectores en el índice de Literatura.
    2. Recorrido de Relaciones en el Grafo Clínico de Pacientes reales.
    """
    contextos = []
    
    # 1. Búsqueda Vectorial
    try:
        query_vector = generar_embedding(query)
        with driver.session() as session:
            # Consulta vectorial en Neo4j
            res = session.run("""
                CALL db.index.vector.queryNodes('literature_vectors', $n_results, $query_vector)
                YIELD node, score
                RETURN node.text AS text, node.tipo_cancer AS tipo_cancer, score
            """, {"n_results": n_results * 2, "query_vector": query_vector}) # Traemos extra para filtrar en memoria si es necesario
            
            vec_count = 0
            for record in res:
                node_cancer = record.get("tipo_cancer")
                node_text = record.get("text")
                
                # Filtrar por tipo de cáncer si es necesario
                if tipo_cancer and tipo_cancer != "No Especificado" and node_cancer != tipo_cancer:
                    continue
                    
                contextos.append(node_text)
                vec_count += 1
                if vec_count >= n_results:
                    break
    except Exception as e:
        print(f"Error en búsqueda vectorial Neo4j: {e}")

    # 2. Búsqueda de Relaciones en el Grafo Clínico (Graph Traversing)
    if tipo_cancer and tipo_cancer != "No Especificado":
        try:
            with driver.session() as session:
                res_grafo = session.run("""
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
            print(f"Error en consulta de relaciones Neo4j: {e}")
            
    return contextos

def obtener_estado_grafo():
    """Retorna métricas básicas de los nodos y relaciones en el grafo para depuración."""
    try:
        with driver.session() as session:
            res_nodos = session.run("""
                MATCH (n) 
                RETURN labels(n)[0] AS label, count(n) AS cantidad
            """)
            nodos = {record["label"] or "Sin Etiqueta": record["cantidad"] for record in res_nodos}
            
            res_rels = session.run("""
                MATCH ()-[r]->() 
                RETURN type(r) AS tipo, count(r) AS cantidad
            """)
            relaciones = {record["tipo"]: record["cantidad"] for record in res_rels}
            
            # Obtener una lista de nodos de literatura para simular la visualización de datos
            res_lit = session.run("MATCH (l:Literatura) RETURN l.id AS id, l.tipo_cancer AS cancer, l.drogas AS drogas LIMIT 20")
            detalles_lit = [{"id": r["id"], "tipo_cancer": r["cancer"], "drogas": r["drogas"]} for r in res_lit]
            
            return {
                "nodos": nodos,
                "relaciones": relaciones,
                "detalles_literatura": detalles_lit
            }
    except Exception as e:
        print(f"Error al obtener estado del grafo: {e}")
        return {"error": str(e)}

def ejecutar_cypher_dinamico(query_cypher: str) -> str:
    """Ejecuta una consulta Cypher generada por la IA y devuelve el resultado."""
    try:
        # Mini limpieza por si la IA devuelve markdown (```cypher ... ```)
        query_limpia = query_cypher.replace("```cypher", "").replace("```", "").strip()
        
        # Filtro de seguridad (solo permitimos leer, no borrar ni escribir)
        if "DELETE" in query_limpia.upper() or "MERGE" in query_limpia.upper() or "CREATE" in query_limpia.upper():
            return "Operación no permitida por seguridad. Solo se permite MATCH."
            
        with driver.session() as session:
            res = session.run(query_limpia)
            resultados = [str(record.data()) for record in res]
            if resultados:
                return "\n".join(resultados)
            return ""
    except Exception as e:
        print(f"Error ejecutando Cypher dinámico: {e}")
        return ""
