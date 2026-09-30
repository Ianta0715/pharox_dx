# Pharox DX

Copiloto clínico de oncología de precisión, enfocado en cáncer de mama. Combina conocimiento
científico público (CIViC, ClinVar, ClinicalTrials.gov, cBioPortal, Europe PMC) con casos
clínicos reales anonimizados en un grafo de conocimiento Neo4j.

El copiloto responde cualquier consulta clínica en lenguaje natural, de dos tipos:

- **Un caso** ("54 años, cT2 cN1 M0, RE 90 %, HER2 2+ sin ISH, ¿opciones?"): las conclusiones
  clínicas (subtipo, escenario, qué opciones cumplen criterios, cuáles quedan condicionales y a qué
  dato) las produce un **motor de reglas determinístico con lógica de tres valores**, versionado y
  auditable. El grafo aporta la **evidencia** que respalda esas opciones.
- **Una pregunta general** ("¿qué evidencia hay para PIK3CA H1047R?", "¿cuántas pacientes triple
  negativo hay en el registro?", "¿qué ensayos reclutan para HER2+ metastásico?"): se responde con
  la base de conocimiento — text-to-Cypher de solo lectura más las fuentes del grafo que la
  pregunta nombra —, como el copiloto de siempre.

En ambos casos el LLM local (Ollama) **redacta** la respuesta a partir del material recuperado, y
esa redacción se **verifica** contra el material antes de llegar al médico.

## Arquitectura

```
                          ┌─ CASO ────► motor de reglas (Tri) ─► evidencia dirigida    DECIDE    app/dominio
texto libre ─► interpretación                                    por el motor
(+ perfil estructurado)   └─ GENERAL ─► evidencia por lo que nombra la pregunta        RESPALDA  app/conocimiento
                     text-to-Cypher de solo lectura ─► "consulta estructurada al grafo"
                                              │
                              dossier markdown armado por código  RESPUESTA app/servicios
                                              │
                      redacción LLM + verificación contra el dossier  REFORMULA app/lenguaje
```

La decisión caso/general (`app/dominio/consulta.py`) es determinística: es un caso si la consulta
aporta 3 o más datos clínicos de fragmentos distintos del texto (o edad + otro dato), o si se envía
un perfil estructurado. Se informa en la respuesta (`modo`, `motivo_modo`) y se puede forzar con
`"modo": "caso" | "general"`.

| Paquete | Responsabilidad | I/O |
|---|---|---|
| `app/dominio/` | Perfil clínico tipado, lógica trivaluada, extracción de texto, interpretación caso/general, léxico de fármacos, motor y catálogo de reglas | Ninguno (puro) |
| `app/conocimiento/` | Conexión a Neo4j en **solo lectura**, esquema, una fuente por módulo, evidencia tipada | Neo4j, embeddings |
| `app/lenguaje/` | Redacción con el LLM local, verificación de la redacción, explorador text-to-Cypher | Ollama |
| `app/servicios/` | Casos de uso: consulta clínica, evaluación de un registro real, dossier markdown | — |
| `app/api/` | HTTP: esquemas Pydantic, API key, dependencias inyectables, un router por área | — |
| `app/bronze/`, `app/gold/` | Pipelines de ingesta (sin cambios de contrato) | APIs públicas, Neo4j |

`app/main.py` solo arma la aplicación (configuración, CORS, ciclo de vida y routers).

### Motor de reglas (`app/dominio/`)

- **Lógica trivaluada** (`tri.py`): cada criterio devuelve `SI`, `NO` o `INDETERMINADO`, con
  conectivas de Kleene. Un dato ausente nunca es negativo: HER2 IHQ 2+ sin ISH es HER2 *sin
  determinar*, no HER2-negativo. Las conectivas cortocircuitan como en la clínica (RE+ descarta
  triple negativo aunque falten RP y HER2).
- **Perfil clínico** (`perfil.py`): un único tipo `PerfilClinico` para texto libre, JSON
  estructurado o un `:RegistroTumor` real (`registro.py`). Deriva receptor hormonal, estado
  HER2, HER2-low, triple negativo, estadio anatómico AJCC, escenario (temprano / metastásico) y
  **subtipos compatibles**: con HER2 2+ sin ISH y RE+ quedan dos, y la evidencia se busca para
  ambos en vez de elegir uno por inferencia.
- **Extracción** (`extraccion.py`): texto → perfil con regex, sin LLM. Cada dato deja una traza
  con el fragmento que lo produjo; lo que está en condicional ("si el ISH da no amplificado") se
  descarta y se informa; M y estadio explícitos le ganan a menciones sueltas de "metastásico".
- **Catálogo** (`reglas/catalogo.py`, `VERSION_REGLAS`): 24 opciones (estudios y tratamientos de
  enfermedad temprana y metastásica), cada una con su **escenario**, fuente (guía + ensayo
  pivotal) y criterios explícitos. Una indicación metastásica da `NO_APLICA` en una paciente M0.
- **Motor** (`reglas/motor.py`): cada opción queda `cumple`, `condicional` (con el dato exacto
  que la destraba) o `no_aplica` (con el criterio que falló), más el **valor de la información**:
  qué dato faltante bloquea más opciones y cuáles decidiría por sí solo.

> Las reglas están en estado **borrador pendiente de validación por comité oncológico**. El
> catálogo completo se expone en `GET /api/v1/reglas` para auditarlo.

### Evidencia (`app/conocimiento/`)

El grafo combina subgrafos independientes entre sí; el cruce lo hace el motor en tiempo de
consulta, por subtipo, escenario, genes y drogas de las opciones abiertas:

```
CIViC               (Variante)-[:TIENE_EVIDENCIA]->(Evidencia)-[:INVOLUCRA_TERAPIA|ASOCIADA_A_ENFERMEDAD|RESPALDADA_POR]->(...)
ClinicalTrials.gov  (EnsayoClinico {condiciones, intervenciones, criterios_elegibilidad, subtipos_relacionados, ...})
                    (RegistroTumor)-[:HABILITA_TRIAL|CONDICIONA_TRIAL|EXCLUYE_TRIAL]->(EnsayoClinico)   ← única relación cruzada
ClinVar             (VarianteClinVar)-[:ASOCIADA_A_CONDICION]->(CondicionClinVar)
cBioPortal          (EstudioCBio)-[:REPORTA_FRECUENCIA]->(FrecuenciaGenCBio)-[:SOBRE_GEN]->(GenCBio)
Registro real       (RegistroTumor)   Hospital Central - Mendoza
Protocolos          (ProtocoloTratamiento), (ActualizacionProtocolo)
Literatura          (Literatura)  Europe PMC, índice vectorial
Clínico             (Paciente)-[:CORRESPONDE_A]->(CasoClinico)-[:INCLUYE_HALLAZGO]->(HallazgoPatologico)
```

`recuperacion.py` tiene dos variantes. En un **caso**, traduce la evaluación del motor en
consultas concretas. En una **pregunta general**, consulta cada fuente solo si la pregunta la
nombra (ensayos, registro, protocolos, guías) o la acota (genes, fármacos, subtipo, escenario):
sin ninguna señal no trae "cinco ensayos cualquiera". Ambas usan una fuente por módulo en
`fuentes/` y devuelven `Evidencia` tipada con id (`E1`, `E2`...), fuente, nivel, referencias
(PMID / NCT), opciones que respalda y si describe a **otras personas** (registro, casos,
frecuencias poblacionales). Una fuente caída no tumba la consulta: queda en `fuentes_fallidas`.

- Protocolos estándar: textuales, filtrados por subtipo **y escenario** (antes una paciente M0
  también recibía el esquema de primera línea metastásica).
- CIViC: por genes y drogas de las opciones abiertas, priorizando las que respaldan una opción.
- Ensayos: reclutando, subtipo compatible, escenario compatible y filtro determinístico de sexo
  y edad (`dominio/ensayos.py`, compartido con el batch de elegibilidad). Nunca concluye
  elegibilidad completa.
- cBioPortal / ClinVar: contexto de los biomarcadores que faltan, nunca como hallazgo del caso.
- Registro local: solo agregado; el registro no trae tratamiento ni evolución.
- Literatura y casos clínicos: búsqueda vectorial (requiere embeddings de Ollama).

Toda lectura corre en una **transacción de solo lectura** (`conocimiento/grafo.leer`): Neo4j
rechaza cualquier escritura o cambio de esquema.

### Lenguaje (`app/lenguaje/`)

- **Redacción** (`redaccion.py`): el LLM recibe exactamente el material que ve el médico y
  responde la consulta citando evidencia por id. Hay un prompt para casos (con el checklist de
  plan completo del copiloto anterior) y otro para preguntas generales.
- **Verificación** (`verificacion.py`): toda droga, ensayo NCT, PMID, porcentaje o id de
  evidencia que nombre la redacción tiene que estar en el material. Política configurable con
  `PHAROX_VERIFICACION`:
  - `advertir` (default): la redacción se devuelve igual, con un aviso visible que lista lo no
    respaldado.
  - `estricta`: la redacción se descarta y se devuelve solo el material determinístico.
  Las fórmulas de consejo ("se recomienda") siempre quedan señaladas: el copiloto informa, no
  prescribe.
- **Text-to-Cypher** (`text_to_cypher.py` + `servicios/exploracion.py`): como antes, en
  `/api/v1/consultar` el LLM traduce la pregunta a Cypher y las filas entran como una pieza de
  evidencia más ("Consulta estructurada al grafo"). Ahora el Cypher se valida por palabra completa
  (CREATE, MERGE, DELETE, SET, DROP, LOAD, CALL, FOREACH...), corre en una transacción de solo
  lectura con `LIMIT` automático, y si falla la consulta sigue con el resto de las fuentes.
  `POST /api/v1/explorar` expone el mismo paso solo, con el Cypher y las filas crudas.

### Pipelines de ingesta

Arquitectura medallion (bronze/silver/gold). Las fuentes públicas siguen el mismo patrón:
`app/bronze/<fuente>_explorer.py` (consulta la API pública, filtra a cáncer de mama) →
`app/gold/<fuente>_to_neo4j.py` (normaliza e ingesta en Neo4j):

- **CIViC**: GraphQL de civicdb.org (requiere `CIVIC_API_KEY`).
- **ClinicalTrials.gov**: REST v2, sin auth.
- **ClinVar**: NCBI E-utilities (`NCBI_API_KEY` opcional).
- **cBioPortal**: REST público, sin auth.
- **Europe PMC**: REST público; el gold necesita Ollama para los embeddings.
- **Vigilancia de protocolos** (ASCO/ESMO): Europe PMC filtrado a guías/consensos de mama.
  NCCN no tiene API pública, así que no está cubierto todavía.
- **Registro de tumores** y **protocolos estándar**: `app/gold/registro_tumores_to_neo4j.py` y
  `app/gold/protocolos_tratamiento_to_neo4j.py` desde las planillas de `data/`.
- **Pacientes (FHIR sintético)**: `app/pipeline_etl.py` anonimiza (SHA-256 + salt) y
  `conocimiento/esquema.py` lo siembra al arrancar. Es sintético: el copiloto no lo usa como
  evidencia.
- **Casos clínicos reales**: `app/etl_casos_clinicos.py` extrae estructura de un informe (texto
  u OCR) vía LLM local y lo ingesta anonimizado.

### Elegibilidad a ensayos persistida

`app/gold/reglas_elegibilidad_trials.py` escribe la relación de elegibilidad entre
`RegistroTumor` y `EnsayoClinico`:

1. **Determinístico** (`dominio/ensayos.evaluar_criterios_deterministicos`, siempre corre): descarta
   por reclutamiento, sexo, edad o subtipo. Nunca concluye `HABILITA`.
2. **Semántico** (opcional, `--con-llm`): sobre los pares `CONDICIONA`, lee el texto de
   `criterios_elegibilidad` y puede confirmar, mantener o excluir. Requiere Ollama.

El veredicto se persiste con `motivo`, `regla` y `fecha_evaluacion`; reevaluar un par reemplaza
el veredicto anterior.

```bash
python -m app.gold.reglas_elegibilidad_trials                 # todos los pacientes x ensayos, solo determinístico
python -m app.gold.reglas_elegibilidad_trials 10 20            # 10 pacientes x 20 ensayos (prueba rápida)
python -m app.gold.reglas_elegibilidad_trials 10 20 --con-llm  # + paso semántico (requiere Ollama)
```

## Requisitos

- Docker y Docker Compose
- Una API key de [CIViC](https://civicdb.org) (Account Settings → API Key)
- **Solo si vas a correr scripts fuera de Docker**: Python **3.10 a 3.12** (ver
  [.python-version](.python-version)). `requirements.txt` fija versiones exactas de numpy/scipy
  que solo tienen wheel precompilado hasta Python 3.12. Si no querés lidiar con esto, corré los
  scripts **dentro del contenedor del backend**:
  ```bash
  docker exec -it pharox_backend python -m app.gold.civic_to_neo4j
  ```

## Setup

```bash
cp .env.example .env
# completar CIVIC_API_KEY, ANONYMIZATION_SALT y PHAROX_API_KEY en .env
# (los dos últimos podés generarlos con: python -c "import secrets; print(secrets.token_urlsafe(32))")
# completar NEO4J_PASSWORD con la contraseña de AuraDB (pedila al equipo por un canal privado)

docker compose up -d --build
```

Esto levanta Ollama (descarga automáticamente `qwen3:8b` y `nomic-embed-text`) y el backend
FastAPI en `localhost:8000`. La base de grafos es la instancia **AuraDB Free "Pharox"**
compartida por el equipo (`NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD` y `NEO4J_DATABASE` en
`.env`), por lo que todos ven los mismos datos. Se puede explorar desde console.neo4j.io →
Instances → Query. Aura Free se pausa tras 3 días sin uso: si el backend no conecta (la API
responde 503 o las respuestas listan "Neo4j no disponible" en las fuentes no consultadas),
reactivar la instancia desde la consola.

Para trabajar con un Neo4j local en su lugar (`localhost:7474` / `bolt://localhost:7687`):
`docker compose --profile local-db up -d` y apuntar `NEO4J_URI`/`NEO4J_USER`/`NEO4J_PASSWORD`/`NEO4J_DATABASE=neo4j` en `.env` a
esa base (ver comentarios en [.env.example](.env.example)).

Todas las lecturas del backend corren en transacciones de solo lectura, también contra AuraDB.
El motor de reglas no depende de Neo4j ni de Ollama: si la base no está disponible,
`/api/v1/evaluar` igual evalúa el perfil e informa las fuentes que no pudo consultar; si Ollama no
está, la respuesta es el dossier determinístico sin redacción.

### Dependencias

`requirements.txt` tiene versiones exactas (pin reproducible) — no editar a mano. Para agregar
o actualizar una dependencia: editar [requirements.in](requirements.in) y regenerar el pin con el
comando documentado en el encabezado de `requirements.txt` (usa `python:3.10-slim`, la misma base
que el `Dockerfile`). `langchain-neo4j` ya no se usa en el código y se puede quitar en el próximo
regenerado.

### Cargar conocimiento público (opcional, fuera de Docker)

Requiere el backend levantado al menos una vez antes (crea las constraints e índices de
`app/conocimiento/esquema.py`, incluida la de `Literatura` que usa Europe PMC).

```bash
python -m app.bronze.civic_explorer BRAF V600E && python -m app.gold.civic_to_neo4j
python -m app.bronze.clinicaltrials_explorer "breast cancer" RECRUITING && python -m app.gold.clinicaltrials_to_neo4j
python -m app.bronze.clinvar_explorer BRCA1 && python -m app.gold.clinvar_to_neo4j
python -m app.bronze.cbioportal_explorer brca_metabric && python -m app.gold.cbioportal_to_neo4j
python -m app.bronze.europepmc_explorer && python -m app.gold.europepmc_to_neo4j
python -m app.bronze.vigilancia_protocolos_explorer && python -m app.gold.vigilancia_protocolos_to_neo4j
python -m app.gold.registro_tumores_to_neo4j
python -m app.gold.protocolos_tratamiento_to_neo4j
```

## Endpoints

Todos salvo `/` requieren el header `X-API-Key` con el valor de `PHAROX_API_KEY`.

| Endpoint | Método | Descripción |
|---|---|---|
| `/` | GET | Health check público |
| `/api/v1/consultar` | POST | Copiloto: cualquier consulta clínica (caso o pregunta general) → motor y/o base de conocimiento + text-to-Cypher + redacción verificada |
| `/api/v1/evaluar` | POST | Mismo contrato, sin ningún LLM (ni redacción ni text-to-Cypher): funciona sin Ollama |
| `/api/v1/registro_tumores/{id}/evaluacion` | GET | Evalúa con el motor a una paciente real del registro |
| `/api/v1/reglas` | GET | Catálogo de reglas con versión, fuentes y criterios (auditoría) |
| `/api/v1/explorar` | POST | Explorador text-to-Cypher de solo lectura (no es evaluación clínica) |
| `/api/v1/registro_tumores` | GET | Lista pacientes reales de mama, filtrable por `subtipo_molecular` |
| `/api/v1/registro_tumores/{id}/protocolo_estandar` | GET | Protocolos estándar del subtipo de un paciente real |
| `/api/v1/cohorte/resumen` | GET | Registro local + frecuencias génicas de cBioPortal, agregados |
| `/api/v1/pacientes/{id}/elegibilidad_trials` | GET | Elegibilidad a ensayos ya persistida |
| `/api/v1/protocolos/actualizaciones` | GET | Actualizaciones de guías ASCO/ESMO |
| `/api/v1/casos/ingestar` | POST | Ingesta un caso clínico real (texto y/o imagen) |
| `/api/v1/casos/ingestar_texto` | POST | Ingesta un caso clínico real (solo texto) |
| `/api/v1/casos/buscar` | GET | Casos clínicos reales similares por texto |
| `/api/v1/debug/graph_db` | GET | Estado del grafo (conteos de nodos/relaciones) |

La respuesta de `/consultar` y `/evaluar` trae: `modo` (caso / general) y por qué; el perfil
interpretado con el **origen** de cada dato (fragmento del texto, perfil estructurado o registro);
lo descartado por condicional; en un caso, la evaluación completa del motor (estado y criterios de
cada opción, datos faltantes, valor de la información; `null` en una pregunta general); la
evidencia tipada; las fuentes que fallaron; la redacción con sus advertencias; y `respuesta`, todo
en markdown listo para mostrar. También conserva los campos del contrato anterior
(`cypher_utilizado`, `evidencia_recuperada`, `metodo_recuperacion`).

```bash
# Pregunta general, como el copiloto de siempre
curl -X POST http://localhost:8000/api/v1/consultar \
  -H "X-API-Key: $PHAROX_API_KEY" -H "Content-Type: application/json" \
  -d '{"consulta": "¿Qué evidencia clínica existe para la variante H1047R de PIK3CA en cáncer de mama?"}'

# Un caso, con un dato estructurado que tiene prioridad sobre el texto
curl -X POST http://localhost:8000/api/v1/consultar \
  -H "X-API-Key: $PHAROX_API_KEY" -H "Content-Type: application/json" \
  -d '{"consulta": "Paciente de 54 años, CDI cT2 cN1 M0, RE 90%, RP 70%, HER2 IHQ 2+ sin ISH, Ki-67 25%, G2. ¿Opciones?",
       "perfil": {"menopausia": "post"}}'
```

Documentación interactiva (Swagger) en `http://localhost:8000/docs`. El origen del frontend que
va a llamar a la API debe agregarse a `CORS_ORIGINS` en `.env`.

## Tests

No requieren Neo4j, Ollama ni GPU: la API se prueba con un lector de grafo y un LLM falsos
(`tests/fakes.py`) inyectados vía `dependency_overrides`.

```bash
pip install -r requirements-dev.txt
cd pharox_backend
pytest -v
```

Cubren la lógica trivaluada, las derivaciones del perfil, la extracción de texto (incluidos los
errores del detector anterior), cada regla del catálogo con casos clínicos concretos, el mapeo del
registro real, la recuperación de evidencia, la verificación de la redacción, el explorador
Cypher, la API de punta a punta y las normalizaciones de ingesta. Corren en cada push/PR a `main`
vía GitHub Actions ([.github/workflows/ci.yml](.github/workflows/ci.yml)), junto con un build de la
imagen Docker.

## Logging

`app/logging_config.py` controla el formato de los logs:

- `LOG_FORMAT=text` (default) — coloreado, para leer en una terminal de desarrollo.
- `LOG_FORMAT=json` — una línea JSON por evento, para Azure Log Analytics / Application Insights.
- `LOG_LEVEL` — `DEBUG`/`INFO`/`WARNING`/`ERROR` (default `INFO`).

Los errores internos se registran con traza completa en el log y se devuelven al cliente como un
mensaje genérico (sin `repr` de la excepción). Si Neo4j no está disponible, la API responde 503.

## Privacidad

Los pacientes nunca se identifican por nombre. Los IDs se anonimizan con SHA-256 + un salt
secreto (`ANONYMIZATION_SALT`, obligatorio y único por entorno — nunca lo commitees). Los casos
clínicos reales se identifican por un hash del contenido clínico, sin nombre ni fecha exacta.
Toda tarea que procesa texto clínico usa el LLM local (`ai_gateway.get_llm(data_sensitive=True)`):
ningún dato de paciente sale a una API externa.
