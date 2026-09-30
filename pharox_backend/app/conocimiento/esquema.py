"""
Esquema del grafo: constraints e índices, siembra inicial del subgrafo clínico
sintético, y la descripción textual del esquema que usa el explorador
text-to-Cypher (lenguaje/text_to_cypher.py).

Nota de independencia: los subgrafos públicos (CIViC, EnsayoClinico,
VarianteClinVar, EstudioCBio), los reales de mama (RegistroTumor,
ProtocoloTratamiento, ActualizacionProtocolo) y el clínico sintético
(Paciente → Tumor/CasoClinico) no tienen relaciones directas entre sí. El
cruce lo hace el motor de reglas en tiempo de consulta, por subtipo molecular,
escenario, genes y drogas (conocimiento/recuperacion.py). Única excepción:
(:RegistroTumor)-[:HABILITA_TRIAL|CONDICIONA_TRIAL|EXCLUYE_TRIAL]->(:EnsayoClinico),
elegibilidad persistida por app/gold/reglas_elegibilidad_trials.py.
"""
from __future__ import annotations

import json
import os

from app.conocimiento.grafo import Lector, sesion_escritura
from app.logging_config import get_logger

logger = get_logger("pharox.conocimiento.esquema")


def _crear_constraints_e_indices(tx) -> None:
    tx.run("CREATE CONSTRAINT unique_patient_hash IF NOT EXISTS FOR (p:Paciente) REQUIRE p.hash IS UNIQUE")
    tx.run("CREATE CONSTRAINT unique_tumor_type IF NOT EXISTS FOR (t:Tumor) REQUIRE t.tipo_cancer IS UNIQUE")
    tx.run("CREATE CONSTRAINT unique_caso_id IF NOT EXISTS FOR (c:CasoClinico) REQUIRE c.id IS UNIQUE")
    tx.run("CREATE CONSTRAINT unique_hallazgo_id IF NOT EXISTS FOR (h:HallazgoPatologico) REQUIRE h.id IS UNIQUE")
    tx.run("CREATE CONSTRAINT unique_evento_id IF NOT EXISTS FOR (e:EventoPostOperatorio) REQUIRE e.id IS UNIQUE")
    # Los gold scripts que hacen MERGE repetible (europepmc_to_neo4j.py) la necesitan explícita.
    tx.run("CREATE CONSTRAINT unique_literatura_id IF NOT EXISTS FOR (l:Literatura) REQUIRE l.id IS UNIQUE")
    # 768 dimensiones: nomic-embed-text.
    for indice, label in (("literature_vectors", "Literatura"), ("caso_clinico_vectors", "CasoClinico")):
        tx.run(f"""
            CREATE VECTOR INDEX {indice} IF NOT EXISTS
            FOR (n:{label}) ON (n.embedding)
            OPTIONS {{indexConfig: {{
                `vector.dimensions`: 768,
                `vector.similarity_function`: 'cosine'
            }}}}
        """)


def buscar_archivo(nombre_archivo: str, subdirectorios: list[str] | None = None) -> str | None:
    """Busca un archivo de datos en las ubicaciones posibles (local, Docker, tests)."""
    bases = ["", "data", os.path.join("..", "data"), os.path.join("pharox_backend", "data"), os.path.join("app", "data"), "/app/data"]
    candidatos = [os.path.join(b, nombre_archivo) for b in bases]
    for sub in subdirectorios or []:
        candidatos += [os.path.join(b, sub, nombre_archivo) for b in bases]
    for ruta in candidatos:
        if os.path.isfile(ruta):
            return ruta
    return None


def _sembrar_subgrafo_clinico_sintetico(session) -> None:
    """
    Carga el dataset FHIR sintético anonimizado (capa gold de app/pipeline_etl.py)
    como Paciente → Tumor → Tratamiento, solo si el grafo todavía no tiene pacientes.
    Es sintético: el copiloto no lo usa como evidencia clínica.
    """
    cantidad = session.run("MATCH (p:Paciente) RETURN count(p) AS cnt").single()["cnt"]
    if cantidad:
        logger.info(f"Omitiendo siembra del subgrafo clínico sintético: ya hay {cantidad} pacientes.")
        return

    ruta = buscar_archivo("dataset_estructurado_seguro.json", ["gold"])
    if not ruta:
        logger.warning("No se encontró dataset_estructurado_seguro.json: subgrafo clínico sintético sin sembrar.")
        return

    with open(ruta, encoding="utf-8") as f:
        registros = json.load(f)

    for idx, item in enumerate(registros):
        clinicos = item.get("datos_clinicos", {})
        demograficos = item.get("datos_demograficos", {})
        session.run("""
            MERGE (p:Paciente {hash: $hash})
            ON CREATE SET p.edad = $edad, p.anio_nacimiento = $anio_nac
            MERGE (t:Tumor {tipo_cancer: $tipo_cancer})
            ON CREATE SET t.cie10 = $cie10, t.descripcion = $descripcion
            MERGE (tr:Tratamiento {droga: $droga})
            MERGE (p)-[:DIAGNOSTICADO_CON]->(t)
            MERGE (t)-[:TRATADO_CON]->(tr)
        """, {
            "hash": item.get("patient_hash_sha256", f"anon_{idx}"),
            "edad": demograficos.get("edad_estimada"),
            "anio_nac": demograficos.get("anio_nacimiento"),
            "tipo_cancer": clinicos.get("tipo_cancer", "No Especificado"),
            "cie10": clinicos.get("cie10_diagnostico", "Desconocido"),
            "descripcion": clinicos.get("diagnostico_descripcion", "Evaluación registrada"),
            "droga": item.get("datos_tratamiento", {}).get("droga_prescripta", "Esquema sistémico"),
        })
    logger.info(f"Subgrafo clínico sintético sembrado desde {ruta} ({len(registros)} registros).")


def inicializar_db() -> None:
    """Crea constraints e índices y siembra el subgrafo sintético. Idempotente."""
    with sesion_escritura() as session:
        session.execute_write(_crear_constraints_e_indices)
        _sembrar_subgrafo_clinico_sintetico(session)
    # :Literatura NO se siembra acá: la única fuente es app/gold/europepmc_to_neo4j.py
    # (PMIDs reales). Si un Neo4j viejo tiene la semilla sintética, limpiarla con:
    #   MATCH (n:Literatura) WHERE n.id STARTS WITH 'lit_' DETACH DELETE n


# ---------------------------------------------------------------------------
# Descripción del esquema para el explorador text-to-Cypher
# ---------------------------------------------------------------------------
PROPIEDADES_POR_LABEL = {
    "Paciente": "hash (String), edad (Integer), anio_nacimiento (Integer)",
    "Tumor": "tipo_cancer (String), cie10 (String), descripcion (String)",
    "Tratamiento": "droga (String)",
    "Literatura": "id (String, PMID), text (String), tipo_cancer (String), categoria (String), drogas (String)",
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

RELACIONES_CONOCIDAS = [
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

NOTA_INDEPENDENCIA_SUBGRAFOS = (
    "\nNota importante: los subgrafos de conocimiento público (CIViC, EnsayoClinico, "
    "VarianteClinVar, EstudioCBio), los subgrafos reales de mama (RegistroTumor, "
    "ProtocoloTratamiento, ActualizacionProtocolo) y el subgrafo clínico sintético de "
    "pacientes (Paciente -> Tumor/CasoClinico) son independientes ENTRE SÍ; no existen "
    "relaciones directas entre ellos (con la única excepción marcada más abajo). Cruzá "
    "por coincidencia EXACTA de subtipo molecular (o CONTAINS si la propiedad es una "
    "lista/string separado por comas), nunca por texto libre.\n"
    "ÚNICA EXCEPCIÓN: (:RegistroTumor)-[:HABILITA_TRIAL|:CONDICIONA_TRIAL|:EXCLUYE_TRIAL]->"
    "(:EnsayoClinico) SÍ es una relación directa real. El tipo de relación no se puede "
    "parametrizar en Cypher: usar WHERE type(rel) IN [...] o type(rel) para leerlo.\n"
)


def describir_esquema(lector: Lector) -> str:
    """
    Descripción del esquema filtrada a las etiquetas y relaciones que existen en
    la base (para no ofrecerle al LLM tipos todavía no ingeridos). No depende de
    APOC: usa los procedimientos nativos db.labels() y db.relationshipTypes().
    """
    try:
        labels = {r["label"] for r in lector("CALL db.labels()")}
        rels = {r["relationshipType"] for r in lector("CALL db.relationshipTypes()")}
    except Exception as e:
        logger.warning(f"No se pudo leer el esquema real, se describe completo: {e}")
        labels, rels = set(), set()

    lineas = ["Nodos y propiedades en la base de datos de grafos:"]
    lineas += [f"- {label}: {props}" for label, props in PROPIEDADES_POR_LABEL.items() if not labels or label in labels]
    lineas.append("\nRelaciones y direcciones permitidas en el grafo:")
    lineas += [f"- (:{o})-[:{r}]->(:{d})" for r, o, d in RELACIONES_CONOCIDAS if not rels or r in rels]
    return "\n".join(lineas) + "\n" + NOTA_INDEPENDENCIA_SUBGRAFOS
