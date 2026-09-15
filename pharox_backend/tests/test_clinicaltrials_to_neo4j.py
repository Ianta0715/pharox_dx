"""
Tests de las funciones de normalización de app/gold/clinicaltrials_to_neo4j.py:
JSON crudo de ClinicalTrials.gov (protocolSection) -> propiedades planas para Cypher.
"""
from app.gold.clinicaltrials_to_neo4j import normalizar_ensayo, _detectar_subtipos_ensayo, _parsear_edad_anios


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
                "eligibilityModule": {
                    "eligibilityCriteria": "Inclusion Criteria:\n* HER2-positive breast cancer\n* ECOG 0-1",
                    "sex": "FEMALE",
                    "minimumAge": "18 Years",
                    "maximumAge": "75 Years",
                    "healthyVolunteers": False,
                },
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
        assert e["sexo"] == "FEMALE"
        assert e["edad_minima_anios"] == 18
        assert e["edad_maxima_anios"] == 75
        assert e["acepta_voluntarios_sanos"] is False
        assert "HER2-positive" in e["criterios_elegibilidad"]
        assert e["subtipos_relacionados"] == ["HER2_positivo"]

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
        assert e["criterios_elegibilidad"] == ""
        assert e["sexo"] == "ALL"
        assert e["edad_minima_anios"] is None
        assert e["edad_maxima_anios"] is None
        assert e["acepta_voluntarios_sanos"] is None
        assert e["subtipos_relacionados"] == []

    def test_deduplica_paises_repetidos(self):
        study = {
            "protocolSection": {
                "identificationModule": {"nctId": "NCT00000002"},
                "contactsLocationsModule": {"locations": [{"country": "Chile"}, {"country": "Chile"}]},
            }
        }
        e = normalizar_ensayo(study)
        assert e["paises"] == ["Chile"]


class TestParsearEdadAnios:
    def test_edad_valida(self):
        assert _parsear_edad_anios("18 Years") == 18

    def test_singular_year(self):
        assert _parsear_edad_anios("1 Year") == 1

    def test_ausente(self):
        assert _parsear_edad_anios(None) is None
        assert _parsear_edad_anios("") is None

    def test_no_disponible(self):
        assert _parsear_edad_anios("N/A") is None

    def test_unidad_no_anios_no_se_convierte(self):
        assert _parsear_edad_anios("6 Months") is None


class TestDetectarSubtiposEnsayo:
    def test_her2_positivo(self):
        assert _detectar_subtipos_ensayo("her2-positive breast cancer trial") == ["HER2_positivo"]

    def test_triple_negativo(self):
        assert _detectar_subtipos_ensayo("study in triple-negative breast cancer (tnbc)") == ["Triple_negativo"]

    def test_rh_positivo_her2_negativo_requiere_ambas_senales(self):
        assert _detectar_subtipos_ensayo("hormone receptor-positive, her2-negative disease") == ["RH_positivo_HER2_negativo"]

    def test_sin_senal_devuelve_lista_vacia(self):
        # A diferencia de vigilancia_protocolos_to_neo4j, "sin señal" es [] (no restringe),
        # no ["desconocido"] -- ver nota en clinicaltrials_to_neo4j.py.
        assert _detectar_subtipos_ensayo("general breast cancer treatment study") == []
