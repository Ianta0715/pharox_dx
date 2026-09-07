import os
from langchain_ollama import ChatOllama, OllamaEmbeddings

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
DEFAULT_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:8b")

def get_llm(model: str = DEFAULT_MODEL, temperature: float = 0.0) -> ChatOllama:
    """
    Inicializa y retorna la instancia de ChatOllama de LangChain.
    """
    return ChatOllama(
        model=model,
        base_url=OLLAMA_HOST,
        temperature=temperature
    )

def get_embeddings(model: str = "nomic-embed-text") -> OllamaEmbeddings:
    """
    Inicializa y retorna la instancia de OllamaEmbeddings de LangChain.
    """
    return OllamaEmbeddings(
        model=model,
        base_url=OLLAMA_HOST
    )

def llamar_modelo(prompt: str, model: str = DEFAULT_MODEL) -> str:
    """
    Genera una respuesta de texto utilizando ChatOllama de LangChain.
    Mantiene compatibilidad con firmas de llamadas anteriores.
    """
    llm = get_llm(model=model)
    response = llm.invoke(prompt)
    return response.content

def generar_embedding(texto: str, model: str = "nomic-embed-text") -> list[float]:
    """
    Genera el vector de embedding utilizando OllamaEmbeddings de LangChain.
    Mantiene compatibilidad con firmas de llamadas anteriores.
    """
    embeddings = get_embeddings(model=model)
    return embeddings.embed_query(texto)
