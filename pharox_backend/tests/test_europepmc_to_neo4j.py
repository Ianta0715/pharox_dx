"""
Tests de las funciones de normalización de app/gold/europepmc_to_neo4j.py:
JSON crudo de Europe PMC -> propiedades planas para el nodo :Literatura.
No requieren Ollama (normalizar_articulo no genera embeddings).
"""
from app.gold.europepmc_to_neo4j import normalizar_articulo, TIPO_CANCER_MAMA


class TestNormalizarArticulo:
    def test_extrae_campos_principales(self):
        articulo = {
            "pmid": "40779894",
            "title": "HER2 status changes after neoadjuvant trastuzumab in breast cancer.",
            "abstractText": "<h4>Background</h4>This study evaluates trastuzumab deruxtecan in HER2+ patients.",
            "meshHeadingList": {"meshHeading": [{"descriptorName": "Breast Neoplasms"}]},
        }
        a = normalizar_articulo(articulo)
        assert a["id"] == "40779894"
        assert "<h4>" not in a["text"]
        assert "Background" in a["text"]
        assert a["tipo_cancer"] == TIPO_CANCER_MAMA == "Cáncer de Mama"
        assert "HER2" in a["categoria"]
        assert "trastuzumab deruxtecan" in a["drogas"]
        assert "ADC" in a["categoria"]  # trastuzumab deruxtecan implica ADC

    def test_usa_id_si_no_hay_pmid(self):
        a = normalizar_articulo({"id": "PPR123456", "title": "Preprint sin PMID"})
        assert a["id"] == "PPR123456"

    def test_sin_categoria_reconocida_devuelve_general(self):
        a = normalizar_articulo({"pmid": "1", "title": "Estudio sobre otro tema", "abstractText": "Sin entidades reconocidas."})
        assert a["categoria"] == "General"
        assert a["drogas"] == ""

    def test_limpia_entidades_html_del_titulo(self):
        a = normalizar_articulo({"pmid": "2", "title": "HER2&lt;sup&gt;+&lt;/sup&gt; breast cancer"})
        assert "&lt;" not in a["text"]
        assert "<sup>" not in a["text"]

    def test_sin_pmid_ni_id_queda_vacio(self):
        a = normalizar_articulo({"title": "Sin identificador"})
        assert a["id"] == ""
