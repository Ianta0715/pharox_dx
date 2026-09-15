"""
Tests de las funciones puras de app/gold/reglas_elegibilidad_trials.py:
paso determinístico, parseo de la respuesta semántica del LLM, y validación
de veredicto antes de tocar Cypher. No requieren Neo4j ni Ollama.
"""
import pytest

from app.gold.reglas_elegibilidad_trials import (
    evaluar_criterios_deterministicos,
    _parsear_respuesta_semantica,
    aplicar_veredicto,
)


def _paciente(**overrides) -> dict:
    base = {
        "id": "RT_0001",
        "edad": 55,
        "sexo": "Mujer",
        "subtipo_molecular": "HER2_positivo",
        "estadio_clinico": "IIB",
        "ecog": "0-Actividad Normal",
        "receptor_estrogeno": "(+)",
        "receptor_progesterona": "(+)",
        "her2": "(+)",
    }
    base.update(overrides)
    return base


def _ensayo(**overrides) -> dict:
    base = {
        "nct_id": "NCT00000001",
        "estado": "RECRUITING",
        "sexo": "ALL",
        "edad_minima_anios": None,
        "edad_maxima_anios": None,
        "subtipos_relacionados": ["HER2_positivo"],
        "criterios_elegibilidad": "Inclusion Criteria:\n* HER2-positive breast cancer",
    }
    base.update(overrides)
    return base


class TestEvaluarCriteriosDeterministicos:
    def test_excluye_si_no_reclutando(self):
        r = evaluar_criterios_deterministicos(_paciente(), _ensayo(estado="COMPLETED"))
        assert r["veredicto"] == "EXCLUYE"
        assert r["regla"] == "estado_reclutamiento"

    def test_excluye_por_sexo(self):
        r = evaluar_criterios_deterministicos(_paciente(sexo="Hombre"), _ensayo(sexo="FEMALE"))
        assert r["veredicto"] == "EXCLUYE"
        assert r["regla"] == "sexo"

    def test_no_excluye_por_sexo_si_ensayo_acepta_todos(self):
        r = evaluar_criterios_deterministicos(_paciente(sexo="Hombre"), _ensayo(sexo="ALL"))
        assert r["veredicto"] != "EXCLUYE" or r["regla"] != "sexo"

    def test_excluye_por_edad_minima(self):
        r = evaluar_criterios_deterministicos(_paciente(edad=17), _ensayo(edad_minima_anios=18))
        assert r["veredicto"] == "EXCLUYE"
        assert r["regla"] == "edad_minima"

    def test_excluye_por_edad_maxima(self):
        r = evaluar_criterios_deterministicos(_paciente(edad=80), _ensayo(edad_maxima_anios=75))
        assert r["veredicto"] == "EXCLUYE"
        assert r["regla"] == "edad_maxima"

    def test_excluye_por_subtipo_molecular_no_coincide(self):
        r = evaluar_criterios_deterministicos(
            _paciente(subtipo_molecular="Triple_negativo"),
            _ensayo(subtipos_relacionados=["HER2_positivo"]),
        )
        assert r["veredicto"] == "EXCLUYE"
        assert r["regla"] == "subtipo_molecular"

    def test_condiciona_si_subtipo_paciente_desconocido(self):
        # No se puede excluir a alguien por un dato que no se tiene.
        r = evaluar_criterios_deterministicos(
            _paciente(subtipo_molecular="desconocido"),
            _ensayo(subtipos_relacionados=["HER2_positivo"]),
        )
        assert r["veredicto"] == "CONDICIONA"

    def test_condiciona_si_ensayo_no_especifica_subtipo(self):
        r = evaluar_criterios_deterministicos(
            _paciente(subtipo_molecular="Triple_negativo"),
            _ensayo(subtipos_relacionados=[]),
        )
        assert r["veredicto"] == "CONDICIONA"

    def test_condiciona_si_todo_pasa(self):
        r = evaluar_criterios_deterministicos(_paciente(), _ensayo())
        assert r["veredicto"] == "CONDICIONA"
        assert r["criterio_pendiente"] is not None

    def test_nunca_emite_habilita(self):
        # El paso determinístico nunca confirma elegibilidad completa por diseño.
        casos = [
            (_paciente(), _ensayo()),
            (_paciente(edad=30), _ensayo(edad_minima_anios=18, edad_maxima_anios=75)),
        ]
        for paciente, ensayo in casos:
            assert evaluar_criterios_deterministicos(paciente, ensayo)["veredicto"] != "HABILITA"


class TestParsearRespuestaSemantica:
    def test_parsea_json_valido(self):
        r = _parsear_respuesta_semantica('{"veredicto": "HABILITA", "motivo": "Cumple todo.", "criterio_pendiente": null}')
        assert r == {"veredicto": "HABILITA", "motivo": "Cumple todo.", "regla": "semantico_llm", "criterio_pendiente": None}

    def test_parsea_json_con_bloque_markdown(self):
        contenido = '```json\n{"veredicto": "CONDICIONA", "motivo": "Falta dato.", "criterio_pendiente": "ECOG"}\n```'
        r = _parsear_respuesta_semantica(contenido)
        assert r["veredicto"] == "CONDICIONA"
        assert r["criterio_pendiente"] == "ECOG"

    def test_parsea_json_con_texto_extra_alrededor(self):
        contenido = 'Acá está mi respuesta:\n{"veredicto": "EXCLUYE", "motivo": "No cumple."}\nListo.'
        r = _parsear_respuesta_semantica(contenido)
        assert r["veredicto"] == "EXCLUYE"

    def test_veredicto_no_reconocido_cae_a_condiciona(self):
        r = _parsear_respuesta_semantica('{"veredicto": "TAL_VEZ", "motivo": "ambiguo"}')
        assert r["veredicto"] == "CONDICIONA"
        assert r["regla"] == "veredicto_invalido_llm"

    def test_json_invalido_cae_a_condiciona(self):
        r = _parsear_respuesta_semantica("esto no es json")
        assert r["veredicto"] == "CONDICIONA"
        assert r["regla"] == "error_parseo_llm"

    def test_motivo_ausente_no_rompe(self):
        r = _parsear_respuesta_semantica('{"veredicto": "habilita"}')
        # case-insensitive: "habilita" -> "HABILITA"
        assert r["veredicto"] == "HABILITA"
        assert r["motivo"]


class TestAplicarVeredicto:
    def test_veredicto_invalido_nunca_toca_neo4j(self):
        # tx=None: si llegara a intentar tx.run, explotaria con AttributeError en vez
        # de ValueError -- este test confirma que la validacion corta ANTES de eso.
        with pytest.raises(ValueError):
            aplicar_veredicto(None, "RT_0001", "NCT00000001", {"veredicto": "QUIZAS", "motivo": "x", "regla": "y"})
