"""
Capa de lenguaje natural. El LLM local ya no decide nada:

- redaccion.py: convierte el dossier (resuelto por el motor + evidencia del
  grafo) en prosa para el médico. Es opcional: sin Ollama, la respuesta es el
  dossier determinístico.
- verificacion.py: chequeo determinístico de la redacción contra el dossier.
- text_to_cypher.py: explorador de solo lectura del grafo (/api/v1/explorar),
  fuera del camino de decisión clínica.
"""
