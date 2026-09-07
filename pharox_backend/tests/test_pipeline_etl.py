"""
Tests de las funciones puras de app/pipeline_etl.py: anonimización,
cálculo de edad y extracción/normalización de recursos FHIR.
"""
from app.pipeline_etl import (
    aplicar_ceguera_tecnica,
    calcular_edad,
    normalizar_valor_unico,
    extraer_todos_los_recursos,
    buscar_y_validar_ontologia,
    deducir_nombre_droga,
)


class TestAplicarCegueraTecnica:
    def test_es_determinístico_con_mismo_salt(self):
        h1 = aplicar_ceguera_tecnica("patient-123", "salt-fijo")
        h2 = aplicar_ceguera_tecnica("patient-123", "salt-fijo")
        assert h1 == h2

    def test_salt_distinto_da_hash_distinto(self):
        h1 = aplicar_ceguera_tecnica("patient-123", "salt-a")
        h2 = aplicar_ceguera_tecnica("patient-123", "salt-b")
        assert h1 != h2

    def test_id_real_no_aparece_en_el_hash(self):
        h = aplicar_ceguera_tecnica("Juan Perez DNI 12345678", "salt")
        assert "Juan" not in h
        assert "12345678" not in h

    def test_devuelve_hash_sha256_hex(self):
        h = aplicar_ceguera_tecnica("patient-123", "salt")
        assert len(h) == 64
        int(h, 16)  # no debe lanzar ValueError si es hex válido

    def test_id_vacio_devuelve_anonimo(self):
        assert aplicar_ceguera_tecnica(None, "salt") == "ANONIMO"
        assert aplicar_ceguera_tecnica("", "salt") == "ANONIMO"


class TestCalcularEdad:
    def test_calcula_edad_correcta(self):
        from datetime import datetime
        hace_30_anios = datetime.now().year - 30
        fecha = f"{hace_30_anios}-01-01"
        edad = calcular_edad(fecha)
        assert edad in (29, 30)  # depende del día del año en que corra el test

    def test_fecha_none_devuelve_none(self):
        assert calcular_edad(None) is None

    def test_fecha_invalida_devuelve_none(self):
        assert calcular_edad("no-es-una-fecha") is None


class TestNormalizarValorUnico:
    def test_lista_devuelve_primer_elemento(self):
        assert normalizar_valor_unico(["Pembrolizumab", "otro"]) == "Pembrolizumab"

    def test_lista_vacia_devuelve_none(self):
        assert normalizar_valor_unico([]) is None

    def test_valor_none_devuelve_none(self):
        assert normalizar_valor_unico(None) is None

    def test_valor_escalar_se_convierte_a_string(self):
        assert normalizar_valor_unico(123) == "123"


class TestExtraerTodosLosRecursos:
    def test_extrae_entries_estandar(self):
        bundle = {"entry": [{"resource": {"resourceType": "Patient", "id": "p1"}}]}
        recursos = extraer_todos_los_recursos(bundle)
        assert len(recursos) == 1
        assert recursos[0]["resourceType"] == "Patient"

    def test_autosana_resource_type_faltante_por_fullurl(self):
        bundle = {"entry": [{"fullUrl": "urn:medication:1", "resource": {"id": "m1"}}]}
        recursos = extraer_todos_los_recursos(bundle)
        assert recursos[0]["resourceType"] == "MedicationRequest"

    def test_bundle_sin_entries_no_falla(self):
        assert extraer_todos_los_recursos({}) == []


class TestBuscarYValidarOntologia:
    def test_detecta_snomed(self):
        resource = {"code": {"coding": [{"code": "254837009", "display": "Breast Cancer", "system": "http://snomed.info/sct"}]}}
        codigo, descripcion, sistema = buscar_y_validar_ontologia(resource)
        assert codigo == "254837009"
        assert sistema == "SNOMED-CT"

    def test_detecta_cie10(self):
        resource = {"code": {"coding": [{"code": "C50", "display": "Malignant neoplasm of breast", "system": "http://hl7.org/fhir/sid/icd-10"}]}}
        _, _, sistema = buscar_y_validar_ontologia(resource)
        assert sistema == "CIE-10"

    def test_sin_coding_devuelve_desconocido(self):
        codigo, descripcion, sistema = buscar_y_validar_ontologia({})
        assert codigo is None
        assert sistema == "Desconocido"


class TestDeducirNombreDroga:
    def test_extrae_display_del_coding(self):
        resource = {"medicationCodeableConcept": {"coding": [{"display": "Pembrolizumab"}]}}
        assert deducir_nombre_droga(resource) == "Pembrolizumab"

    def test_fallback_a_text(self):
        resource = {"medicationCodeableConcept": {"text": "Esquema FOLFOX"}}
        assert deducir_nombre_droga(resource) == "Esquema FOLFOX"

    def test_sin_datos_devuelve_default(self):
        assert deducir_nombre_droga({}) == "Terapia Sistémica No Especificada"
