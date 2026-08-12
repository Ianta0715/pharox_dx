import os
import requests

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")

def llamar_modelo(prompt: str, model: str = "qwen2.5:1.5b") -> str:
    """
    Abstracción para realizar llamadas de generación a Ollama/LLMs.
    Retorna la respuesta en formato de texto.
    """
    url = f"{OLLAMA_HOST}/api/generate"
    payload_ia = {
        "model": model,
        "prompt": prompt,
        "stream": False
    }
    
    try:
        response = requests.post(url, json=payload_ia, timeout=300.0)
        
        if response.status_code == 200:
            return response.json().get("response", "")
        else:
            raise RuntimeError(f"Ollama devolvió código de estado {response.status_code}: {response.text}")
            
    except Exception as e:
        raise ConnectionError(f"Error de conexión con Ollama en {url}: {repr(e)}")

def generar_embedding(texto: str, model: str = "nomic-embed-text") -> list[float]:
    """
    Genera el vector de embedding de 768 dimensiones para un texto usando Ollama.
    """
    url = f"{OLLAMA_HOST}/api/embeddings"
    payload = {
        "model": model,
        "prompt": texto
    }
    
    try:
        response = requests.post(url, json=payload, timeout=60.0)
        
        if response.status_code == 200:
            embedding = response.json().get("embedding")
            if embedding:
                return embedding
            else:
                # Intentar formato alternativo si la API de Ollama responde de otra forma
                embeddings = response.json().get("embeddings")
                if embeddings and isinstance(embeddings, list):
                    return embeddings[0]
                raise ValueError("La respuesta de Ollama no contiene el campo 'embedding' ni 'embeddings'.")
        else:
            raise RuntimeError(f"Ollama embeddings devolvió código de estado {response.status_code}: {response.text}")
            
    except Exception as e:
        raise ConnectionError(f"Error de conexión con Ollama Embeddings en {url}: {repr(e)}")
