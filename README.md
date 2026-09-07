# Pharox DX

Copiloto clínico de oncología de precisión. Combina conocimiento científico público (CIViC) con
casos clínicos reales anonimizados en un grafo de conocimiento Neo4j, y usa LangChain (LCEL) +
un LLM local (Ollama) para responder preguntas médicas en lenguaje natural traduciéndolas a
Cypher y sintetizando la evidencia recuperada.

## Arquitectura

El grafo de Neo4j combina dos subgrafos independientes (no hay relaciones directas entre ellos):

```
CIViC (conocimiento científico público)
  (Variante)-[:TIENE_EVIDENCIA]->(Evidencia)
  (Evidencia)-[:ASOCIADA_A_ENFERMEDAD]->(Enfermedad)
  (Evidencia)-[:INVOLUCRA_TERAPIA]->(Terapia)
  (Evidencia)-[:RESPALDADA_POR]->(Fuente)

Clínico (pacientes anonimizados)
  (Paciente)-[:DIAGNOSTICADO_CON]->(Tumor)-[:TRATADO_CON]->(Tratamiento)
  (Paciente)-[:CORRESPONDE_A]->(CasoClinico)-[:INCLUYE_HALLAZGO]->(HallazgoPatologico)-[:ASOCIADO_A_TUMOR]->(Tumor)
                                (CasoClinico)-[:TUVO_EVENTO_POSTOP]->(EventoPostOperatorio)
  (Paciente)-[:TIENE_ANTECEDENTE]->(AntecedenteMedico)
```

Pipelines de ingesta (arquitectura medallion, capas bronze/silver/gold):

- **CIViC**: `app/bronze/civic_explorer.py` (consulta la API GraphQL de civicdb.org) →
  `app/gold/civic_to_neo4j.py` (normaliza e ingesta en Neo4j).
- **Pacientes (FHIR sintético)**: `app/pipeline_etl.py` anonimiza (SHA-256 + salt) y estructura
  el dataset en capas silver/gold → `graph_db.py` lo ingesta en Neo4j al arrancar el backend.
- **Casos clínicos reales**: `app/etl_casos_clinicos.py` — recibe texto u OCR de un informe,
  extrae estructura vía LLM y lo ingesta anonimizado (hash SHA-256 del contenido clínico, sin
  nombre ni fecha exacta).

Motor de consultas (`app/main.py`, orquestado con LangChain LCEL):

1. **Text-to-Cypher**: el LLM traduce la pregunta médica a Cypher usando few-shot prompting
   sobre el esquema real del grafo.
2. **Filtro de seguridad**: solo se permiten operaciones de lectura (`MATCH`/`RETURN`); cualquier
   `DELETE`/`CREATE`/`MERGE`/`SET`/`REMOVE`/`DETACH` generado por el LLM se bloquea.
3. **Fallback híbrido** (si el Cypher falla, es bloqueado o no devuelve nada): búsqueda vectorial
   sobre `Literatura` y `CasoClinico`, recorrido de relaciones fijo en el subgrafo clínico, y
   búsqueda por palabras clave en el subgrafo CIViC.
4. **Síntesis clínica**: un segundo prompt redacta la respuesta final citando la evidencia
   recuperada (papers, casos reales similares, evidencia CIViC).

## Requisitos

- Docker y Docker Compose
- Una API key de [CIViC](https://civicdb.org) (Account Settings → API Key)

## Setup

```bash
cp .env.example .env
# completar CIVIC_API_KEY, ANONYMIZATION_SALT y PHAROX_API_KEY en .env
# (los dos últimos podés generarlos con: python -c "import secrets; print(secrets.token_urlsafe(32))")

docker compose up -d --build
```

Esto levanta Neo4j (`localhost:7474` browser / `bolt://localhost:7687`), Ollama (descarga
automáticamente `qwen3:8b` y `nomic-embed-text`) y el backend FastAPI en `localhost:8000`.

Ver variables de entorno documentadas en [.env.example](.env.example).

### Dependencias

`requirements.txt` tiene versiones exactas (pin reproducible) — no editar a mano. Para agregar
o actualizar una dependencia: editar [requirements.in](requirements.in) (rangos amplios) y
regenerar el pin con el comando documentado en el encabezado de `requirements.txt` (usa
`python:3.10-slim`, la misma base que el `Dockerfile`, para que las versiones resueltas sean
las que realmente corren en producción).

### Cargar conocimiento CIViC (opcional, fuera de Docker)

```bash
python -m app.bronze.civic_explorer BRAF V600E
python -m app.gold.civic_to_neo4j
```

## Endpoints principales

Todos los endpoints salvo `/` requieren el header `X-API-Key` con el valor de `PHAROX_API_KEY`.

| Endpoint | Método | Descripción |
|---|---|---|
| `/` | GET | Health check público (sin auth), para probes de infraestructura |
| `/api/v1/consultar` | POST | Consulta clínica en lenguaje natural (motor Graph RAG completo) |
| `/api/v1/casos/ingestar` | POST | Ingesta un caso clínico real (texto y/o imagen de informe) |
| `/api/v1/casos/ingestar_texto` | POST | Ingesta un caso clínico real (solo texto) |
| `/api/v1/casos/buscar` | GET | Busca casos clínicos reales similares por texto |
| `/api/v1/debug/graph_db` | GET | Estado del grafo (conteos de nodos/relaciones) |

```bash
curl -H "X-API-Key: $PHAROX_API_KEY" http://localhost:8000/api/v1/debug/graph_db
```

Documentación interactiva (Swagger) en `http://localhost:8000/docs`. El origen del frontend que
va a llamar a la API debe agregarse a `CORS_ORIGINS` en `.env`.

## Tests

Los tests cubren solo funciones puras (normalización, filtro de seguridad Cypher,
anonimización) — no requieren Neo4j, Ollama ni GPU levantados.

```bash
pip install -r requirements-dev.txt
cd pharox_backend
pytest -v
```

Corren automáticamente en cada push/PR a `main` vía GitHub Actions
([.github/workflows/ci.yml](.github/workflows/ci.yml)), junto con un build de
la imagen Docker para detectar roturas del `Dockerfile`.

## Logging

`app/logging_config.py` controla el formato de los logs:

- `LOG_FORMAT=text` (default) — coloreado, para leer en una terminal de desarrollo.
- `LOG_FORMAT=json` — una línea JSON por evento, para Azure Log Analytics / Application Insights.
- `LOG_LEVEL` — `DEBUG`/`INFO`/`WARNING`/`ERROR` (default `INFO`).

Los scripts de línea de comandos (`bronze/civic_explorer.py`, `gold/civic_to_neo4j.py`,
`pipeline_etl.py`) siguen usando `print()` a propósito — son herramientas que corre un
humano directamente, no logs de un servicio.

## Privacidad

Los pacientes nunca se identifican por nombre. Los IDs se anonimizan con SHA-256 + un salt
secreto (`ANONYMIZATION_SALT`, obligatorio y único por entorno — nunca lo commitees). Los casos
clínicos reales se identifican por un hash del contenido clínico, sin nombre ni fecha exacta.
