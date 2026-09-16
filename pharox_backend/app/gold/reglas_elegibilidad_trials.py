"""
reglas_elegibilidad_trials.py
Motor de elegibilidad a ensayos clinicos -- Pharox_Documento_v5 §11 "Capa 1 -
Matching de trials activos" (paso determinístico + paso semántico).

A DIFERENCIA de todo el resto de los subgrafos del proyecto (CIViC, ClinVar,
cBioPortal, ProtocoloTratamiento, ActualizacionProtocolo -- ver la nota de
independencia de subgrafos en app/graph_db.py), este script SÍ crea
relaciones explícitas y persistentes entre dos subgrafos que hasta ahora
estaban desconectados: (:RegistroTumor)-[:HABILITA_TRIAL|CONDICIONA_TRIAL|
EXCLUYE_TRIAL]->(:EnsayoClinico). Es la excepción deliberada: para preguntas
exploratorias ("¿qué dice la evidencia sobre X?") el matching en tiempo de
consulta es lo correcto (rápido de construir, no fuerza un esquema sobre
fuentes heterogéneas). Pero "¿María es elegible para el trial Y?" es estado
de alto valor que un médico va a volver a consultar, necesita ser instantáneo,
y necesita quedar auditado (qué regla lo decidió, cuándo, por qué) -- por
eso, y solo para esto, se persiste como arista real en vez de recalcularse
desde cero en cada pregunta. Ver conversación del equipo sobre este tradeoff.

DOS PASOS, igual que describe el documento:

Paso 1 (determinístico, `evaluar_criterios_deterministicos`): compara SOLO
campos estructurados (estado de reclutamiento, sexo, rango etario, subtipo
molecular) -- nunca Neo4j, nunca LLM, 100% reproducible. NUNCA emite
HABILITA: con los datos estructurados disponibles (ver RegistroTumor / el
excel real del Hospital Central - Mendoza) no alcanza para confirmar
elegibilidad completa, solo para descartarla con confianza. El resultado es
siempre EXCLUYE (encontró un descarte objetivo) o CONDICIONA (ningún
descarte objetivo, pero quedan criterios en texto libre sin verificar).

Paso 2 (semántico, `evaluar_criterio_semantico`, opcional): SOLO se corre
sobre los pares que el paso 1 dejó en CONDICIONA -- no tiene sentido gastar
una llamada al LLM en algo que ya se descartó determinísticamente. Lee el
texto libre completo de criterios_elegibilidad (ClinicalTrials.gov) contra
el perfil de la paciente y puede confirmar HABILITA, mantener CONDICIONA
(nombrando qué falta verificar) o bajar a EXCLUYE. Requiere Ollama
corriendo -- no se pudo verificar end-to-end en esta máquina (sin GPU);
confirmar en la máquina con GPU antes de correrlo con --con-llm.

El veredicto final (el que se persiste) SIEMPRE queda acotado a las 3
relaciones del documento -- nunca se escribe una relación cuyo tipo venga de
un valor no controlado (ver _QUERIES_POR_VEREDICTO): el output del LLM se
valida contra una lista cerrada antes de tocar Cypher.

Uso:
    python -m app.gold.reglas_elegibilidad_trials                     # deterministico, todos los pacientes/ensayos
    python -m app.gold.reglas_elegibilidad_trials 10 20                # limita a 10 pacientes x 20 ensayos (pruebas rapidas)
    python -m app.gold.reglas_elegibilidad_trials 10 20 --con-llm      # + paso semantico (requiere Ollama)
"""
import os
import re
import sys
import json
from datetime import datetime, timezone

from neo4j import GraphDatabase
from dotenv import load_dotenv

from app.ai_gateway import get_llm

load_dotenv()

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "pharoxpass")

driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

_MAPA_SEXO_PACIENTE = {"Mujer": "FEMALE", "Hombre": "MALE"}
_VEREDICTOS_VALIDOS = {"HABILITA", "CONDICIONA", "EXCLUYE"}


def _veredicto(veredicto: str, motivo: str, regla: str, criterio_pendiente: str | None = None) -> dict:
    return {"veredicto": veredicto, "motivo": motivo, "regla": regla, "criterio_pendiente": criterio_pendiente}


# ---------------------------------------------------------------------------
# PASO 1 — DETERMINÍSTICO (pura: sin red, sin Neo4j, sin LLM)
# ---------------------------------------------------------------------------
def evaluar_criterios_deterministicos(paciente: dict, ensayo: dict) -> dict:
    """
    Evalúa criterios OBJETIVOS y estructurados. Recibe los dicts tal como los
    devuelven graph_db.obtener_pacientes_mama_activos / obtener_ensayos_activos
    (mismas claves que las propiedades de :RegistroTumor / :EnsayoClinico).
    """
    if ensayo.get("estado") != "RECRUITING":
        return _veredicto(
            "EXCLUYE",
            f"El ensayo no está reclutando actualmente (estado: {ensayo.get('estado') or 'desconocido'}).",
            regla="estado_reclutamiento",
        )

    sexo_paciente = _MAPA_SEXO_PACIENTE.get((paciente.get("sexo") or "").strip())
    sexo_ensayo = (ensayo.get("sexo") or "ALL").upper()
    if sexo_paciente and sexo_ensayo != "ALL" and sexo_paciente != sexo_ensayo:
        return _veredicto(
            "EXCLUYE",
            f"El ensayo solo acepta sexo {sexo_ensayo.lower()} (paciente: {sexo_paciente.lower()}).",
            regla="sexo",
        )

    edad = paciente.get("edad")
    edad_min = ensayo.get("edad_minima_anios")
    edad_max = ensayo.get("edad_maxima_anios")
    if edad is not None and edad_min is not None and edad < edad_min:
        return _veredicto("EXCLUYE", f"Edad ({edad}) por debajo del mínimo del ensayo ({edad_min} años).", regla="edad_minima")
    if edad is not None and edad_max is not None and edad > edad_max:
        return _veredicto("EXCLUYE", f"Edad ({edad}) por encima del máximo del ensayo ({edad_max} años).", regla="edad_maxima")

    subtipo_paciente = paciente.get("subtipo_molecular") or "desconocido"
    subtipos_ensayo = ensayo.get("subtipos_relacionados") or []
    if subtipo_paciente != "desconocido" and subtipos_ensayo and subtipo_paciente not in subtipos_ensayo:
        return _veredicto(
            "EXCLUYE",
            f"Perfil molecular de la paciente ({subtipo_paciente}) no coincide con el perfil del ensayo ({', '.join(subtipos_ensayo)}).",
            regla="subtipo_molecular",
        )

    return _veredicto(
        "CONDICIONA",
        "Ningún criterio estructurado (reclutamiento, sexo, edad, subtipo molecular) descarta a la paciente.",
        regla="filtros_estructurados_ok",
        criterio_pendiente="Quedan por verificar los criterios en texto libre del protocolo (líneas de tratamiento previas, biomarcadores adicionales, comorbilidades, etc.).",
    )


# ---------------------------------------------------------------------------
# PASO 2 — SEMÁNTICO (LLM, opcional, solo sobre pares CONDICIONA)
# ---------------------------------------------------------------------------
PROMPT_ELEGIBILIDAD_SEMANTICA = """Actuás como un coordinador de investigación clínica evaluando si una
paciente de cáncer de mama es elegible para un ensayo clínico, a partir de los criterios de
inclusión/exclusión REALES del protocolo (texto original de ClinicalTrials.gov).

PERFIL DE LA PACIENTE (único dato disponible -- no asumas nada que no esté acá):
- Edad: {edad}
- Sexo: {sexo}
- Subtipo molecular: {subtipo_molecular} (RE:{re} RP:{rp} HER2:{her2})
- Estadio clínico: {estadio_clinico}
- ECOG: {ecog}

CRITERIOS DE ELEGIBILIDAD DEL ENSAYO {nct_id} (texto original del protocolo):
{criterios_elegibilidad}

YA SE VERIFICÓ Y NO HAY QUE REPETIRLO: el ensayo está reclutando, el sexo y el rango etario de
la paciente son compatibles con el protocolo, y el subtipo molecular no está explícitamente
excluido.

Tu tarea es evaluar SOLO lo que el perfil de arriba permite verificar contra el texto del
protocolo. Reglas estrictas:
- NUNCA asumas un dato que no esté en el perfil (ej. líneas de tratamiento previas, biomarcadores
  no mencionados, comorbilidades). Si un criterio del protocolo lo requiere y no está en el
  perfil, es un criterio PENDIENTE -- ni lo des por cumplido ni lo uses para excluir.
- Devolvé ÚNICAMENTE un objeto JSON, sin texto adicional ni bloques de código, con esta forma
  exacta: {{"veredicto": "HABILITA" | "CONDICIONA" | "EXCLUYE", "motivo": "...", "criterio_pendiente": "..." o null}}
- "HABILITA": todos los criterios verificables con este perfil se cumplen y no queda ningún
  criterio evaluable pendiente.
- "CONDICIONA": no hay un criterio de exclusión claro, pero queda al menos un criterio del
  protocolo que no se puede verificar con los datos del perfil -- nombralo en "criterio_pendiente".
- "EXCLUYE": el perfil cumple explícitamente un criterio de exclusión del protocolo, o contradice
  un criterio de inclusión -- nombralo en "motivo".
"""


def _parsear_respuesta_semantica(contenido: str) -> dict:
    """Pura: parsea la respuesta JSON del LLM. Aislada de evaluar_criterio_semantico para poder testearla sin invocar un modelo."""
    limpio = re.sub(r"```json\s*", "", contenido)
    limpio = re.sub(r"```\s*", "", limpio).strip()
    match = re.search(r"\{.*\}", limpio, re.DOTALL)
    if match:
        limpio = match.group(0)

    try:
        datos = json.loads(limpio)
    except json.JSONDecodeError:
        return _veredicto(
            "CONDICIONA", "No se pudo interpretar la evaluación semántica del LLM (respuesta no era JSON válido).",
            regla="error_parseo_llm", criterio_pendiente="Revisar manualmente los criterios del ensayo.",
        )

    veredicto = str(datos.get("veredicto") or "").upper()
    if veredicto not in _VEREDICTOS_VALIDOS:
        return _veredicto(
            "CONDICIONA", f"El LLM devolvió un veredicto no reconocido: '{veredicto}'.",
            regla="veredicto_invalido_llm", criterio_pendiente="Revisar manualmente los criterios del ensayo.",
        )

    return _veredicto(
        veredicto,
        str(datos.get("motivo") or "Sin motivo provisto por el modelo."),
        regla="semantico_llm",
        criterio_pendiente=datos.get("criterio_pendiente"),
    )


def evaluar_criterio_semantico(paciente: dict, ensayo: dict) -> dict:
    """
    Solo tiene sentido llamarla sobre pares que el paso 1 dejó en CONDICIONA.
    Requiere Ollama corriendo (usa app.ai_gateway.get_llm, task="elegibilidad_trial",
    data_sensitive=True -- nunca sale a un proveedor cloud, ver ai_gateway.py).
    """
    criterios = (ensayo.get("criterios_elegibilidad") or "").strip()
    if not criterios:
        return _veredicto(
            "CONDICIONA", "El ensayo no tiene texto de criterios de elegibilidad para evaluar.",
            regla="sin_texto_elegibilidad", criterio_pendiente="Revisar los criterios directamente en ClinicalTrials.gov.",
        )

    prompt = PROMPT_ELEGIBILIDAD_SEMANTICA.format(
        edad=paciente.get("edad") or "desconocida",
        sexo=paciente.get("sexo") or "desconocido",
        subtipo_molecular=paciente.get("subtipo_molecular") or "desconocido",
        re=paciente.get("receptor_estrogeno") or "?",
        rp=paciente.get("receptor_progesterona") or "?",
        her2=paciente.get("her2") or "?",
        estadio_clinico=paciente.get("estadio_clinico") or "desconocido",
        ecog=paciente.get("ecog") or "desconocido",
        nct_id=ensayo.get("nct_id") or "?",
        criterios_elegibilidad=criterios,
    )

    llm = get_llm(task="elegibilidad_trial", data_sensitive=True, temperature=0.0)
    respuesta = llm.invoke(prompt)
    contenido = respuesta.content if hasattr(respuesta, "content") else str(respuesta)
    return _parsear_respuesta_semantica(contenido)


# ---------------------------------------------------------------------------
# PERSISTENCIA — MERGE de la relación semántica, tipo validado contra lista cerrada
# ---------------------------------------------------------------------------
_QUERIES_POR_VEREDICTO = {
    "HABILITA": """
        MATCH (p:RegistroTumor {id: $paciente_id}), (e:EnsayoClinico {nct_id: $nct_id})
        MERGE (p)-[r:HABILITA_TRIAL]->(e)
        SET r += $props
    """,
    "CONDICIONA": """
        MATCH (p:RegistroTumor {id: $paciente_id}), (e:EnsayoClinico {nct_id: $nct_id})
        MERGE (p)-[r:CONDICIONA_TRIAL]->(e)
        SET r += $props
    """,
    "EXCLUYE": """
        MATCH (p:RegistroTumor {id: $paciente_id}), (e:EnsayoClinico {nct_id: $nct_id})
        MERGE (p)-[r:EXCLUYE_TRIAL]->(e)
        SET r += $props
    """,
}

_TIPOS_RELACION = ["HABILITA_TRIAL", "CONDICIONA_TRIAL", "EXCLUYE_TRIAL"]


def _limpiar_veredictos_previos(tx, paciente_id: str, nct_id: str, tipo_nuevo: str):
    """
    Borra cualquier relación de un veredicto DISTINTO al nuevo entre el mismo
    par paciente-ensayo -- sin esto, una paciente re-evaluada podría terminar
    con EXCLUYE_TRIAL *y* CONDICIONA_TRIAL simultáneos hacia el mismo ensayo
    (MERGE por tipo no toca relaciones de otro tipo). Solo debe existir un
    veredicto vigente por par.
    """
    otros_tipos = [t for t in _TIPOS_RELACION if t != tipo_nuevo]
    tx.run("""
        MATCH (p:RegistroTumor {id: $paciente_id})-[r]->(e:EnsayoClinico {nct_id: $nct_id})
        WHERE type(r) IN $otros_tipos
        DELETE r
    """, {"paciente_id": paciente_id, "nct_id": nct_id, "otros_tipos": otros_tipos})


def aplicar_veredicto(tx, paciente_id: str, nct_id: str, resultado: dict):
    """
    Persiste el veredicto como relación. `resultado["veredicto"]` se valida
    contra _QUERIES_POR_VEREDICTO (lista cerrada de 3 valores) ANTES de
    tocar Cypher -- un veredicto fuera de esa lista nunca llega a interpolarse
    en una consulta, se descarta con error explícito.
    """
    veredicto = resultado["veredicto"]
    if veredicto not in _QUERIES_POR_VEREDICTO:
        raise ValueError(f"Veredicto no reconocido, no se persiste: '{veredicto}'")

    _limpiar_veredictos_previos(tx, paciente_id, nct_id, f"{veredicto}_TRIAL")

    props = {
        "motivo": resultado["motivo"],
        "regla": resultado["regla"],
        "criterio_pendiente": resultado.get("criterio_pendiente"),
        "fecha_evaluacion": datetime.now(timezone.utc).isoformat(),
    }
    tx.run(_QUERIES_POR_VEREDICTO[veredicto], {"paciente_id": paciente_id, "nct_id": nct_id, "props": props})


# ---------------------------------------------------------------------------
# LECTURA DE CANDIDATOS
# ---------------------------------------------------------------------------
def obtener_pacientes_mama_activos(session, limit: int | None = None) -> list[dict]:
    cypher = "MATCH (r:RegistroTumor) WHERE r.topografia_codigo STARTS WITH 'C50' RETURN r"
    if limit:
        cypher += " LIMIT $limit"
    resultados = session.run(cypher, {"limit": limit})
    return [dict(record["r"]) for record in resultados]


def obtener_ensayos_activos(session, limit: int | None = None) -> list[dict]:
    cypher = "MATCH (e:EnsayoClinico {estado: 'RECRUITING'}) RETURN e"
    if limit:
        cypher += " LIMIT $limit"
    resultados = session.run(cypher, {"limit": limit})
    return [dict(record["e"]) for record in resultados]


# ---------------------------------------------------------------------------
# ORQUESTACIÓN
# ---------------------------------------------------------------------------
def procesar_par(session, paciente: dict, ensayo: dict, usar_llm: bool) -> dict:
    resultado = evaluar_criterios_deterministicos(paciente, ensayo)

    if resultado["veredicto"] == "CONDICIONA" and usar_llm:
        try:
            resultado = evaluar_criterio_semantico(paciente, ensayo)
        except Exception as e:
            print(f"[Reglas Elegibilidad] ERROR — falló el paso semántico para {paciente.get('id')}/{ensayo.get('nct_id')}: {e}")
            # Se conserva el veredicto determinístico (CONDICIONA) -- un fallo del LLM
            # nunca debe dejar el par sin evaluar ni inventar un resultado.

    session.execute_write(aplicar_veredicto, paciente["id"], ensayo["nct_id"], resultado)
    return resultado


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    usar_llm = "--con-llm" in sys.argv

    max_pacientes = int(args[0]) if len(args) > 0 else None
    max_ensayos = int(args[1]) if len(args) > 1 else None

    print(
        f"Evaluando elegibilidad a ensayos (paso semántico LLM: {'sí' if usar_llm else 'no'})"
        + (f" — máx {max_pacientes} paciente(s)" if max_pacientes else "")
        + (f", máx {max_ensayos} ensayo(s)" if max_ensayos else "")
        + "..."
    )

    conteos = {"HABILITA": 0, "CONDICIONA": 0, "EXCLUYE": 0}
    with driver.session() as session:
        pacientes = obtener_pacientes_mama_activos(session, max_pacientes)
        ensayos = obtener_ensayos_activos(session, max_ensayos)
        print(f"{len(pacientes)} paciente(s) de mama x {len(ensayos)} ensayo(s) reclutando = {len(pacientes) * len(ensayos)} par(es) a evaluar.")

        for paciente in pacientes:
            for ensayo in ensayos:
                resultado = procesar_par(session, paciente, ensayo, usar_llm)
                conteos[resultado["veredicto"]] += 1

    print(f"\nTotal evaluado: {sum(conteos.values())} par(es) — HABILITA: {conteos['HABILITA']}, CONDICIONA: {conteos['CONDICIONA']}, EXCLUYE: {conteos['EXCLUYE']}")


if __name__ == "__main__":
    main()
