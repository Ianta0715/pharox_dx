"""
Capa de conocimiento: acceso al grafo Neo4j y recuperación de evidencia tipada.

Esta capa NO decide nada clínico. Recibe lo que el motor de reglas ya resolvió
(subtipos compatibles, escenario, opciones abiertas, genes y drogas en juego)
y trae del grafo la evidencia que respalda o contextualiza esas opciones.
"""
