"""Extracción determinística texto → perfil (app/dominio/extraccion.py)."""
from app.dominio import perfil as P
from app.dominio.extraccion import extraer_perfil

CONSULTA_MARIA = (
    "Paciente de 54 años, CDI cT2 cN1 M0, RE 90%, RP 70%, HER2 IHQ 2+ sin ISH, Ki-67 25%, G2, "
    "CA 15-3 en 32 y TC de estadificación pendiente. Estoy definiendo neoadyuvancia vs. cirugía de inicio.\n"
    "1) Si el ISH da no amplificado, ¿T-DXd tiene lugar en este escenario no metastásico? "
    "Y si amplifica, ¿cambia la secuencia a neoadyuvancia con doble bloqueo?\n"
    "2) Sin estado menopáusico registrado, ¿qué queda indeterminado (RxPONDER en N1 con RS ≤25) y para el "
    "CDK4/6 adyuvante (NATALEE vs. monarchE con 3,2 cm, G2 y Ki-67 25%)?\n"
    "3) ¿Corresponde testeo germinal BRCA a los 54 sin antecedentes familiares? Si saliera mutado, "
    "¿qué umbral de OlympiA tendría que cumplir una RH+?"
)


class TestCasoDeReferencia:
    def setup_method(self):
        self.ex = extraer_perfil(CONSULTA_MARIA)
        self.p = self.ex.perfil

    def test_extrae_los_datos_del_caso(self):
        p = self.p
        assert (p.edad, p.re_pct, p.rp_pct, p.her2_ihq, p.ki67_pct, p.grado) == (54, 90, 70, 2, 25, 2)
        assert (p.t, p.n, p.m) == ("T2", "N1", "M0")
        assert p.tamano_cm == 3.2
        assert p.menciona_marcadores_sericos

    def test_lo_condicional_no_es_un_dato(self):
        # "Si el ISH da no amplificado" es una hipótesis del médico, no un ISH realizado.
        assert self.p.her2_ish is None
        assert any(t.campo == "her2_ish" for t in self.ex.descartados)

    def test_no_infiere_menopausia_ni_brca(self):
        assert self.p.menopausia is None
        assert self.p.brca_germinal is None

    def test_umbral_citado_no_es_un_recurrence_score(self):
        assert self.p.recurrence_score is None

    def test_m0_explicito_gana_sobre_mencion_suelta(self):
        assert P.metastasico(self.p).value == "no"

    def test_subtipo_queda_indeterminado_con_dos_compatibles(self):
        assert P.subtipo_molecular(self.p) is None
        assert len(P.subtipos_compatibles(self.p)) == 2

    def test_cada_dato_tiene_su_fragmento(self):
        trazas = {t.campo: t.fragmento for t in self.ex.trazas}
        assert "RE 90%" in trazas["re_pct"]
        assert "HER2 IHQ 2+" in trazas["her2_ihq"]


class TestErroresDelDetectorAnterior:
    def test_re_mas_no_fija_her2_negativo(self):
        # Antes "RE+" clasificaba RH+/HER2− aunque el texto dijera HER2 2+.
        p = extraer_perfil("RE+ RP+, HER2 2+ sin FISH").perfil
        assert P.subtipo_molecular(p) is None

    def test_luminal_no_determina_subtipo(self):
        p = extraer_perfil("tumor luminal B, Ki-67 30%").perfil
        assert P.subtipo_molecular(p) is None

    def test_valores_numericos_triple_negativo(self):
        p = extraer_perfil("RE 0%, RP 0%, HER2 IHQ 1+").perfil
        assert P.subtipo_molecular(p) == P.SUBTIPO_TRIPLE_NEGATIVO

    def test_prefijo_re_no_es_receptor(self):
        p = extraer_perfil("se solicita re-estadificación con PET").perfil
        assert p.re is None and p.re_pct is None

    def test_siglas_clinicas_no_son_genes(self):
        p = extraer_perfil("PAAF de axila, CDI, TC de tórax, BRCA1 pendiente").perfil
        assert p.genes_mencionados == ("BRCA1",)

    def test_clase_cdk4_6_no_es_el_gen_cdk4(self):
        assert "CDK4" not in extraer_perfil("¿corresponde un inhibidor de CDK4/6 adyuvante?").perfil.genes_mencionados


class TestOtrosEscenarios:
    def test_post_neoadyuvancia(self):
        p = extraer_perfil("ypT1c ypN1a (2/14) tras neoadyuvancia, RCB-II, CPS+EG 3, BRCA1 germinal mutado").perfil
        assert p.neoadyuvancia_previa is True
        assert p.respuesta_patologica_completa is False
        assert p.ganglios_positivos == 2
        assert p.n_patologico is True
        assert p.cps_eg == 3
        assert p.brca_germinal == "mutado"

    def test_metastasico_pretratado(self):
        p = extraer_perfil(
            "mujer de 67 años, metastásica ósea, progresó a letrozol + palbociclib, PIK3CA H1047R, "
            "ESR1 no mutado, ECOG 1, posmenopáusica, HER2 1+"
        ).perfil
        assert p.metastasico is True
        assert p.endocrino_previo_metastasico is True
        assert p.variantes == {"PIK3CA": True, "ESR1": False}
        assert (p.ecog, p.menopausia, p.sexo) == (1, "post", "F")

    def test_negacion_de_metastasis(self):
        assert extraer_perfil("HER2 3+, sin metástasis a distancia").perfil.metastasico is False

    def test_texto_vacio(self):
        ex = extraer_perfil(None)
        assert ex.perfil == P.PerfilClinico()
        assert ex.trazas == ()
