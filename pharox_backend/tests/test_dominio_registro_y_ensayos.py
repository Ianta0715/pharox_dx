"""
Mapeo :RegistroTumor → PerfilClinico (app/dominio/registro.py) y helpers de
ensayos del dominio (app/dominio/ensayos.py).
"""
import pytest

from app.dominio import perfil as P
from app.dominio.ensayos import escenario_del_ensayo, paciente_para_ensayos
from app.dominio.perfil import PerfilClinico
from app.dominio.registro import perfil_desde_registro


def _registro(**overrides) -> dict:
    base = {
        "id": "RT_0001", "edad": 58, "sexo": "Mujer",
        "receptor_estrogeno": "(+)", "receptor_progesterona": "(+)", "her2": "(-)",
        "grado_diferenciacion": "Moderadamente diferenciado",
        "estadio_clinico_t": "2", "estadio_clinico_n": "1", "estadio_clinico_m": "0",
        "estadio_clinico": "IIB", "ecog": "0-Actividad Normal",
    }
    return {**base, **overrides}


class TestPerfilDesdeRegistro:
    def test_mapeo_basico(self):
        p = perfil_desde_registro(_registro())
        assert (p.edad, p.sexo, p.re, p.rp, p.her2_informado, p.grado, p.ecog) == (58, "F", "positivo", "positivo", "negativo", 2, 0)
        assert (p.t, p.n, p.m, p.estadio) == ("T2", "N1", "M0", "IIB")
        assert P.subtipo_molecular(p) == P.SUBTIPO_RH_POSITIVO_HER2_NEGATIVO

    @pytest.mark.parametrize("valor", ["Se ignora", "...", None])
    def test_sin_dato_no_es_negativo(self, valor):
        assert perfil_desde_registro(_registro(receptor_estrogeno=valor)).re is None

    def test_her2_dudoso_queda_sin_determinar(self):
        p = perfil_desde_registro(_registro(her2="Dudoso"))
        assert p.her2_informado is None and p.her2_ihq is None
        assert P.subtipo_molecular(p) is None

    def test_prefiere_n_patologico(self):
        p = perfil_desde_registro(_registro(estadio_patologico_t="1b", estadio_patologico_n="2a"))
        assert (p.t, p.n, p.n_patologico) == ("T1b", "N2a", True)
        assert P.rango_ganglios_positivos(p) == (4, 9)

    def test_carcinoma_in_situ(self):
        assert perfil_desde_registro(_registro(estadio_clinico_t="IS")).t == "Tis"


class TestEnsayos:
    @pytest.mark.parametrize("titulo, escenario", [
        ("Elacestrant in ER+ HER2- Advanced or Metastatic Breast Cancer", "metastasico"),
        ("Neoadjuvant pembrolizumab in early TNBC", "temprano"),
        ("Stage IV breast cancer registry", "metastasico"),  # "stage iv" no cuenta como "stage i"
        ("Adjuvant therapy in stage II-III and metastatic disease", "ambos"),
        ("Quality of life survey", None),
    ])
    def test_escenario_del_ensayo(self, titulo, escenario):
        assert escenario_del_ensayo({"titulo": titulo}) == escenario

    def test_paciente_para_ensayos_usa_subtipo_derivado(self):
        p = PerfilClinico(edad=50, sexo="F", re_pct=0, rp_pct=0, her2_ihq=0)
        assert paciente_para_ensayos(p) == {"edad": 50, "sexo": "F", "subtipo_molecular": P.SUBTIPO_TRIPLE_NEGATIVO}

    def test_subtipo_indeterminado_se_envia_como_desconocido(self):
        assert paciente_para_ensayos(PerfilClinico(her2_ihq=2))["subtipo_molecular"] == "desconocido"
