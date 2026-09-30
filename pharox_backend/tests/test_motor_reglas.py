"""
Motor de reglas (app/dominio/reglas): estados auditables sobre el catálogo.

Los casos clínicos de estos tests fijan el comportamiento esperado de cada
regla. Si un comité oncológico cambia un criterio, estos tests tienen que
cambiar con él (y VERSION_REGLAS subir).
"""
import pytest

from app.dominio.perfil import PerfilClinico
from app.dominio.reglas.catalogo import CATALOGO, VERSION_REGLAS
from app.dominio.reglas.motor import Estado, evaluar

MARIA = PerfilClinico(
    edad=54, re_pct=90, rp_pct=70, her2_ihq=2, ki67_pct=25, grado=2,
    t="T2", n="N1", m="M0", n_patologico=False, tamano_cm=3.2,
)


def _estados(perfil: PerfilClinico) -> dict[str, Estado]:
    return {o.opcion.id: o.estado for o in evaluar(perfil).opciones}


def _opcion(perfil: PerfilClinico, opcion_id: str):
    return next(o for o in evaluar(perfil).opciones if o.opcion.id == opcion_id)


class TestInvariantes:
    def test_ausencia_de_datos_nunca_descarta_una_opcion(self):
        # Con el perfil vacío nada puede ser NO: todo queda condicional a algún dato.
        assert set(_estados(PerfilClinico()).values()) == {Estado.CONDICIONAL}

    def test_ids_unicos_y_fuentes_citadas(self):
        assert len({o.id for o in CATALOGO}) == len(CATALOGO)
        for opcion in CATALOGO:
            assert opcion.fuente and opcion.criterios, opcion.id

    def test_toda_indicacion_metastasica_exige_el_escenario(self):
        for opcion in CATALOGO:
            if opcion.escenario == "metastasico":
                assert "m1" in {c.id for c in opcion.criterios}, opcion.id
            if opcion.escenario == "temprano" and opcion.tipo == "tratamiento":
                assert "m0" in {c.id for c in opcion.criterios}, opcion.id

    def test_la_version_viaja_en_la_evaluacion(self):
        assert evaluar(MARIA).version_reglas == VERSION_REGLAS


class TestCasoDeReferencia:
    """RE+ / HER2 IHQ 2+ sin ISH, cT2 cN1 M0 (estadio IIB), sin menopausia ni BRCA registrados."""

    def test_tdxd_no_aplica_en_enfermedad_temprana(self):
        # El error del catálogo del MVP: T-DXd quedaba "condicional al FISH" en un estadio IIB.
        for opcion_id in ("tdxd-her2-low-db04", "tdxd-her2-low-db06", "tdxd-her2-positivo-db03"):
            evaluada = _opcion(MARIA, opcion_id)
            assert evaluada.estado is Estado.NO_APLICA
            assert [c.id for c in evaluada.criterios_no_cumplidos] == ["m1"]

    def test_natalee_solo_depende_del_ish(self):
        evaluada = _opcion(MARIA, "ribociclib-natalee")
        assert evaluada.estado is Estado.CONDICIONAL
        assert evaluada.bloqueantes == ("her2_ish",)

    def test_monarche_espera_la_anatomia_patologica(self):
        assert "ganglios_patologicos" in _opcion(MARIA, "abemaciclib-monarche").bloqueantes

    def test_inhibidor_de_aromatasa_no_asume_posmenopausia_por_edad(self):
        evaluada = _opcion(MARIA, "ia-adyuvante")
        assert evaluada.estado is Estado.CONDICIONAL
        assert evaluada.bloqueantes == ("menopausia",)

    def test_tamoxifeno_cumple_con_independencia_de_la_menopausia(self):
        assert _opcion(MARIA, "tamoxifeno-adyuvante").estado is Estado.CUMPLE

    def test_estudios_indicados(self):
        estados = _estados(MARIA)
        assert estados["ish-her2"] is Estado.CUMPLE
        assert estados["test-germinal-brca"] is Estado.CUMPLE  # ≤65 años
        assert estados["estadificacion-sistemica"] is Estado.CUMPLE  # cN1

    def test_firma_genomica_con_nota_de_rxponder_y_menopausia(self):
        evaluada = _opcion(MARIA, "firma-genomica")
        assert evaluada.estado is Estado.CONDICIONAL
        assert any("RxPONDER" in n for n in evaluada.notas)
        assert any("menopáusico" in n for n in evaluada.notas)

    def test_el_ish_es_el_dato_de_mayor_valor(self):
        primero = evaluar(MARIA).valor_informacion[0]
        assert primero.dato == "her2_ish"
        assert "neo-anti-her2" in primero.opciones_que_decide

    def test_triple_negativo_descartado_por_re(self):
        assert _opcion(MARIA, "pembrolizumab-kn522").estado is Estado.NO_APLICA


class TestReglasPorEscenario:
    def test_her2_low_metastasico_tras_quimio_cumple_destiny_breast04(self):
        p = PerfilClinico(her2_ihq=1, re_pct=0, rp_pct=0, metastasico=True, quimio_previa_metastasico=True)
        assert _opcion(p, "tdxd-her2-low-db04").estado is Estado.CUMPLE

    def test_db06_exige_no_haber_recibido_quimio(self):
        p = PerfilClinico(her2_ihq=1, re_pct=90, metastasico=True, endocrino_previo_metastasico=True, quimio_previa_metastasico=True)
        assert _opcion(p, "tdxd-her2-low-db06").estado is Estado.NO_APLICA

    @pytest.mark.parametrize("t, n, estado", [
        ("T2", "N0", Estado.CUMPLE),
        ("T1c", "N1", Estado.CUMPLE),
        ("T1c", "N0", Estado.NO_APLICA),
        ("T2", "N3", Estado.NO_APLICA),
    ])
    def test_poblacion_keynote522(self, t, n, estado):
        p = PerfilClinico(re_pct=0, rp_pct=0, her2_ihq=0, t=t, n=n, m="M0")
        assert _opcion(p, "pembrolizumab-kn522").estado is estado

    @pytest.mark.parametrize("ganglios, estado", [(4, Estado.CUMPLE), (2, Estado.NO_APLICA)])
    def test_olympia_rh_positivo_cirugia_de_inicio(self, ganglios, estado):
        p = PerfilClinico(re_pct=90, her2_ihq=1, m="M0", brca_germinal="mutado", neoadyuvancia_previa=False, ganglios_positivos=ganglios)
        assert _opcion(p, "olaparib-olympia").estado is estado

    def test_olympia_rh_positivo_post_neoadyuvancia_exige_cps_eg(self):
        base = dict(re_pct=90, her2_ihq=1, m="M0", brca_germinal="mutado", neoadyuvancia_previa=True, respuesta_patologica_completa=False)
        assert _opcion(PerfilClinico(**base, cps_eg=3), "olaparib-olympia").estado is Estado.CUMPLE
        assert _opcion(PerfilClinico(**base, cps_eg=2), "olaparib-olympia").estado is Estado.NO_APLICA
        assert _opcion(PerfilClinico(**base), "olaparib-olympia").bloqueantes == ("cps_eg",)

    @pytest.mark.parametrize("perfil_extra, estado", [
        (dict(n="N2", n_patologico=True), Estado.CUMPLE),
        (dict(n="N1", n_patologico=True, grado=3), Estado.CUMPLE),
        (dict(n="N1", n_patologico=True, grado=2, tamano_cm=3.0), Estado.NO_APLICA),
        (dict(n="N0", n_patologico=True), Estado.NO_APLICA),
    ])
    def test_monarche_cohorte_1(self, perfil_extra, estado):
        p = PerfilClinico(re_pct=90, her2_ihq=0, m="M0", **perfil_extra)
        assert _opcion(p, "abemaciclib-monarche").estado is estado

    @pytest.mark.parametrize("perfil_extra, estado", [
        (dict(t="T2", n="N0", grado=3), Estado.CUMPLE),
        (dict(t="T2", n="N0", grado=2, ki67_pct=25), Estado.CUMPLE),
        (dict(t="T2", n="N0", grado=2, ki67_pct=10), Estado.CONDICIONAL),  # podría calificar por firma genómica
        (dict(t="T2", n="N0", grado=2, ki67_pct=10, recurrence_score=15), Estado.NO_APLICA),
        (dict(t="T2", n="N0", grado=1), Estado.NO_APLICA),
        (dict(t="T1", n="N0", grado=3), Estado.NO_APLICA),
    ])
    def test_poblacion_natalee(self, perfil_extra, estado):
        p = PerfilClinico(re_pct=90, her2_ihq=0, m="M0", **perfil_extra)
        assert _opcion(p, "ribociclib-natalee").estado is estado

    def test_inhibidor_pi3k_con_una_alteracion_alcanza(self):
        p = PerfilClinico(re_pct=90, her2_ihq=0, metastasico=True, endocrino_previo_metastasico=True, variantes={"AKT1": True})
        assert _opcion(p, "pi3k-akt-fulvestrant").estado is Estado.CUMPLE

    def test_inhibidor_pi3k_sin_alteraciones_estudiadas(self):
        p = PerfilClinico(re_pct=90, her2_ihq=0, metastasico=True, endocrino_previo_metastasico=True,
                          variantes={"PIK3CA": False, "AKT1": False, "PTEN": False})
        assert _opcion(p, "pi3k-akt-fulvestrant").estado is Estado.NO_APLICA

    def test_inhibidor_pi3k_pide_solo_los_genes_no_estudiados(self):
        p = PerfilClinico(re_pct=90, her2_ihq=0, metastasico=True, endocrino_previo_metastasico=True, variantes={"PIK3CA": False})
        assert _opcion(p, "pi3k-akt-fulvestrant").bloqueantes == ("gen_AKT1", "gen_PTEN")

    def test_test_germinal_ya_realizado_no_se_vuelve_a_ofrecer(self):
        assert _opcion(PerfilClinico(edad=40, brca_germinal="no_mutado"), "test-germinal-brca").estado is Estado.NO_APLICA
