"""
Tests de las funciones puras de app/etl_casos_clinicos.py (sin OCR, sin LLM,
sin Neo4j): normalización de valores, generación de ID anonimizado y
resumen clínico para embeddings.
"""
from app.etl_casos_clinicos import _s, generar_id_caso, generar_resumen_clinico


class TestS:
    def test_none_devuelve_default(self):
        assert _s(None) == ""
        assert _s(None, "desconocido") == "desconocido"

    def test_convierte_a_string(self):
        assert _s(60) == "60"
        assert _s(True) == "True"

    def test_string_pasa_igual(self):
        assert _s("Luminal_A") == "Luminal_A"


class TestGenerarIdCaso:
    def test_es_determinístico(self):
        caso = {
            "datos_demograficos": {"sexo": "F", "edad": 60},
            "hallazgos_patologicos": [{"subtipo_molecular": "Luminal_A", "estadificacion": "pT1c"}],
        }
        assert generar_id_caso(caso) == generar_id_caso(caso)

    def test_devuelve_hash_de_24_caracteres(self):
        caso = {"datos_demograficos": {"sexo": "F", "edad": 60}, "hallazgos_patologicos": []}
        assert len(generar_id_caso(caso)) == 24

    def test_casos_distintos_dan_ids_distintos(self):
        caso_a = {
            "datos_demograficos": {"sexo": "F", "edad": 60},
            "hallazgos_patologicos": [{"subtipo_molecular": "Luminal_A", "estadificacion": "pT1c"}],
        }
        caso_b = {
            "datos_demograficos": {"sexo": "M", "edad": 45},
            "hallazgos_patologicos": [{"subtipo_molecular": "HER2_positivo", "estadificacion": "pT2"}],
        }
        assert generar_id_caso(caso_a) != generar_id_caso(caso_b)

    def test_limitacion_conocida_colision_por_perfil_clinico_identico(self):
        """
        LIMITACIÓN CONOCIDA (no un comportamiento deseado): el ID sale solo
        de sexo + edad + subtipo + estadificación, nunca de un identificador
        real del paciente. Dos pacientes reales distintos con el mismo
        perfil clínico exacto colisionan en el mismo caso_id, y el segundo
        pisaría (MERGE) el nodo del primero en vez de crear uno nuevo.
        Este test documenta el comportamiento actual; si se corrige el
        esquema de generación de ID, este test debe actualizarse.
        """
        perfil = {
            "datos_demograficos": {"sexo": "F", "edad": 60},
            "hallazgos_patologicos": [{"subtipo_molecular": "Luminal_A", "estadificacion": "pT1c"}],
        }
        paciente_1 = perfil
        paciente_2 = {
            "datos_demograficos": {"sexo": "F", "edad": 60},
            "hallazgos_patologicos": [{"subtipo_molecular": "Luminal_A", "estadificacion": "pT1c"}],
        }
        assert generar_id_caso(paciente_1) == generar_id_caso(paciente_2)


class TestGenerarResumenClinico:
    def test_incluye_datos_demograficos(self):
        caso = {"datos_demograficos": {"sexo": "F", "edad": 60, "imc_categoria": "normal"}}
        resumen = generar_resumen_clinico(caso)
        assert "F" in resumen
        assert "60" in resumen

    def test_no_falla_con_campos_faltantes(self):
        """Los datos que vienen del LLM extractor pueden traer null en cualquier campo."""
        caso = {"datos_demograficos": {}, "hallazgos_patologicos": [{"lateralidad": None}]}
        resumen = generar_resumen_clinico(caso)
        assert isinstance(resumen, str)
        assert resumen != ""

    def test_incluye_antecedentes_con_medicacion(self):
        caso = {
            "datos_demograficos": {},
            "antecedentes": [{"tipo": "hipertension", "medicacion": "enalapril"}],
        }
        resumen = generar_resumen_clinico(caso)
        assert "hipertension" in resumen
        assert "enalapril" in resumen

    def test_incluye_eventos_postoperatorios(self):
        caso = {
            "datos_demograficos": {},
            "eventos_postoperatorios": [{
                "tipo_evento": "seroma",
                "mama_afectada": "derecha",
                "detalle_resolucion": "drenaje",
                "resultado": "resolucion_completa",
            }],
        }
        resumen = generar_resumen_clinico(caso)
        assert "seroma" in resumen
        assert "resolucion_completa" in resumen
