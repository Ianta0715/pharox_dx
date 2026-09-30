"""
API de punta a punta (FastAPI TestClient) con Neo4j y Ollama reemplazados por
dobles de prueba vía dependency_overrides. No requiere servicios levantados.
"""
import pytest
from fastapi.testclient import TestClient

from app.api.dependencias import obtener_dependencias, obtener_lector, obtener_llm_cypher
from app.main import app
from app.servicios.copiloto import Dependencias
from tests.fakes import LLMGuionado, LectorFalso
from tests.test_extraccion import CONSULTA_MARIA

HEADERS = {"X-API-Key": "test-api-key"}


@pytest.fixture
def cliente():
    estado = {"llm": None, "llm_cypher": None, "estricta": False}
    lector = LectorFalso()

    def deps():
        crear = (lambda: estado["llm"]) if estado["llm"] else None
        crear_cypher = (lambda: estado["llm_cypher"]) if estado["llm_cypher"] else None
        return Dependencias(
            lector=lector, embedder=None, crear_llm=crear, crear_llm_cypher=crear_cypher,
            nombre_modelo="modelo-de-prueba", verificacion_estricta=estado["estricta"],
        )

    app.dependency_overrides[obtener_dependencias] = deps
    app.dependency_overrides[obtener_lector] = lambda: lector
    c = TestClient(app)
    c.estado = estado
    yield c
    app.dependency_overrides.clear()


def _opcion(cuerpo, opcion_id):
    return next(o for o in cuerpo["evaluacion"]["opciones"] if o["id"] == opcion_id)


class TestSeguridad:
    def test_health_publico(self, cliente):
        assert cliente.get("/").status_code == 200

    def test_sin_api_key(self, cliente):
        assert cliente.post("/api/v1/evaluar", json={"consulta": "x"}).status_code == 401

    def test_api_key_incorrecta(self, cliente):
        assert cliente.get("/api/v1/reglas", headers={"X-API-Key": "otra"}).status_code == 401


class TestConsultar:
    def test_caso_de_referencia_sin_llm(self, cliente):
        r = cliente.post("/api/v1/consultar", headers=HEADERS, json={"consulta": CONSULTA_MARIA})
        assert r.status_code == 200
        cuerpo = r.json()
        assert cuerpo["evaluacion"]["escenario"] == "temprano"
        assert cuerpo["evaluacion"]["subtipo"] is None
        assert _opcion(cuerpo, "tdxd-her2-low-db04")["estado"] == "no_aplica"
        assert _opcion(cuerpo, "ribociclib-natalee")["datos_faltantes"] == ["HER2 por ISH (FISH / ISH dual)"]
        assert cuerpo["narrativa"] is None
        assert "## Evaluación del motor de reglas" in cuerpo["respuesta"]
        assert any(e["fuente"] == "protocolo_estandar" for e in cuerpo["evidencia"])

    def test_redaccion_fiel_se_incluye(self, cliente):
        cliente.estado["llm"] = LLMGuionado("El tamoxifeno adyuvante cumple criterios; el ISH define el resto [E1].")
        cuerpo = cliente.post("/api/v1/consultar", headers=HEADERS, json={"consulta": CONSULTA_MARIA}).json()
        assert cuerpo["narrativa"].startswith("El tamoxifeno")
        assert cuerpo["modelo_narrativa"] == "modelo-de-prueba"
        assert cuerpo["respuesta"].startswith("El tamoxifeno")

    def test_redaccion_no_respaldada_se_advierte_por_defecto(self, cliente):
        cliente.estado["llm"] = LLMGuionado("Una alternativa es CMF, con beneficio absoluto de 7%.")
        cuerpo = cliente.post("/api/v1/consultar", headers=HEADERS, json={"consulta": CONSULTA_MARIA}).json()
        assert cuerpo["narrativa"].startswith("Una alternativa es CMF")
        assert any("cmf" in a for a in cuerpo["advertencias"])
        assert "Verificación automática" in cuerpo["respuesta"]

    def test_redaccion_no_respaldada_se_descarta_en_modo_estricto(self, cliente):
        cliente.estado["llm"] = LLMGuionado("Una alternativa es CMF, con beneficio absoluto de 7%.")
        cliente.estado["estricta"] = True
        cuerpo = cliente.post("/api/v1/consultar", headers=HEADERS, json={"consulta": CONSULTA_MARIA}).json()
        assert cuerpo["narrativa"] is None
        assert any("cmf" in a for a in cuerpo["advertencias"])
        assert cuerpo["respuesta"].startswith("## Perfil interpretado")

    def test_campos_del_contrato_anterior(self, cliente):
        cuerpo = cliente.post("/api/v1/consultar", headers=HEADERS, json={"consulta": CONSULTA_MARIA}).json()
        assert cuerpo["metodo_recuperacion"].startswith("Motor de reglas")
        assert "[E1]" in cuerpo["evidencia_recuperada"]
        assert cuerpo["cypher_utilizado"] == ""

    def test_perfil_estructurado_tiene_prioridad(self, cliente):
        cuerpo = cliente.post("/api/v1/evaluar", headers=HEADERS, json={
            "consulta": CONSULTA_MARIA,
            "perfil": {"her2_ish": "no_amplificado", "menopausia": "post"},
        }).json()
        assert cuerpo["evaluacion"]["subtipo"] == "RH_positivo_HER2_negativo"
        assert _opcion(cuerpo, "ia-adyuvante")["estado"] == "cumple"
        assert _opcion(cuerpo, "neo-anti-her2")["estado"] == "no_aplica"
        assert cuerpo["origen"]["her2_ish"] == "perfil estructurado"

    def test_evaluar_nunca_llama_al_llm(self, cliente):
        llm = LLMGuionado("no debería usarse")
        cliente.estado["llm"] = llm
        cliente.post("/api/v1/evaluar", headers=HEADERS, json={"consulta": CONSULTA_MARIA})
        assert llm.prompts == []

    def test_requiere_consulta_o_perfil(self, cliente):
        assert cliente.post("/api/v1/evaluar", headers=HEADERS, json={}).status_code == 422

    def test_perfil_invalido(self, cliente):
        r = cliente.post("/api/v1/evaluar", headers=HEADERS, json={"perfil": {"her2_ihq": 5}})
        assert r.status_code == 422

    def test_solo_cancer_de_mama(self, cliente):
        r = cliente.post("/api/v1/evaluar", headers=HEADERS, json={"consulta": "x", "tipo_cancer": "Cáncer de Pulmón"})
        assert r.status_code == 422


class TestConsultaGeneral:
    """Preguntas de conocimiento o de cohorte: el copiloto responde como antes, sin aplicar el catálogo."""

    def test_pregunta_de_evidencia_molecular(self, cliente):
        cuerpo = cliente.post("/api/v1/consultar", headers=HEADERS, json={
            "consulta": "¿Qué evidencia clínica existe para la variante H1047R del gen PIK3CA?",
        }).json()
        assert cuerpo["modo"] == "general"
        assert cuerpo["evaluacion"] is None
        assert any(e["fuente"] == "civic" and "PIK3CA" in e["titulo"] for e in cuerpo["evidencia"])
        assert "Evaluación del motor de reglas" not in cuerpo["respuesta"]
        assert cuerpo["respuesta"].startswith("## Consulta general")

    def test_text_to_cypher_responde_primero(self, cliente):
        cliente.estado["llm_cypher"] = LLMGuionado(
            "MATCH (r:RegistroTumor) WHERE r.subtipo_molecular = 'Triple_negativo' RETURN count(r) AS cantidad"
        )
        cuerpo = cliente.post("/api/v1/consultar", headers=HEADERS, json={
            "consulta": "¿Cuántas pacientes reales del registro de tumores tienen subtipo triple negativo?",
        }).json()
        primera = cuerpo["evidencia"][0]
        assert primera["fuente"] == "consulta_cypher"
        assert '"cantidad": 25' in primera["detalle"]
        assert primera["sobre_otras_personas"]
        assert cuerpo["cypher_utilizado"].endswith("LIMIT 100")
        assert "Text-to-Cypher" in cuerpo["metodo_recuperacion"]
        assert any(e["fuente"] == "registro_tumores" for e in cuerpo["evidencia"])

    def test_cypher_bloqueado_no_tumba_la_consulta(self, cliente):
        cliente.estado["llm_cypher"] = LLMGuionado("MATCH (n) DETACH DELETE n")
        r = cliente.post("/api/v1/consultar", headers=HEADERS, json={"consulta": "¿Qué ensayos hay para HER2 positivo?"})
        assert r.status_code == 200
        assert any(f["fuente"] == "consulta_estructurada" for f in r.json()["fuentes_fallidas"])

    def test_redaccion_usa_el_prompt_general(self, cliente):
        llm = LLMGuionado("La base registra evidencia para PIK3CA [E1].")
        cliente.estado["llm"] = llm
        cuerpo = cliente.post("/api/v1/consultar", headers=HEADERS, json={"consulta": "¿Qué evidencia hay para PIK3CA?"}).json()
        assert cuerpo["narrativa"].startswith("La base registra")
        assert "consulta de\nconocimiento" in llm.prompts[0]

    def test_evaluar_no_usa_text_to_cypher(self, cliente):
        llm_cypher = LLMGuionado("MATCH (n) RETURN n")
        cliente.estado["llm_cypher"] = llm_cypher
        cliente.post("/api/v1/evaluar", headers=HEADERS, json={"consulta": "¿Qué evidencia hay para PIK3CA?"})
        assert llm_cypher.prompts == []

    def test_modo_forzado(self, cliente):
        cuerpo = cliente.post("/api/v1/evaluar", headers=HEADERS, json={"consulta": CONSULTA_MARIA, "modo": "general"}).json()
        assert cuerpo["modo"] == "general" and cuerpo["evaluacion"] is None


class TestRegistroYReglas:
    def test_evaluacion_de_registro_real(self, cliente):
        cuerpo = cliente.get("/api/v1/registro_tumores/RT_0001/evaluacion", headers=HEADERS).json()
        assert cuerpo["evaluacion"]["subtipo"] == "Triple_negativo"
        assert _opcion(cuerpo, "pembrolizumab-kn522")["estado"] == "cumple"
        assert cuerpo["origen"]["edad"] == "registro RT_0001"

    def test_registro_inexistente(self, cliente):
        assert cliente.get("/api/v1/registro_tumores/RT_9999/evaluacion", headers=HEADERS).status_code == 404

    def test_catalogo_de_reglas_auditable(self, cliente):
        cuerpo = cliente.get("/api/v1/reglas", headers=HEADERS).json()
        assert cuerpo["version_reglas"]
        assert all(o["fuente"] and o["criterios"] for o in cuerpo["opciones"])


class TestExplorar:
    def _con_llm(self, texto):
        app.dependency_overrides[obtener_llm_cypher] = lambda: (lambda: LLMGuionado(texto))

    def test_cypher_de_escritura_se_rechaza(self, cliente):
        self._con_llm("MATCH (n) DETACH DELETE n")
        r = cliente.post("/api/v1/explorar", headers=HEADERS, json={"pregunta": "borrá todo"})
        assert r.status_code == 422

    def test_cypher_de_lectura_se_ejecuta_con_limite(self, cliente):
        self._con_llm("```cypher\nMATCH (p:ProtocoloTratamiento) RETURN p.id AS id\n```")
        cuerpo = cliente.post("/api/v1/explorar", headers=HEADERS, json={"pregunta": "protocolos"}).json()
        assert cuerpo["cypher"].endswith("LIMIT 100")
        assert "No es una evaluación clínica" in cuerpo["aviso"]
