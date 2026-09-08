"""
Tests de las funciones de normalización de app/gold/clinvar_to_neo4j.py:
JSON crudo de esummary (ClinVar) -> propiedades planas para Cypher.
"""
from app.gold.clinvar_to_neo4j import normalizar_variante_clinvar, normalizar_condicion_clinvar


class TestNormalizarVarianteClinVar:
    def test_extrae_campos_principales_variante_puntual(self):
        summary = {
            "uid": "39705",
            "obj_type": "single nucleotide variant",
            "title": "NM_006218.4(PIK3CA):c.3139C>T (p.His1047Tyr)",
            "protein_change": "H1047Y",
            "variation_set": [{
                "cdna_change": "c.3139C>T",
                "variation_loc": [
                    {"status": "current", "assembly_name": "GRCh38", "chr": "3", "display_start": "179234296"},
                    {"status": "previous", "assembly_name": "GRCh37", "chr": "3", "display_start": "178952084"},
                ],
            }],
            "genes": [{"symbol": "PIK3CA", "geneid": "5290"}],
            "germline_classification": {
                "description": "Pathogenic",
                "review_status": "criteria provided, multiple submitters, no conflicts",
                "last_evaluated": "2025/06/24 00:00",
                "trait_set": [{"trait_name": "Familial cancer of breast", "trait_xrefs": [{"db_source": "MedGen", "db_id": "C0346153"}]}],
            },
            "oncogenicity_classification": {"description": "", "trait_set": []},
            "clinical_impact_classification": {"description": "", "trait_set": []},
        }
        v = normalizar_variante_clinvar(summary)
        assert v["variation_id"] == 39705
        assert v["gen"] == "PIK3CA"
        assert v["hgvs_c"] == "c.3139C>T"
        assert v["hgvs_p"] == "H1047Y"
        assert v["assembly"] == "GRCh38"
        assert v["posicion"] == "179234296"
        assert v["clasificacion_clinica"] == "Pathogenic"

    def test_prefiere_oncogenicity_si_germline_vacia(self):
        summary = {
            "uid": "1",
            "variation_set": [{}],
            "genes": [],
            "germline_classification": {"description": "", "trait_set": []},
            "oncogenicity_classification": {"description": "Oncogenic", "review_status": "reviewed", "trait_set": []},
            "clinical_impact_classification": {"description": "", "trait_set": []},
        }
        v = normalizar_variante_clinvar(summary)
        assert v["clasificacion_clinica"] == "Oncogenic"

    def test_sin_ninguna_clasificacion_devuelve_no_clasificada(self):
        summary = {"uid": "2", "variation_set": [], "genes": []}
        v = normalizar_variante_clinvar(summary)
        assert v["clasificacion_clinica"] == "No clasificada"
        assert v["gen"] is None


class TestNormalizarCondicionClinVar:
    def test_extrae_nombre_y_medgen_id(self):
        trait = {"trait_name": "Hereditary breast ovarian cancer syndrome", "trait_xrefs": [{"db_source": "MedGen", "db_id": "C0677776"}, {"db_source": "Orphanet", "db_id": "145"}]}
        c = normalizar_condicion_clinvar(trait)
        assert c["nombre"] == "Hereditary breast ovarian cancer syndrome"
        assert c["medgen_id"] == "C0677776"

    def test_sin_trait_xrefs_medgen_id_es_none(self):
        trait = {"trait_name": "PIK3CA overgrowth syndrome", "trait_xrefs": []}
        c = normalizar_condicion_clinvar(trait)
        assert c["nombre"] == "PIK3CA overgrowth syndrome"
        assert c["medgen_id"] is None

    def test_sin_trait_name_devuelve_none(self):
        assert normalizar_condicion_clinvar({"trait_xrefs": []}) is None
