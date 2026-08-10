from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
import os

app = FastAPI(
    title="Pharox DX - Core Engine",
    version="1.0.0",
    description="Backend Server para Orquestación RAG y Ceguera Técnica"
)

# Configuración de CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Volvemos a la URL exacta que funcionó en tu script de prueba
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")

class ConsultaMedicaRequest(BaseModel):
    consulta: str
    tipo_cancer: str = "Cáncer de Pulmón (NSCLC)"

@app.get("/")
def health_check():
    return {"status": "ok", "service": "Pharox DX Engine Running"}

# Quitamos el 'async' para que requests corra perfecto en el threadpool de FastAPI
@app.post("/api/v1/consultar")
def consultar_copiloto(payload: ConsultaMedicaRequest):
    """
    Endpoint principal consumido por el Frontend (React).
    Recibe la consulta del médico y procesa la respuesta en Ollama.
    """
    prompt_sistema = f"""
    Actúa como un Copiloto Clínico Experto en Oncología de Precisión.
    Analiza la consulta médica relacionada con {payload.tipo_cancer}.
    
    Consulta Médica: "{payload.consulta}"
    
    Responde con rigor clínico, de forma concisa y estructurada.
    """
    
    payload_ia = {
        "model": "phi3",
        "prompt": prompt_sistema,
        "stream": False
    }
    
    try:
        # Usamos requests tal cual lo hiciste en pipeline_test.py
        response = requests.post(OLLAMA_URL, json=payload_ia, timeout=300.0)
        
        if response.status_code == 200:
            resultado = response.json().get("response", "")
            return {"success": True, "respuesta": resultado}
        else:
            raise HTTPException(status_code=500, detail=f"Ollama devolvió código {response.status_code}")
            
    except Exception as e:
        # Usamos repr(e) para que si hay un fallo, el mensaje nunca más se vea vacío
        raise HTTPException(status_code=500, detail=f"Error de conexión con Ollama: {repr(e)}")