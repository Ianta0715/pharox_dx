"""Derivaciones del perfil clínico (app/dominio/perfil.py): nada se infiere de un dato ausente."""
import pytest

from app.dominio import perfil as P
from app.dominio.perfil import PerfilClinico, fusionar
from app.dominio.tri import Tri

SI, NO, IND = Tri.SI, Tri.NO, Tri.INDETERMINADO


class TestReceptores:
    def test_positivo_desde_1_por_ciento(self):
        assert P.re_positivo(PerfilClinico(re_pct=1)) is SI
        assert P.re_positivo(PerfilClinico(re_pct=0)) is NO

    def test_porcentaje_tiene_prioridad_sobre_estado(self):
        assert P.re_positivo(PerfilClinico(re_pct=0, re="positivo")) is NO

    def test_ausente_es_indeterminado(self):
        assert P.rh_positivo(PerfilClinico()) is IND

    def test_rh_positivo_si_alcanza_rp(self):
        assert P.rh_positivo(PerfilClinico(re_pct=0, rp_pct=20)) is SI


class TestHer2:
    @pytest.mark.parametrize("ihq, ish, estado", [
        (3, None, "positivo"),
        (2, "amplificado", "positivo"),
        (2, "no_amplificado", "negativo"),
        (2, None, None),  # equívoco sin ISH: sin determinar, NO negativo
        (1, None, "negativo"),
        (0, None, "negativo"),
    ])
    def test_estado(self, ihq, ish, estado):
        assert P.her2_estado(PerfilClinico(her2_ihq=ihq, her2_ish=ish)) == estado

    @pytest.mark.parametrize("perfil, esperado", [
        (PerfilClinico(her2_ihq=1), SI),
        (PerfilClinico(her2_ihq=2, her2_ish="no_amplificado"), SI),
        (PerfilClinico(her2_ihq=2), IND),
        (PerfilClinico(her2_ihq=0), NO),
        (PerfilClinico(her2_ihq=3), NO),
        (PerfilClinico(her2_informado="negativo"), IND),  # "negativo" sin score no distingue 0 de low
        (PerfilClinico(her2_low_informado=True), SI),
    ])
    def test_her2_low(self, perfil, esperado):
        assert P.her2_low(perfil) is esperado


class TestTripleNegativo:
    def test_re_positivo_descarta_sin_esperar_el_resto(self):
        assert P.triple_negativo(PerfilClinico(re_pct=80)) is NO

    def test_completo(self):
        assert P.triple_negativo(PerfilClinico(re_pct=0, rp_pct=0, her2_ihq=1)) is SI

    def test_her2_equivoco_lo_deja_indeterminado(self):
        assert P.triple_negativo(PerfilClinico(re_pct=0, rp_pct=0, her2_ihq=2)) is IND


class TestEstadificacion:
    @pytest.mark.parametrize("t, n, m, esperado", [
        ("T2", "N1", "M0", "IIB"),
        ("T2", "N0", None, "IIA"),
        ("T1c", "N1", None, "IIA"),
        ("T1", "N0", None, "IA"),
        ("T3", "N0", None, "IIB"),
        ("T3", "N1", None, "IIIA"),
        ("T4b", "N1", None, "IIIB"),
        ("T2", "N3", None, "IIIC"),
        ("T2", "N1", "M1", "IV"),
        ("Tis", "N0", None, "0"),
        (None, "N1", None, None),
    ])
    def test_estadio_anatomico(self, t, n, m, esperado):
        assert P.estadio_anatomico(t, n, m) == esperado

    def test_estadio_explicito_tiene_prioridad(self):
        assert P.estadio_efectivo(PerfilClinico(t="T2", n="N1", estadio="IIIA")) == "IIIA"

    def test_metastasico_no_se_deduce_de_un_estadio_calculado_sin_m(self):
        assert P.metastasico(PerfilClinico(t="T2", n="N1")) is IND

    def test_metastasico_por_m_o_estadio_explicito(self):
        assert P.metastasico(PerfilClinico(m="M0")) is NO
        assert P.metastasico(PerfilClinico(estadio="IV")) is SI
        assert P.metastasico(PerfilClinico(estadio="IIB")) is NO

    @pytest.mark.parametrize("perfil, rango", [
        (PerfilClinico(ganglios_positivos=2), (2, 2)),
        (PerfilClinico(n="N1", n_patologico=True), (1, 3)),
        (PerfilClinico(n="N2", n_patologico=True), (4, 9)),
        (PerfilClinico(n="N1", n_patologico=False), None),  # cN1 no da conteo
    ])
    def test_rango_ganglios(self, perfil, rango):
        assert P.rango_ganglios_positivos(perfil) == rango


class TestSubtipo:
    def test_subtipo_determinado(self):
        assert P.subtipo_molecular(PerfilClinico(re_pct=90, rp_pct=70, her2_ihq=1)) == P.SUBTIPO_RH_POSITIVO_HER2_NEGATIVO

    def test_her2_equivoco_con_re_positivo_deja_dos_compatibles(self):
        p = PerfilClinico(re_pct=90, rp_pct=70, her2_ihq=2)
        assert P.subtipo_molecular(p) is None
        assert set(P.subtipos_compatibles(p)) == {P.SUBTIPO_HER2_POSITIVO, P.SUBTIPO_RH_POSITIVO_HER2_NEGATIVO}

    def test_perfil_vacio_no_descarta_ningun_subtipo(self):
        assert set(P.subtipos_compatibles(PerfilClinico())) == set(P.SUBTIPOS)


class TestFusionar:
    def test_estructurado_pisa_texto_y_combina_variantes(self):
        texto = PerfilClinico(edad=54, her2_ihq=2, variantes={"PIK3CA": True})
        estructurado = PerfilClinico(her2_ish="no_amplificado", edad=55, variantes={"ESR1": False})
        f = fusionar(texto, estructurado)
        assert f.edad == 55
        assert f.her2_ihq == 2
        assert f.her2_ish == "no_amplificado"
        assert f.variantes == {"PIK3CA": True, "ESR1": False}


class TestDatosFaltantes:
    def test_todas_las_claves_documentadas_se_pueden_evaluar(self):
        for clave in P.ETIQUETAS_DATOS:
            assert P.falta_dato(PerfilClinico(), clave) in (True, False)

    def test_clave_desconocida_falla_ruidosamente(self):
        with pytest.raises(KeyError):
            P.falta_dato(PerfilClinico(), "dato_inventado")
