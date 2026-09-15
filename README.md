# Pharox DX

Copiloto clínico de oncología de precisión, enfocado en cáncer de mama. Combina conocimiento
científico público (CIViC, ClinVar, ClinicalTrials.gov, cBioPortal, Europe PMC) con casos
clínicos reales anonimizados en un grafo de conocimiento Neo4j, y usa LangChain (LCEL) + un LLM
local (Ollama) para responder preguntas médicas en lenguaje natural traduciéndolas a Cypher y
sintetizando la evidencia recuperada.

## Arquitectura

El grafo de Neo4j combina varios subgrafos independientes entre sí (no hay relaciones directas
entre ellos — ver la nota de independencia en `app/graph_db.py`) más el subgrafo clínico. La
única excepción deliberada es la relación `RegistroTumor -> EnsayoClinico` que crea
`app/gold/reglas_elegibilidad_trials.py` (ver más abajo): para todo lo demás, exploratorio y
heterogéneo, el cruce sigue siendo en tiempo de consulta; para elegibilidad a ensayos — estado de
alto valor que se vuelve a consultar y necesita quedar auditado — se persiste como relación real.

```
CIViC (conocimiento científico público)
  (Variante)-[:TIENE_EVIDENCIA]->(Evidencia)
  (Evidencia)-[:ASOCIADA_A_ENFERMEDAD]->(Enfermedad)
  (Evidencia)-[:INVOLUCRA_TERAPIA]->(Terapia)
  (Evidencia)-[:RESPALDADA_POR]->(Fuente)

ClinicalTrials.gov (ensayos clínicos activos)
  (EnsayoClinico {condiciones, intervenciones, fase, estado, criterios_elegibilidad,
                  sexo, edad_minima_anios, edad_maxima_anios, subtipos_relacionados, ...})
  (RegistroTumor)-[:HABILITA_TRIAL | :CONDICIONA_TRIAL | :EXCLUYE_TRIAL]->(EnsayoClinico)
    — única relación cruzada entre subgrafos, ver app/gold/reglas_elegibilidad_trials.py

ClinVar (variantes clasificadas clínicamente, con HGVS/coordenadas que CIViC no expone)
  (VarianteClinVar)-[:ASOCIADA_A_CONDICION]->(CondicionClinVar)

cBioPortal (frecuencia de alteración en cohortes públicas reales)
  (EstudioCBio)-[:REPORTA_FRECUENCIA]->(FrecuenciaGenCBio)-[:SOBRE_GEN]->(GenCBio)

Clínico (pacientes anonimizados)
  (Paciente)-[:DIAGNOSTICADO_CON]->(Tumor)-[:TRATADO_CON]->(Tratamiento)
  (Paciente)-[:CORRESPONDE_A]->(CasoClinico)-[:INCLUYE_HALLAZGO]->(HallazgoPatologico)-[:ASOCIADO_A_TUMOR]->(Tumor)
                                (CasoClinico)-[:TUVO_EVENTO_POSTOP]->(EventoPostOperatorio)
  (Paciente)-[:TIENE_ANTECEDENTE]->(AntecedenteMedico)
```

`Literatura` (búsqueda vectorial, ver más abajo) no es un subgrafo propio, es una etiqueta plana
que solo se puebla vía `app/gold/europepmc_to_neo4j.py` (PMIDs reales de Europe PMC, filtrado a
mama) — sin correr ese script, `:Literatura` está vacía. Hubo una semilla sintética
(`data_oncologica.json`, 16 fichas sin cita real, la mayoría ni siquiera de mama) que se sacó a
propósito por no aportar evidencia utilizable; si tu Neo4j local la tiene de una corrida vieja,
limpiala con `MATCH (n:Literatura) WHERE n.id STARTS WITH 'lit_' DETACH DELETE n`.

Pipelines de ingesta (arquitectura medallion, capas bronze/silver/gold). Las cinco fuentes
públicas siguen el mismo patrón: `app/bronze/<fuente>_explorer.py` (consulta la API pública,
filtra a cáncer de mama) → `app/gold/<fuente>_to_neo4j.py` (normaliza e ingesta en Neo4j):

- **CIViC**: `app/bronze/civic_explorer.py` (GraphQL de civicdb.org, requiere `CIVIC_API_KEY`) →
  `app/gold/civic_to_neo4j.py`.
- **ClinicalTrials.gov**: `app/bronze/clinicaltrials_explorer.py` (REST v2, sin auth) →
  `app/gold/clinicaltrials_to_neo4j.py`.
- **ClinVar**: `app/bronze/clinvar_explorer.py` (NCBI E-utilities, `NCBI_API_KEY` opcional) →
  `app/gold/clinvar_to_neo4j.py`.
- **cBioPortal**: `app/bronze/cbioportal_explorer.py` (REST público, sin auth) →
  `app/gold/cbioportal_to_neo4j.py`.
- **Europe PMC**: `app/bronze/europepmc_explorer.py` (REST público, sin auth) →
  `app/gold/europepmc_to_neo4j.py` (requiere Ollama levantado: genera embeddings).
- **Vigilancia de protocolos** (ASCO/ESMO, Pharox_Documento_v5 §11 Capa 2):
  `app/bronze/vigilancia_protocolos_explorer.py` (Europe PMC filtrado a guías/consensos de
  mama, sin auth) → `app/gold/vigilancia_protocolos_to_neo4j.py`. Pensada para correrse
  periódicamente (cron o manual) y detectar actualizaciones de guías relevantes para el
  perfil molecular de casos activos. NCCN no tiene API pública ni se indexa en Europe PMC
  (guías vivas, no papers), así que no está cubierto todavía.
- **Pacientes (FHIR sintético)**: `app/pipeline_etl.py` anonimiza (SHA-256 + salt) y estructura
  el dataset en capas silver/gold → `graph_db.py` lo ingesta en Neo4j al arrancar el backend.
- **Casos clínicos reales**: `app/etl_casos_clinicos.py` — recibe texto u OCR de un informe,
  extrae estructura vía LLM y lo ingesta anonimizado (hash SHA-256 del contenido clínico, sin
  nombre ni fecha exacta).

**Elegibilidad a ensayos** (Pharox_Documento_v5 §11 Capa 1, estado persistente):
`app/gold/reglas_elegibilidad_trials.py` — a diferencia de todo lo anterior, no ingesta una
fuente nueva: lee `RegistroTumor` (pacientes de mama) y `EnsayoClinico` (ya ingeridos) y escribe
la relación de elegibilidad entre ambos. Dos pasos:

1. **Determinístico** (`evaluar_criterios_deterministicos`, siempre corre, sin LLM): descarta por
   estado de reclutamiento, sexo, rango etario o subtipo molecular. Nunca concluye `HABILITA` —
   con los campos estructurados disponibles solo se puede descartar con confianza, no confirmar
   elegibilidad completa. Resultado: `EXCLUYE` o `CONDICIONA`.
2. **Semántico** (`evaluar_criterio_semantico`, opcional, flag `--con-llm`): solo sobre los pares
   que quedaron en `CONDICIONA`. Lee el texto real de `criterios_elegibilidad` contra el perfil
   completo (incluye ECOG) y puede confirmar `HABILITA`, mantener `CONDICIONA` (nombrando qué
   falta) o bajar a `EXCLUYE`. Requiere Ollama levantado.

El veredicto se persiste como relación (`HABILITA_TRIAL`/`CONDICIONA_TRIAL`/`EXCLUYE_TRIAL`) con
`motivo`, `regla` y `fecha_evaluacion` como propiedades — instantáneo y auditable de ahí en más,
sin recalcular en cada consulta. Reevaluar un par borra el veredicto anterior entre ese paciente y
ese ensayo antes de escribir el nuevo (nunca coexisten dos veredictos para el mismo par).

```bash
python -m app.gold.reglas_elegibilidad_trials                 # todos los pacientes x ensayos, solo determinístico
python -m app.gold.reglas_elegibilidad_trials 10 20            # 10 pacientes x 20 ensayos (prueba rápida)
python -m app.gold.reglas_elegibilidad_trials 10 20 --con-llm  # + paso semántico (requiere Ollama)
```

Motor de consultas (`app/main.py`, orquestado con LangChain LCEL):

1. **Text-to-Cypher**: el LLM traduce la pregunta médica a Cypher usando few-shot prompting
   sobre el esquema real del grafo.
2. **Filtro de seguridad**: solo se permiten operaciones de lectura (`MATCH`/`RETURN`); cualquier
   `DELETE`/`CREATE`/`MERGE`/`SET`/`REMOVE`/`DETACH` generado por el LLM se bloquea.
3. **Búsqueda híbrida** (se ejecuta siempre como complemento, no solo si el Cypher dirigido
   falla): búsqueda vectorial sobre `Literatura` y `CasoClinico`, recorrido de relaciones fijo en
   el subgrafo clínico, y búsqueda por palabras clave en los subgrafos públicos independientes
   (CIViC, ClinicalTrials.gov, ClinVar, cBioPortal).
4. **Síntesis clínica**: un segundo prompt redacta la respuesta final citando la evidencia
   recuperada (papers, casos reales similares, evidencia CIViC).

## Requisitos

- Docker y Docker Compose
- Una API key de [CIViC](https://civicdb.org) (Account Settings → API Key)
- **Solo si vas a correr scripts fuera de Docker** (sección "Cargar conocimiento público" más
  abajo): Python **3.10 a 3.12** (ver [.python-version](.python-version)). `requirements.txt`
  fija versiones exactas de numpy/scipy que solo tienen wheel precompilado hasta Python 3.12 —
  con una versión más nueva (ej. 3.13/3.14, la que trae por defecto Ubuntu 24.04+ en WSL) pip
  intenta compilarlos desde código fuente y falla en cascada pidiendo gcc, gfortran, pkg-config,
  libopenblas, etc. Si no querés lidiar con esto, corré los scripts **dentro del contenedor del
  backend** en su lugar — ya tiene Python 3.10 con todo instalado:
  ```bash
  docker exec -it pharox_backend python -m app.gold.civic_to_neo4j
  ```

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

**Si `pip install -r requirements.txt` falla compilando numpy/scipy** (errores de meson pidiendo
gcc, gfortran, pkg-config o OpenBLAS), es porque tu Python local es más nuevo que 3.12 y no hay
wheel precompilado para esas versiones exactas — ver la nota en [Requisitos](#requisitos). Esto
nunca pasa con `docker compose up --build` (usa Python 3.10 dentro del contenedor).

### Cargar conocimiento público (opcional, fuera de Docker)

Requiere el backend levantado al menos una vez antes (crea las constraints e índices de
`graph_db.py`, incluida la de `Literatura` que usa Europe PMC). Cada fuente sigue el mismo
patrón bronze → gold:

```bash
# CIViC (requiere CIVIC_API_KEY en .env)
python -m app.bronze.civic_explorer BRAF V600E
python -m app.gold.civic_to_neo4j

# ClinicalTrials.gov (sin auth)
python -m app.bronze.clinicaltrials_explorer "breast cancer" RECRUITING
python -m app.gold.clinicaltrials_to_neo4j

# ClinVar (NCBI_API_KEY opcional, sube el rate limit de 3 a 10 req/s)
python -m app.bronze.clinvar_explorer BRCA1
python -m app.gold.clinvar_to_neo4j

# cBioPortal (sin auth)
python -m app.bronze.cbioportal_explorer brca_metabric
python -m app.gold.cbioportal_to_neo4j

# Europe PMC (sin auth, pero el gold necesita Ollama levantado para los embeddings)
python -m app.bronze.europepmc_explorer
python -m app.gold.europepmc_to_neo4j

# Vigilancia de protocolos ASCO/ESMO (sin auth; sin Ollama, no genera embeddings)
python -m app.bronze.vigilancia_protocolos_explorer        # últimos 180 días por defecto
python -m app.gold.vigilancia_protocolos_to_neo4j
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
| `/api/v1/protocolos/actualizaciones` | GET | Lista actualizaciones de guías ASCO/ESMO, filtrable por `subtipo_molecular` |
| `/api/v1/pacientes/{paciente_id}/elegibilidad_trials` | GET | Elegibilidad a ensayos ya calculada para un paciente (`RegistroTumor.id`), lectura instantánea |
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

Los scripts de línea de comandos (todo `bronze/*_explorer.py`, `gold/*_to_neo4j.py`,
`pipeline_etl.py`) siguen usando `print()` a propósito — son herramientas que corre un
humano directamente, no logs de un servicio.

## Privacidad

Los pacientes nunca se identifican por nombre. Los IDs se anonimizan con SHA-256 + un salt
secreto (`ANONYMIZATION_SALT`, obligatorio y único por entorno — nunca lo commitees). Los casos
clínicos reales se identifican por un hash del contenido clínico, sin nombre ni fecha exacta.
