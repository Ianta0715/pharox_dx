"""
Consultas generales: enrutamiento caso/general (app/dominio/consulta.py), léxico
de fármacos (app/dominio/farmacos.py) y recuperación por lo que nombra la
pregunta (conocimiento/recuperacion.recuperar_evidencia_general).
"""
import pytest

from app.conocimiento.evidencia import Fuente
from app.conocimiento.recuperacion import recuperar_evidencia_general
from app.dominio.consulta import interpretar
from app.dominio.farmacos import farmacos_mencionados, terminos_busqueda
from tests.fakes import LectorFalso
from tests.test_extraccion import CONSULTA_MARIA


class TestEnrutamiento:
    @pytest.mark.parametrize("pregunta", [
        "¿Qué evidencia clínica existe para la variante H1047R del gen PIK3CA?",
        "¿Cuántas pacientes reales del registro de tumores tienen subtipo triple negativo?",
        "¿Qué ensayos clínicos activos existen para cáncer de mama HER2 positivo metastásico?",
        "¿Cuál es el protocolo estándar para un perfil HER2 positivo?",
        "¿Qué dice la evidencia sobre T-DXd en HER2-low?",
    ])
    def test_preguntas_generales(self, pregunta):
        assert interpretar(pregunta).modo == "general"

    @pytest.mark.parametrize("pregunta", [
        CONSULTA_MARIA,
        "Paciente de 45 años con cáncer de mama triple negativo, ¿qué opciones hay?",
        "Mujer de 67 años, metastásica ósea, progresó a letrozol + palbociclib, PIK3CA mutado, HER2 1+",
        "RE 0%, RP 0%, HER2 1+, cT2 cN0. ¿KEYNOTE-522?",
    ])
    def test_casos(self, pregunta):
        assert interpretar(pregunta).modo == "caso"

    def test_triple_negativo_es_una_sola_mencion(self):
        # Fija tres campos (RE, RP, HER2) pero sale de un solo fragmento del texto.
        assert interpretar("¿Cuántas pacientes triple negativo hay?").modo == "general"

    def test_perfil_estructurado_fuerza_caso(self):
        assert interpretar("¿Qué opciones hay?", hay_perfil_estructurado=True).modo == "caso"

    def test_modo_pedido_explicitamente(self):
        assert interpretar(CONSULTA_MARIA, "general").modo == "general"

    def test_temas(self):
        temas = interpretar("¿Qué ensayos reclutando y guías ESMO hay para el registro?").temas
        assert temas == {"ensayos", "guias", "registro"}


class TestFarmacos:
    def test_reconoce_nombres_abreviaturas_y_tildes(self):
        texto = "¿T-DXd o sacituzumab govitecán tras letrozol? ¿Y trastuzumab emtansina?"
        assert set(farmacos_mencionados(texto)) == {"t-dxd", "sacituzumab", "letrozol", "t-dm1"}

    def test_terminos_en_ingles_para_la_base(self):
        assert terminos_busqueda(("t-dxd", "letrozol", "olaparib")) == ("trastuzumab deruxtecan", "letrozole", "olaparib")

    def test_sin_farmacos(self):
        assert farmacos_mencionados("¿Qué es el Ki-67?") == ()


def _general(pregunta, lector=None):
    interp = interpretar(pregunta)
    return recuperar_evidencia_general(
        lector or LectorFalso(), interp.extraccion.perfil, terminos_busqueda(interp.farmacos), interp.temas, pregunta
    )


def _fuentes(resultado):
    return {e.fuente for e in resultado.evidencias}


class TestRecuperacionGeneral:
    def test_pregunta_sin_senales_no_trae_ruido(self):
        r = _general("¿Qué es el índice de proliferación?")
        assert r.evidencias == ()
        assert r.fuentes_consultadas == ()

    def test_gen_mencionado(self):
        r = _general("¿Qué evidencia hay para PIK3CA H1047R?")
        assert {Fuente.CIVIC, Fuente.CBIOPORTAL} <= _fuentes(r)
        assert all(e.opciones == () for e in r.evidencias)

    def test_farmaco_mencionado_trae_solo_ensayos_que_lo_nombran(self):
        r = _general("¿Qué ensayos con trastuzumab y pertuzumab hay?")
        assert Fuente.CIVIC in _fuentes(r)
        ensayos = [e for e in r.evidencias if e.fuente is Fuente.ENSAYO]
        assert {e.referencias[0] for e in ensayos} == {"NCT00000003"}
        assert all("descarta a la paciente" not in e.detalle for e in ensayos)

    def test_subtipo_acota_protocolos_y_registro(self):
        r = _general("¿Cuál es el protocolo estándar para triple negativo?")
        protocolos = [e for e in r.evidencias if e.fuente is Fuente.PROTOCOLO]
        assert [e.referencias for e in protocolos] == [("PROTO_04",)]
        assert Fuente.REGISTRO in _fuentes(r)

    def test_escenario_metastasico_filtra_ensayos(self):
        r = _general("¿Qué ensayos hay en enfermedad metastásica RH positivo?")
        ncts = {e.referencias[0] for e in r.evidencias if e.fuente is Fuente.ENSAYO}
        assert "NCT00000002" in ncts and "NCT00000001" not in ncts
