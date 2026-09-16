"""
Tests de las funciones de normalización de app/gold/cbioportal_to_neo4j.py:
JSON crudo de cBioPortal (AlterationCountByGene / info de estudio) -> propiedades
planas para Cypher.
"""
from app.gold.cbioportal_to_neo4j import normalizar_estudio, normalizar_frecuencia_gen


class TestNormalizarEstudio:
    def test_extrae_campos_principales(self):
        info = {
            "studyId": "brca_metabric",
            "name": "Breast Cancer (METABRIC, Nature 2012 & Nat Commun 2016)",
            "description": "Breast cancer cohort",
            "allSampleCount": 2509,
        }
        e = normalizar_estudio(info)
        assert e["study_id"] == "brca_metabric"
        assert e["n_pacientes"] == 2509

    def test_maneja_campos_ausentes(self):
        e = normalizar_estudio({})
        assert e["study_id"] is None
        assert e["nombre"] == ""
        assert e["n_pacientes"] == 0


class TestNormalizarFrecuenciaGen:
    def test_calcula_porcentaje_y_frecuencia_id(self):
        row = {
            "hugoGeneSymbol": "PIK3CA",
            "entrezGeneId": 5290,
            "numberOfAlteredCases": 975,
            "numberOfProfiledCases": 2433,
            "tipo_alteracion": "MUTATION",
        }
        f = normalizar_frecuencia_gen(row, "brca_metabric")
        assert f["frecuencia_id"] == "brca_metabric_5290_MUTATION"
        assert f["hugo_symbol"] == "PIK3CA"
        assert f["n_alterados"] == 975
        assert f["porcentaje"] == 40.07

    def test_frecuencia_id_distingue_tipo_alteracion(self):
        base = {"hugoGeneSymbol": "ERBB2", "entrezGeneId": 2064, "numberOfAlteredCases": 342, "numberOfProfiledCases": 2173}
        amp = normalizar_frecuencia_gen({**base, "tipo_alteracion": "AMPLIFICATION"}, "brca_metabric")
        mut = normalizar_frecuencia_gen({**base, "tipo_alteracion": "MUTATION"}, "brca_metabric")
        assert amp["frecuencia_id"] != mut["frecuencia_id"]

    def test_sin_perfilados_no_divide_por_cero(self):
        row = {"hugoGeneSymbol": "X", "entrezGeneId": 1, "numberOfAlteredCases": 0, "numberOfProfiledCases": 0, "tipo_alteracion": "MUTATION"}
        f = normalizar_frecuencia_gen(row, "study1")
        assert f["porcentaje"] == 0.0
