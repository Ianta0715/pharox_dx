"""
Tests de las funciones puras de app/graph_db.py que no requieren una
conexión real a Neo4j.
"""
from app.graph_db import _extraer_palabras_clave


class TestExtraerPalabrasClave:
    def test_extrae_palabras_relevantes(self):
        palabras = _extraer_palabras_clave("¿Qué evidencia hay para BRAF V600E?")
        assert "braf" in palabras
        assert "v600e" in palabras

    def test_filtra_stopwords(self):
        palabras = _extraer_palabras_clave("¿Qué evidencia hay para el cáncer de mama?")
        assert "para" not in palabras
        assert "cáncer" not in palabras
        assert "qué" not in palabras

    def test_filtra_palabras_cortas(self):
        palabras = _extraer_palabras_clave("hay un gen ahi")
        assert all(len(p) >= 4 for p in palabras)

    def test_texto_vacio_devuelve_lista_vacia(self):
        assert _extraer_palabras_clave("") == []

    def test_none_devuelve_lista_vacia(self):
        assert _extraer_palabras_clave(None) == []

    def test_normaliza_a_minusculas(self):
        palabras = _extraer_palabras_clave("BRAF Melanoma")
        assert "braf" in palabras
        assert "melanoma" in palabras

    def test_quita_puntuacion(self):
        palabras = _extraer_palabras_clave("¿variante BRAF?")
        assert "braf" in palabras
        assert "braf?" not in palabras
