"""
Tests del filtro de seguridad Cypher (app/main.py). Es la única barrera
entre lo que el LLM genera y lo que se ejecuta contra Neo4j: si esto falla,
un Cypher de escritura generado por el modelo podría ejecutarse.
"""
import pytest

from app.main import clean_cypher_output, validate_cypher


class TestCleanCypherOutput:
    def test_quita_bloque_markdown_con_lenguaje(self):
        texto = "```cypher\nMATCH (n) RETURN n\n```"
        assert clean_cypher_output(texto) == "MATCH (n) RETURN n"

    def test_quita_bloque_markdown_sin_lenguaje(self):
        texto = "```\nMATCH (n) RETURN n\n```"
        assert clean_cypher_output(texto) == "MATCH (n) RETURN n"

    def test_sin_markdown_no_cambia(self):
        texto = "MATCH (n) RETURN n"
        assert clean_cypher_output(texto) == texto

    def test_recorta_espacios(self):
        assert clean_cypher_output("   MATCH (n) RETURN n   ") == "MATCH (n) RETURN n"


class TestValidateCypher:
    @pytest.mark.parametrize("query", [
        "MATCH (n) RETURN n",
        "MATCH (p:Paciente)-[:DIAGNOSTICADO_CON]->(t:Tumor) RETURN p, t",
        "MATCH (v:Variante {gen: 'BRAF'}) RETURN v LIMIT 10",
    ])
    def test_permite_consultas_de_solo_lectura(self, query):
        assert validate_cypher(query) == query

    @pytest.mark.parametrize("palabra_prohibida", [
        "DELETE", "CREATE", "MERGE", "SET", "REMOVE", "DETACH",
    ])
    def test_bloquea_cada_palabra_prohibida(self, palabra_prohibida):
        query = f"MATCH (n) {palabra_prohibida} n.hackeado = true"
        with pytest.raises(ValueError, match=palabra_prohibida):
            validate_cypher(query)

    def test_bloquea_sin_importar_mayusculas_minusculas(self):
        with pytest.raises(ValueError):
            validate_cypher("match (n) delete n")

    def test_bloquea_detach_delete(self):
        # Contiene tanto "DELETE" como "DETACH"; el filtro corta en la
        # primera palabra prohibida que encuentra (DELETE va antes en la
        # lista) — lo importante es que quede bloqueado de cualquier forma.
        with pytest.raises(ValueError, match="DELETE|DETACH"):
            validate_cypher("MATCH (n) DETACH DELETE n")

    def test_limpia_markdown_antes_de_validar(self):
        query = "```cypher\nMATCH (n) RETURN n\n```"
        assert validate_cypher(query) == "MATCH (n) RETURN n"
