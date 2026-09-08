"""
Tests de las funciones de normalización de app/gold/clinicaltrials_to_neo4j.py:
JSON crudo de ClinicalTrials.gov (protocolSection) -> propiedades planas para Cypher.
"""
from app.gold.clinicaltrials_to_neo4j import normalizar_ensayo


class TestNormalizarEnsayo:
    def test_extrae_campos_principales(self):
        study = {
            "protocolSection": {
                "identificationModule": {"nctId": "NCT04761055", "briefTitle": "Study of iBreast"},
                "statusModule": {"overallStatus": "RECRUITING"},
                "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Memorial Sloan Kettering"}},
                "descriptionModule": {"briefSummary": "Resumen del ensayo."},
                "conditionsModule": {"conditions": ["Breast Cancer", "HER2 Positive"]},
                "designModule": {"phases": ["PHASE2"]},
                "armsInterventionsModule": {"interventions": [{"name": "Trastuzumab"}, {"name": "Placebo"}]},
                "contactsLocationsModule": {"locations": [{"country": "United States"}, {"country": "Argentina"}]},
            }
        }
        e = normalizar_ensayo(study)
        assert e["nct_id"] == "NCT04761055"
        assert e["titulo"] == "Study of iBreast"
        assert e["estado"] == "RECRUITING"
        assert e["fases"] == ["PHASE2"]
        assert e["condiciones"] == ["Breast Cancer", "HER2 Positive"]
        assert e["intervenciones"] == ["Trastuzumab", "Placebo"]
        assert e["sponsor"] == "Memorial Sloan Kettering"
        assert e["paises"] == ["Argentina", "United States"]
        assert e["url"] == "https://clinicaltrials.gov/study/NCT04761055"

    def test_maneja_modulos_ausentes(self):
        study = {"protocolSection": {"identificationModule": {"nctId": "NCT00000001"}}}
        e = normalizar_ensayo(study)
        assert e["nct_id"] == "NCT00000001"
        assert e["titulo"] == ""
        assert e["fases"] == []
        assert e["condiciones"] == []
        assert e["intervenciones"] == []
        assert e["paises"] == []
        assert e["estado"] == "UNKNOWN"

    def test_deduplica_paises_repetidos(self):
        study = {
            "protocolSection": {
                "identificationModule": {"nctId": "NCT00000002"},
                "contactsLocationsModule": {"locations": [{"country": "Chile"}, {"country": "Chile"}]},
            }
        }
        e = normalizar_ensayo(study)
        assert e["paises"] == ["Chile"]
