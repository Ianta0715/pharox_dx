"""
etl_casos_clinicos.py
Módulo ETL para ingesta de casos clínicos reales anonimizados en Pharox DX.

Flujo:
  [Imagen] → OCR → [Texto libre]
  [Texto libre]  → LLM Extractor → [JSON estructurado] → [Neo4j]

Privacidad: No se almacena nombre, fecha exacta ni ningún dato identificatorio.
El ID del caso es un hash SHA-256 derivado del contenido clínico.
"""
import os
import json
import hashlib
import re
from typing import Optional

from app.ai_gateway import get_llm, generar_embedding
from app.logging_config import get_logger

logger = get_logger("pharox.etl_casos_clinicos")

# ---------------------------------------------------------------------------
# OCR — se importa condicionalmente para no romper si no está instalado
# ---------------------------------------------------------------------------
try:
    import pytesseract
    from PIL import Image
    import io
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False
    logger.warning("[ETL] pytesseract/Pillow no instalados. Solo se aceptará texto.")

# ---------------------------------------------------------------------------
# PROMPT DE EXTRACCIÓN ESTRUCTURADA
# ---------------------------------------------------------------------------
EXTRACTION_PROMPT = """Eres un asistente médico especializado en oncología.
Recibirás texto de un informe anatomopatológico y/o descripción clínica post-operatoria.
Extrae la información y devuelve ÚNICAMENTE un objeto JSON válido, sin bloques de código, sin explicaciones.

Esquema JSON requerido:
{{
  "datos_demograficos": {{
    "sexo": "F" o "M",
    "edad": número entero o null,
    "imc_categoria": "normal" | "sobrepeso" | "obesidad" | "bajo_peso" | "desconocido",
    "comorbilidades_relevantes": ["string"]
  }},
  "antecedentes": [
    {{"tipo": "string", "medicacion": "string o null", "relevancia": "baja" | "media" | "alta"}}
  ],
  "hallazgos_patologicos": [
    {{
      "lateralidad": "derecha" | "izquierda" | "bilateral" | "desconocida",
      "tipo_carcinoma": "string",
      "subtipo_molecular": "Luminal_A" | "Luminal_B" | "HER2_positivo" | "Triple_negativo" | "desconocido",
      "grado_nottingham": 1 | 2 | 3 | null,
      "estadificacion": "string (ej: pT1c,pNX) o null",
      "multifocal": true | false,
      "margenes_libres": true | false,
      "permeacion_vascular": true | false,
      "procedimiento": "tumorectomia" | "mastectomia_simple" | "mastectomia_radical" | "biopsia" | "otro",
      "texto_microscopico": "resumen del hallazgo microscópico"
    }}
  ],
  "eventos_postoperatorios": [
    {{
      "tipo_evento": "seroma" | "infeccion" | "dehiscencia" | "hematoma" | "dolor_cronico" | "otro",
      "descripcion": "string",
      "mama_afectada": "derecha" | "izquierda" | "bilateral" | "no_aplica",
      "resolucion": "string",
      "detalle_resolucion": "string",
      "resultado": "resolucion_completa" | "resolucion_parcial" | "sin_resolucion" | "en_seguimiento"
    }}
  ]
}}

Reglas:
- Si no hay eventos post-operatorios: "eventos_postoperatorios": []
- Si un campo no está disponible: usa null para números, "desconocido" para strings
- NUNCA uses "bilateral" si el informe habla de mama derecha y mama izquierda por separado. Crea un elemento en la lista "hallazgos_patologicos" para cada mama.
- NUNCA inventes ni asumas la lateralidad (derecha/izquierda) si el informe no la especifica explícitamente: en ese caso usa "desconocida". Lo mismo aplica a "subtipo_molecular": si no está explícito en el texto, usa "desconocido" en vez de adivinar.
- Extrae la EDAD y el SEXO del paciente, no del médico que firma. "femenina" o "mujer" es sexo "F".
- Solo JSON, sin markdown, sin texto adicional.

EJEMPLO DE ENTRADA:
"Paciente de 60 años, F, hipertensa. Mama derecha: carcinoma lobular Luminal A Nottingham 2, pT1c, pNX, tumorectomia. Mama izquierda: sin hallazgos. Post-op: dehiscencia leve de herida resuelta con curaciones diarias."

EJEMPLO DE RESPUESTA JSON:
{{
  "datos_demograficos": {{
    "sexo": "F",
    "edad": 60,
    "imc_categoria": "desconocido",
    "comorbilidades_relevantes": ["hipertension"]
  }},
  "antecedentes": [
    {{"tipo": "hipertension", "medicacion": "desconocido", "relevancia": "media"}}
  ],
  "hallazgos_patologicos": [
    {{
      "lateralidad": "derecha",
      "tipo_carcinoma": "lobular",
      "subtipo_molecular": "Luminal_A",
      "grado_nottingham": 2,
      "estadificacion": "pT1c,pNX",
      "multifocal": false,
      "margenes_libres": true,
      "permeacion_vascular": false,
      "procedimiento": "tumorectomia",
      "texto_microscopico": "Carcinoma lobular invasor Luminal A"
    }}
  ],
  "eventos_postoperatorios": [
    {{
      "tipo_evento": "dehiscencia",
      "descripcion": "dehiscencia leve de herida",
      "mama_afectada": "derecha",
      "resolucion": "curaciones diarias",
      "detalle_resolucion": "curaciones",
      "resultado": "resolucion_completa"
    }}
  ]
}}

INFORME A PROCESAR:
{texto}

JSON:"""


# ---------------------------------------------------------------------------
# OCR: Imagen → Texto
# ---------------------------------------------------------------------------
def imagen_a_texto(image_bytes: bytes) -> str:
    """Convierte una imagen de un informe médico a texto usando OCR (Tesseract)."""
    if not OCR_AVAILABLE:
        raise RuntimeError(
            "pytesseract y Pillow no están instalados. "
            "Instálalos con: pip install pytesseract Pillow"
        )
    try:
        image = Image.open(io.BytesIO(image_bytes))
        # Intentar primero con español, luego con inglés como respaldo
        try:
            texto = pytesseract.image_to_string(image, lang="spa")
        except Exception:
            texto = pytesseract.image_to_string(image, lang="eng")

        texto = texto.strip()
        if not texto:
            raise ValueError("El OCR no pudo extraer texto de la imagen.")
        logger.info(f"[OCR] Texto extraído ({len(texto)} caracteres).")
        return texto
    except Exception as e:
        raise RuntimeError(f"Error en OCR: {e}")


# ---------------------------------------------------------------------------
# EXTRACCIÓN ESTRUCTURADA: Texto libre → JSON estructurado via LLM
# ---------------------------------------------------------------------------
def extraer_estructura_con_llm(texto: str) -> dict:
    """
    Usa el LLM para extraer información estructurada del texto libre del informe.
    Devuelve un diccionario con el esquema del caso clínico.
    """
    llm = get_llm(task="extraccion", data_sensitive=True, temperature=0.0)
    prompt_final = EXTRACTION_PROMPT.format(texto=texto)

    logger.info("[ETL] Extrayendo estructura del informe con LLM...")
    try:
        respuesta = llm.invoke(prompt_final)
        contenido = respuesta.content if hasattr(respuesta, "content") else str(respuesta)

        # Limpiar posibles bloques de código markdown
        contenido = re.sub(r"```json\s*", "", contenido)
        contenido = re.sub(r"```\s*", "", contenido)
        contenido = contenido.strip()

        # Extraer solo la parte JSON si hay texto extra
        match = re.search(r'\{.*\}', contenido, re.DOTALL)
        if match:
            contenido = match.group(0)

        caso = json.loads(contenido)
        logger.info("[ETL] Estructura extraída correctamente.")
        return caso
    except json.JSONDecodeError as e:
        logger.error(f"[ETL] Error al parsear JSON del LLM: {e}")
        logger.info(f"[ETL] Respuesta recibida: {contenido[:500]}")
        raise ValueError(f"El LLM no generó un JSON válido: {e}")


def _s(val, default: str = "") -> str:
    """Convierte None o cualquier valor no-string a string de forma segura."""
    if val is None:
        return default
    return str(val)


# ---------------------------------------------------------------------------
# ANONIMIZACIÓN: Generar ID de caso único sin datos personales
# ---------------------------------------------------------------------------
def generar_id_caso(caso: dict) -> str:
    """
    Genera un hash SHA-256 anonimizado basado en los datos clínicos
    (sin nombre, sin fecha exacta ni dato identificatorio).
    """
    demo = caso.get("datos_demograficos", {})
    hallazgos = caso.get("hallazgos_patologicos", [])

    # Usamos _s() para garantizar que valores null del LLM no rompan la concatenación
    contenido_hash = (
        f"{_s(demo.get('sexo'), '?')}_"
        f"{_s(demo.get('edad'), '?')}_"
        f"{'_'.join([_s(h.get('subtipo_molecular')) + _s(h.get('estadificacion')) for h in hallazgos])}"
    )
    return hashlib.sha256(contenido_hash.encode()).hexdigest()[:24]


# ---------------------------------------------------------------------------
# RESUMEN CLÍNICO: Texto de embedding para búsqueda por similitud
# ---------------------------------------------------------------------------
def generar_resumen_clinico(caso: dict) -> str:
    """
    Genera un texto clínico resumido y semánticamente rico
    para ser usado como embedding de búsqueda de similitud.
    Todos los valores se pasan por _s() para garantizar seguridad ante null del LLM.
    """
    partes = []
    demo = caso.get("datos_demograficos", {})
    partes.append(
        f"Paciente {_s(demo.get('sexo'), '?')}, {_s(demo.get('edad'), '?')} años, "
        f"IMC: {_s(demo.get('imc_categoria'), 'desconocido')}"
    )

    comorbilidades = demo.get("comorbilidades_relevantes") or []
    if comorbilidades:
        partes.append(f"Comorbilidades: {', '.join([_s(c) for c in comorbilidades])}")

    antecedentes = caso.get("antecedentes") or []
    for a in antecedentes:
        med = f" (medicación: {_s(a.get('medicacion'))})" if a.get("medicacion") else ""
        partes.append(f"Antecedente: {_s(a.get('tipo'), '?')}{med}")

    for h in caso.get("hallazgos_patologicos") or []:
        partes.append(
            f"Mama {_s(h.get('lateralidad'), '?')}: {_s(h.get('subtipo_molecular'), '?')}, "
            f"carcinoma {_s(h.get('tipo_carcinoma'), '?')}, "
            f"Nottingham {_s(h.get('grado_nottingham'), '?')}, "
            f"estadio {_s(h.get('estadificacion'), '?')}, "
            f"multifocal: {_s(h.get('multifocal'), 'no especificado')}, "
            f"procedimiento: {_s(h.get('procedimiento'), '?')}. "
            f"{_s(h.get('texto_microscopico'), '')}"
        )

    for e in caso.get("eventos_postoperatorios") or []:
        partes.append(
            f"Complicación post-operatoria: {_s(e.get('tipo_evento'), '?')} "
            f"en mama {_s(e.get('mama_afectada'), '?')}. "
            f"Resolución: {_s(e.get('detalle_resolucion'), '?')}. "
            f"Resultado: {_s(e.get('resultado'), '?')}"
        )

    return " | ".join(partes)


# ---------------------------------------------------------------------------
# INGESTA EN NEO4J
# ---------------------------------------------------------------------------
def ingestar_caso_en_neo4j(caso: dict, caso_id: str, resumen: str, embedding: list) -> dict:
    """
    Persiste el caso clínico estructurado en el grafo de Neo4j.
    Crea los nodos CasoClinico, HallazgoPatologico y EventoPostOperatorio
    y los vincula al nodo Paciente/Tumor correspondiente.
    """
    from app.graph_db import get_graph, driver

    demo = caso.get("datos_demograficos", {})
    edad = demo.get("edad")
    sexo = demo.get("sexo", "desconocido")
    imc = demo.get("imc_categoria", "desconocido")

    logger.info(f"[ETL] Ingesta de caso {caso_id} en Neo4j...")

    with driver.session() as session:
        # 1. Crear nodo Paciente anónimo (si no existe)
        session.run("""
            MERGE (p:Paciente {hash: $hash})
            ON CREATE SET p.edad = $edad, p.sexo = $sexo, p.imc_categoria = $imc
        """, {"hash": caso_id, "edad": edad, "sexo": sexo, "imc": imc})

        # 2. Crear nodo CasoClinico con embedding para búsqueda vectorial
        session.run("""
            MERGE (c:CasoClinico {id: $id})
            ON CREATE SET
                c.resumen_clinico = $resumen,
                c.embedding = $embedding,
                c.tipo_caso = $tipo_caso
            MERGE (p:Paciente {hash: $id})
            MERGE (p)-[:CORRESPONDE_A]->(c)
        """, {
            "id": caso_id,
            "resumen": resumen,
            "embedding": embedding,
            "tipo_caso": "oncologia_mama"
        })

        # 3. Crear antecedentes
        for ant in caso.get("antecedentes", []):
            session.run("""
                MERGE (a:AntecedenteMedico {tipo: $tipo, medicacion: $medicacion})
                WITH a
                MATCH (p:Paciente {hash: $hash})
                MERGE (p)-[:TIENE_ANTECEDENTE]->(a)
            """, {
                "tipo": ant.get("tipo", "desconocido"),
                "medicacion": ant.get("medicacion") or "ninguna",
                "hash": caso_id
            })

        # 4. Crear hallazgos patológicos y vincular al Tumor existente (si aplica)
        for idx, h in enumerate(caso.get("hallazgos_patologicos", [])):
            hallazgo_id = f"{caso_id}_hallazgo_{idx}"
            tipo_carcinoma = h.get("tipo_carcinoma", "carcinoma")
            subtipo = h.get("subtipo_molecular", "desconocido")

            session.run("""
                MERGE (hp:HallazgoPatologico {id: $id})
                ON CREATE SET
                    hp.lateralidad = $lateralidad,
                    hp.tipo_carcinoma = $tipo_carcinoma,
                    hp.subtipo_molecular = $subtipo_molecular,
                    hp.grado_nottingham = $grado,
                    hp.estadificacion = $estadificacion,
                    hp.multifocal = $multifocal,
                    hp.margenes_libres = $margenes,
                    hp.permeacion_vascular = $permeacion,
                    hp.procedimiento = $procedimiento,
                    hp.texto_microscopico = $texto
                WITH hp
                MATCH (c:CasoClinico {id: $caso_id})
                MERGE (c)-[:INCLUYE_HALLAZGO]->(hp)
            """, {
                "id": hallazgo_id,
                "lateralidad": h.get("lateralidad", "desconocida"),
                "tipo_carcinoma": tipo_carcinoma,
                "subtipo_molecular": subtipo,
                "grado": h.get("grado_nottingham"),
                "estadificacion": h.get("estadificacion", "desconocido"),
                "multifocal": h.get("multifocal", False),
                "margenes": h.get("margenes_libres", True),
                "permeacion": h.get("permeacion_vascular", False),
                "procedimiento": h.get("procedimiento", "otro"),
                "texto": h.get("texto_microscopico", ""),
                "caso_id": caso_id
            })

            # Vincular al nodo Tumor si el tipo de cáncer ya existe en el grafo
            session.run("""
                MATCH (t:Tumor)
                WHERE toLower(t.tipo_cancer) CONTAINS 'mama'
                   OR toLower(t.descripcion) CONTAINS 'mama'
                WITH t LIMIT 1
                MATCH (hp:HallazgoPatologico {id: $hp_id})
                MERGE (hp)-[:ASOCIADO_A_TUMOR]->(t)
            """, {"hp_id": hallazgo_id})

        # 5. Crear eventos post-operatorios
        for idx, e in enumerate(caso.get("eventos_postoperatorios", [])):
            evento_id = f"{caso_id}_evento_{idx}"
            session.run("""
                MERGE (ev:EventoPostOperatorio {id: $id})
                ON CREATE SET
                    ev.tipo_evento = $tipo,
                    ev.descripcion = $descripcion,
                    ev.mama_afectada = $mama,
                    ev.resolucion = $resolucion,
                    ev.detalle_resolucion = $detalle,
                    ev.resultado = $resultado
                WITH ev
                MATCH (c:CasoClinico {id: $caso_id})
                MERGE (c)-[:TUVO_EVENTO_POSTOP]->(ev)
            """, {
                "id": evento_id,
                "tipo": e.get("tipo_evento", "otro"),
                "descripcion": e.get("descripcion", ""),
                "mama": e.get("mama_afectada", "no_aplica"),
                "resolucion": e.get("resolucion", ""),
                "detalle": e.get("detalle_resolucion", ""),
                "resultado": e.get("resultado", "en_seguimiento"),
                "caso_id": caso_id
            })

    logger.info(f"[ETL] Caso {caso_id} ingesta completada en Neo4j.")
    return {"caso_id": caso_id, "nodos_creados": True}


# ---------------------------------------------------------------------------
# FUNCIÓN PRINCIPAL: Orquesta todo el pipeline
# ---------------------------------------------------------------------------
def procesar_informe(texto: Optional[str] = None, image_bytes: Optional[bytes] = None) -> dict:
    """
    Punto de entrada principal del ETL de casos clínicos.

    Acepta texto libre O bytes de imagen (JPG/PNG del informe).
    Devuelve el caso ingresado con su ID y resumen.
    """
    if not texto and not image_bytes:
        raise ValueError("Se requiere texto o imagen del informe médico.")

    # Paso 1: OCR si es imagen
    if image_bytes:
        logger.info("[ETL] Procesando imagen con OCR...")
        texto_ocr = imagen_a_texto(image_bytes)
        # Combinar con texto adicional si el médico también aportó descripción
        texto_final = f"{texto_ocr}\n\n{texto}" if texto else texto_ocr
    else:
        texto_final = texto

    # Paso 2: Extracción estructurada con LLM
    caso_estructurado = extraer_estructura_con_llm(texto_final)

    # Paso 3: Anonimización e ID único
    caso_id = generar_id_caso(caso_estructurado)

    # Paso 4: Generar resumen clínico para embedding
    resumen = generar_resumen_clinico(caso_estructurado)

    # Paso 5: Generar embedding vectorial
    logger.info("[ETL] Generando embedding vectorial del caso...")
    embedding = generar_embedding(resumen)

    # Paso 6: Ingestar en Neo4j
    resultado = ingestar_caso_en_neo4j(caso_estructurado, caso_id, resumen, embedding)

    return {
        "success": True,
        "caso_id": caso_id,
        "resumen_clinico": resumen,
        "hallazgos_extraidos": len(caso_estructurado.get("hallazgos_patologicos", [])),
        "eventos_postop_extraidos": len(caso_estructurado.get("eventos_postoperatorios", [])),
        "estructura_completa": caso_estructurado
    }
