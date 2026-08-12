from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import os
import re

# Importamos la nueva función ejecutar_cypher_dinamico
from app.graph_db import inicializar_db, buscar_contexto_hibrido, obtener_estado_grafo, ejecutar_cypher_dinamico
from app.ai_gateway import llamar_modelo

app = FastAPI(
    title="Pharox DX - Core Engine (Graph RAG)",
    version="2.0.0",
    description="Backend Server para Orquestación Graph RAG con Neo4j y Text-to-Cypher"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ConsultaMedicaRequest(BaseModel):
    consulta: str
    tipo_cancer: str = "Cáncer de Pulmón (NSCLC)"

@app.on_event("startup")
def startup_event():
    try:
        inicializar_db()
    except Exception as e:
        print(f"Error al inicializar la BD de grafos: {e}")

@app.get("/")
def health_check():
    return {"status": "ok", "service": "Pharox DX Graph Engine Running"}

@app.post("/api/v1/consultar")
def consultar_copiloto(payload: ConsultaMedicaRequest):
    """
    Endpoint principal. Implementa el verdadero concepto de "Querying":
    1. Traduce Lenguaje Natural a Cypher.
    2. Ejecuta en Neo4j.
    3. Formula la respuesta clínica.
    """
    
    # ---------------------------------------------------------
    # PASO 1: TEXT-TO-CYPHER (Traducción al Vuelo)
    # ---------------------------------------------------------
    prompt_traductor = f"""
    Actúa como un experto traductor de Lenguaje Natural a consultas Cypher para Neo4j.
    Este es el esquema de nuestra base de datos oncológica:
    - Nodo (Paciente) con propiedades: hash, edad, anio_nacimiento
    - Nodo (Tumor) con propiedades: tipo_cancer, cie10, descripcion
    - Nodo (Tratamiento) con propiedades: droga
    - Relaciones: (Paciente)-[:DIAGNOSTICADO_CON]->(Tumor)-[:TRATADO_CON]->(Tratamiento)
    
    Pregunta del médico: "{payload.consulta}"
    
    Genera ÚNICAMENTE el código Cypher (usando MATCH y RETURN) para responder esta pregunta. No incluyas texto extra.
    """
    
    try:
        # La IA genera el código Cypher
        cypher_generado = llamar_modelo(prompt=prompt_traductor, model="qwen2.5:1.5b")
        
        # Ejecutamos el Cypher generado en la base de datos
        datos_cypher = ejecutar_cypher_dinamico(cypher_generado)
        
        # ---------------------------------------------------------
        # PASO 2: FALLBACK A BÚSQUEDA HÍBRIDA (Por si la IA falló el Cypher)
        # ---------------------------------------------------------
        if not datos_cypher:
            print("El Cypher dinámico falló o vino vacío. Usando fallback de vectores y relaciones estándar.")
            contextos_fallback = buscar_contexto_hibrido(query=payload.consulta, tipo_cancer=payload.tipo_cancer, n_results=3)
            evidencia = "\n".join([f"- {c}" for c in contextos_fallback]) if contextos_fallback else "Sin evidencia local."
        else:
            print(f"Éxito Text-to-Cypher! Datos obtenidos: {datos_cypher}")
            evidencia = datos_cypher

        # ---------------------------------------------------------
        # PASO 3: RESPUESTA CLÍNICA FINAL
        # ---------------------------------------------------------
        prompt_clinico = f"""
        Actúa como un Copiloto Clínico Experto en Oncología de Precisión.
        Analiza la consulta médica relacionada con {payload.tipo_cancer}.
        
        Basado ESTRICTAMENTE en esta evidencia recuperada de nuestra base de datos de grafos:
        {evidencia}
        
        Consulta Médica: "{payload.consulta}"
        
        Responde con rigor clínico, de forma concisa y estructurada. Si la evidencia dice "Sin evidencia", indícalo amablemente.
        """
        
        resultado_final = llamar_modelo(prompt=prompt_clinico, model="qwen2.5:1.5b")
        
        return {
            "success": True,
            "respuesta": resultado_final,
            "cypher_utilizado": cypher_generado,
            "evidencia_recuperada": evidencia
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en el Copiloto Clínico (Graph RAG): {repr(e)}")

@app.get("/api/v1/debug/graph_db")
def ver_base_de_grafos():
    try:
        datos = obtener_estado_grafo()
        return {"success": True, "estado": datos}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al leer base de grafos: {repr(e)}")