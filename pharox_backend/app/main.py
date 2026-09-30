"""
Pharox DX — punto de entrada de la API.

Solo arma la aplicación: configuración, CORS, ciclo de vida y routers. La
lógica vive en capas separadas (ver README, sección Arquitectura):

    app/dominio       perfil clínico, lógica trivaluada y motor de reglas (decide)
    app/conocimiento  acceso de solo lectura al grafo y evidencia tipada (respalda)
    app/lenguaje      redacción opcional con el LLM local + verificación (reformula)
    app/servicios     casos de uso que combinan las tres capas
    app/api           HTTP: esquemas, seguridad y rutas
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from neo4j.exceptions import ServiceUnavailable
from starlette.concurrency import run_in_threadpool

from app.api.rutas import casos, copiloto, exploracion, protocolos, registro, sistema
from app.config import get_settings
from app.conocimiento.esquema import inicializar_db
from app.conocimiento.grafo import cerrar_driver
from app.logging_config import configurar_logging, get_logger

configurar_logging()
logger = get_logger("pharox.main")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        await run_in_threadpool(inicializar_db)
        logger.info("Grafo Neo4j inicializado (constraints, índices y siembra).")
    except Exception as e:
        # El backend arranca igual: el motor de reglas no depende de Neo4j, y
        # los endpoints que sí lo necesitan responden 503 hasta que vuelva.
        logger.error(f"No se pudo inicializar Neo4j al arrancar: {e}")
    yield
    cerrar_driver()


def create_app() -> FastAPI:
    settings = get_settings()
    if not settings.pharox_api_key:
        raise RuntimeError(
            "PHAROX_API_KEY no está definida. Agregala al archivo .env antes de levantar el backend: PHAROX_API_KEY=<valor secreto>"
        )

    app = FastAPI(
        title="Pharox DX - Copiloto de oncología de precisión",
        version="4.0.0",
        description="Motor de reglas clínicas trivaluado + grafo de conocimiento Neo4j + redacción opcional con LLM local.",
        lifespan=lifespan,
    )
    # Lista explícita de orígenes: nunca "*" con credentials.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(ServiceUnavailable)
    async def _neo4j_no_disponible(_request: Request, exc: ServiceUnavailable):
        logger.error(f"Neo4j no disponible: {exc}")
        return JSONResponse(status_code=503, content={"detail": "La base de conocimiento (Neo4j) no está disponible."})

    app.include_router(sistema.publico)
    app.include_router(sistema.router)
    app.include_router(copiloto.router)
    app.include_router(registro.router)
    app.include_router(protocolos.router)
    app.include_router(casos.router)
    app.include_router(exploracion.router)
    return app


app = create_app()
