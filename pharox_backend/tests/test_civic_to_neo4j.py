"""
Tests de las funciones de normalización de app/gold/civic_to_neo4j.py:
JSON crudo de CIViC -> estructuras planas para Cypher.
"""
from app.gold.civic_to_neo4j import (
    normalizar_variante,
    normalizar_evidencia,
    normalizar_enfermedad,
    normalizar_terapia,
    normalizar_fuente,
)


class TestNormalizarVariante:
    def test_extrae_campos_principales(self):
        data = {
            "id": 12,
            "name": "V600E",
            "link": "/variants/12",
            "deprecated": False,
            "variantTypes": [{"name": "missense_variant"}],
            "feature": {"name": "BRAF", "featureInstance": {"entrezId": 673}},
            "singleVariantMolecularProfile": {"id": 99, "name": "BRAF V600E", "molecularProfileScore": 500.0, "description": "desc"},
        }
        v = normalizar_variante(data)
        assert v["civic_id"] == 12
        assert v["gen"] == "BRAF"
        assert v["nombre_variante"] == "V600E"
        assert v["gene_entrez_id"] == 673
        assert v["link"] == "https://civicdb.org/variants/12"
        assert v["tipos_so"] == ["missense_variant"]
        assert v["perfil_molecular_score"] == 500.0

    def test_maneja_feature_y_perfil_ausentes(self):
        data = {"id": 1, "name": "X"}
        v = normalizar_variante(data)
        assert v["gen"] is None
        assert v["perfil_molecular_id"] is None
        assert v["tipos_so"] == []


class TestNormalizarEvidencia:
    def test_extrae_campos_principales(self):
        ev = {
            "id": 5, "name": "EID5", "description": "desc",
            "evidenceType": "Predictive", "evidenceLevel": "B",
            "significance": "Sensitivity/Response",
        }
        r = normalizar_evidencia(ev)
        assert r["civic_id"] == 5
        assert r["tipo"] == "Predictive"
        assert r["nivel"] == "B"
        assert r["significancia"] == "Sensitivity/Response"


class TestNormalizarEnfermedad:
    def test_extrae_campos_principales(self):
        disease = {"id": 3, "name": "Melanoma", "displayName": "Melanoma", "doid": "1909"}
        r = normalizar_enfermedad(disease)
        assert r["civic_id"] == 3
        assert r["nombre_mostrado"] == "Melanoma"
        assert r["doid"] == "1909"


class TestNormalizarTerapia:
    def test_extrae_campos_principales(self):
        therapy = {"id": 7, "name": "Vemurafenib", "ncitId": "C64768"}
        r = normalizar_terapia(therapy)
        assert r["civic_id"] == 7
        assert r["nombre"] == "Vemurafenib"
        assert r["ncit_id"] == "C64768"


class TestNormalizarFuente:
    def test_extrae_campos_principales(self):
        source = {"id": 9, "citationId": "12345", "sourceType": "PUBMED", "citation": "cita", "publicationYear": 2020, "journal": "Nature", "sourceUrl": "http://x"}
        r = normalizar_fuente(source)
        assert r["civic_id"] == 9
        assert r["pubmed_id"] == "12345"
        assert r["anio"] == 2020
