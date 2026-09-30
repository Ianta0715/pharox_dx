"""
Configuración del backend, leída de variables de entorno en un único lugar.

Ver .env.example para la documentación de cada variable. Los scripts de
ingesta (bronze/gold) siguen leyendo sus propias variables con load_dotenv()
porque se corren sueltos desde la terminal.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    neo4j_uri: str
    neo4j_user: str
    neo4j_password: str
    # En AuraDB es el id de la instancia. None: la base por defecto del usuario.
    neo4j_database: str | None
    pharox_api_key: str | None
    cors_origins: tuple[str, ...]
    # "advertir" (default): la redacción del LLM se devuelve siempre, con un aviso
    # visible si menciona datos que no están en la evidencia. "estricta": en ese
    # caso se descarta y se devuelve solo el material determinístico.
    verificacion_estricta: bool


@lru_cache
def get_settings() -> Settings:
    load_dotenv()
    return Settings(
        neo4j_uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        neo4j_user=os.getenv("NEO4J_USER", "neo4j"),
        neo4j_password=os.getenv("NEO4J_PASSWORD", "pharoxpass"),
        neo4j_database=os.getenv("NEO4J_DATABASE") or None,
        pharox_api_key=os.getenv("PHAROX_API_KEY") or None,
        cors_origins=tuple(
            o.strip()
            for o in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:3000").split(",")
            if o.strip()
        ),
        verificacion_estricta=os.getenv("PHAROX_VERIFICACION", "advertir").strip().lower() == "estricta",
    )
