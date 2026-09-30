"""
Dominio clínico puro de Pharox DX.

Nada de este paquete toca la red, Neo4j ni un LLM: recibe datos, devuelve
conclusiones. Es la parte que DECIDE (perfil, subtipo, escenario, elegibilidad
a opciones terapéuticas) y por eso tiene que ser reproducible y auditable: el
mismo perfil da siempre el mismo resultado, y cada resultado dice qué regla lo
produjo y con qué dato.
"""
