"""
Tests de las funciones de normalización de app/gold/vigilancia_protocolos_to_neo4j.py:
JSON crudo de Europe PMC -> propiedades planas para el nodo :ActualizacionProtocolo.
No requieren Neo4j.
"""
from app.gold.vigilancia_protocolos_to_neo4j import (
    normalizar_actualizacion,
    _detectar_subtipos,
    _detectar_sociedad,
)


class TestDetectarSubtipos:
    def test_triple_negativo(self):
        assert _detectar_subtipos("update on triple-negative breast cancer (tnbc) management") == ["Triple_negativo"]

    def test_her2_positivo(self):
        assert _detectar_subtipos("her2-positive breast cancer: new consensus") == ["HER2_positivo"]

    def test_rh_positivo_her2_negativo_requiere_ambas_senales(self):
        assert _detectar_subtipos("hormone receptor-positive, her2-negative disease") == ["RH_positivo_HER2_negativo"]

    def test_solo_hr_positivo_sin_her2_negativo_no_alcanza(self):
        # Sin mencion explicita de HER2-negativo no se puede asumir RH_positivo_HER2_negativo.
        assert _detectar_subtipos("hormone receptor-positive breast cancer") == ["desconocido"]

    def test_her2_low_no_se_detecta_como_her2_positivo(self):
        # HER2-low es clinicamente distinto de HER2 positivo clasico; no debe mezclarse.
        assert _detectar_subtipos("her2-low breast cancer and antibody-drug conjugates") == ["desconocido"]

    def test_multiples_subtipos_en_un_mismo_articulo(self):
        texto = "consensus update covering triple-negative and her2-positive breast cancer"
        assert _detectar_subtipos(texto) == ["HER2_positivo", "Triple_negativo"]

    def test_sin_senales_devuelve_desconocido(self):
        assert _detectar_subtipos("general oncology practice update") == ["desconocido"]


class TestDetectarSociedad:
    def test_por_nombre_de_revista_esmo(self):
        assert _detectar_sociedad("Annals of Oncology", "") == "ESMO"

    def test_por_nombre_de_revista_asco(self):
        assert _detectar_sociedad("Journal of Clinical Oncology", "") == "ASCO"

    def test_por_mencion_en_texto(self):
        assert _detectar_sociedad("Some Other Journal", "this is the updated nccn guideline") == "NCCN"

    def test_sin_senal_devuelve_otro(self):
        assert _detectar_sociedad("Unrelated Journal", "no society mentioned") == "otro"


class TestNormalizarActualizacion:
    def test_extrae_campos_principales(self):
        articulo = {
            "pmid": "40999999",
            "title": "ASCO Guideline Update: HER2-Positive Breast Cancer",
            "abstractText": "<h4>Purpose</h4>This ASCO update covers her2-positive disease management.",
            "journalInfo": {"journal": {"title": "Journal of Clinical Oncology"}},
            "firstPublicationDate": "2026-08-01",
        }
        a = normalizar_actualizacion(articulo)
        assert a["id"] == "40999999"
        assert "<h4>" not in a["resumen"]
        assert a["sociedad"] == "ASCO"
        assert a["fecha_publicacion"] == "2026-08-01"
        assert a["subtipos_detectados"] == "HER2_positivo"
        assert a["url"] == "https://pubmed.ncbi.nlm.nih.gov/40999999/"

    def test_usa_id_si_no_hay_pmid(self):
        a = normalizar_actualizacion({"id": "PPR000111", "title": "Preprint de guia sin PMID"})
        assert a["id"] == "PPR000111"
        assert a["url"] == ""

    def test_sin_journal_info_no_rompe(self):
        a = normalizar_actualizacion({"pmid": "1", "title": "Actualizacion sin journalInfo"})
        assert a["fuente"] == ""
        assert a["sociedad"] == "otro"
