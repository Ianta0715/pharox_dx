"""
Recuperación de evidencia dirigida por el motor (app/conocimiento/recuperacion.py),
con un lector de Neo4j falso.
"""
from neo4j.exceptions import ServiceUnavailable

from app.conocimiento.evidencia import Fuente
from app.conocimiento.recuperacion import recuperar_evidencia
from app.dominio.perfil import PerfilClinico
from app.dominio.reglas.motor import evaluar
from tests.fakes import LectorFalso

MARIA = PerfilClinico(edad=54, sexo="F", re_pct=90, rp_pct=70, her2_ihq=2, ki67_pct=25, grado=2, t="T2", n="N1", m="M0")


def _recuperar(perfil=MARIA, lector=None, embedder=None):
    lector = lector or LectorFalso()
    return recuperar_evidencia(lector, perfil, evaluar(perfil), "consulta", embedder), lector


def _de(resultado, fuente):
    return [e for e in resultado.evidencias if e.fuente is fuente]


class TestProtocolos:
    def test_trae_los_subtipos_compatibles_y_filtra_por_escenario(self):
        r, _ = _recuperar()
        ids = {e.referencias[0] for e in _de(r, Fuente.PROTOCOLO)}
        # RH+/HER2− temprano y HER2+ temprano; NO el de primera línea metastásica.
        assert ids == {"PROTO_01", "PROTO_02"}

    def test_esquema_textual_y_condicionado_al_subtipo(self):
        r, _ = _recuperar()
        tchp = next(e for e in _de(r, Fuente.PROTOCOLO) if e.referencias == ("PROTO_02",))
        assert "TCHP: Docetaxel + Carboplatino + Trastuzumab + Pertuzumab" in tchp.detalle
        assert "aplica solo si se confirma" in tchp.titulo


class TestCivic:
    def test_prioriza_evidencia_de_opciones_abiertas(self):
        r, _ = _recuperar()
        civic = _de(r, Fuente.CIVIC)
        assert "neo-anti-her2" in civic[0].opciones
        assert civic[0].referencias == ("PMID:22149876",)

    def test_descarta_evidencia_de_opciones_cerradas(self):
        # Alpelisib es de enfermedad metastásica: no aplica a este caso M0.
        r, _ = _recuperar()
        assert not any("alpelisib" in e.titulo for e in _de(r, Fuente.CIVIC))


class TestEnsayos:
    def test_filtra_escenario_y_edad(self):
        r, _ = _recuperar()
        ncts = {e.referencias[0] for e in _de(r, Fuente.ENSAYO)}
        assert "NCT00000002" not in ncts  # metastásico, paciente M0
        assert "NCT00000004" not in ncts  # edad mínima 70
        assert {"NCT00000001", "NCT00000003"} <= ncts

    def test_nunca_concluye_elegibilidad_completa(self):
        r, _ = _recuperar()
        assert all("Quedan por verificar" in e.detalle for e in _de(r, Fuente.ENSAYO))


class TestContextoPoblacional:
    def test_registro_agregado_y_marcado_como_otras_personas(self):
        r, _ = _recuperar()
        (registro,) = _de(r, Fuente.REGISTRO)
        assert registro.sobre_otras_personas
        assert "Dudoso" in registro.detalle
        assert "no incluye tratamiento" in registro.detalle

    def test_frecuencias_de_biomarcadores_pendientes(self):
        r, _ = _recuperar()
        assert any("ERBB2" in e.titulo for e in _de(r, Fuente.CBIOPORTAL))
        assert all(e.sobre_otras_personas for e in _de(r, Fuente.CBIOPORTAL))


class TestRobustez:
    def test_una_fuente_caida_no_tumba_la_consulta(self):
        r, _ = _recuperar(lector=LectorFalso(fallar_en=("TIENE_EVIDENCIA",)))
        assert [f.fuente for f in r.fuentes_fallidas if f.fuente == "civic"] == ["civic"]
        assert _de(r, Fuente.PROTOCOLO) and _de(r, Fuente.ENSAYO)

    def test_con_neo4j_caido_no_espera_fuente_por_fuente(self):
        llamadas = []

        def lector_caido(cypher, params=None):
            llamadas.append(cypher)
            raise ServiceUnavailable("sin conexión")

        r = recuperar_evidencia(lector_caido, MARIA, evaluar(MARIA), "consulta", embedder=lambda t: [0.0])
        assert len(llamadas) == 1
        assert r.evidencias == ()
        assert {f.error for f in r.fuentes_fallidas} == {"Neo4j no disponible"}

    def test_sin_embeddings_se_informa(self):
        r, _ = _recuperar()
        assert any(f.fuente == "literatura_y_casos" for f in r.fuentes_fallidas)

    def test_con_embeddings_trae_literatura(self):
        r, _ = _recuperar(embedder=lambda texto: [0.1] * 768)
        (lit,) = _de(r, Fuente.LITERATURA)
        assert lit.referencias == ("PMID:38000001",)

    def test_ids_correlativos(self):
        r, _ = _recuperar()
        assert [e.id for e in r.evidencias] == [f"E{i}" for i in range(1, len(r.evidencias) + 1)]

    def test_todas_las_consultas_llevan_parametros_no_texto_libre(self):
        _, lector = _recuperar()
        # El texto de la consulta nunca se interpola en el Cypher.
        assert all("consulta" not in cypher for cypher, _ in lector.consultas)
