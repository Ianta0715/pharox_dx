"""
Verificación determinística de la redacción del LLM (app/lenguaje/verificacion.py)
y reglas nuevas del explorador text-to-Cypher (app/lenguaje/text_to_cypher.py).
"""
import pytest

from app.lenguaje.text_to_cypher import con_limite, validate_cypher
from app.lenguaje.verificacion import verificar_narrativa

DOSSIER = (
    "✅ Tamoxifeno adyuvante — cumple. ⏳ Ribociclib adyuvante + inhibidor de aromatasa — condicional. "
    "[E1] Protocolo: TCHP: Docetaxel + Carboplatino + Trastuzumab + Pertuzumab. "
    "[E2] CIViC ERBB2 — letrozole (PMID:22149876). [E3] NCT00000001 — frecuencia 12.5% en METABRIC. "
    "Trastuzumab deruxtecan — no aplica."
)
IDS = {"E1", "E2", "E3"}


def _tipos(narrativa):
    return [h.tipo for h in verificar_narrativa(narrativa, DOSSIER, IDS)]


class TestVerificacionNarrativa:
    def test_redaccion_fiel_pasa(self):
        texto = "El tamoxifeno cumple criterios. El ensayo NCT00000001 [E3] y el esquema TCHP [E1] figuran en la base (12.5%)."
        assert _tipos(texto) == []

    def test_droga_inventada(self):
        # El caso real que motivó esto: qwen3:14b inventó un esquema "CMF".
        assert _tipos("El esquema CMF es una alternativa [E1].") == ["entidad_no_respaldada"]

    def test_sinonimos_en_ingles_y_abreviaturas(self):
        assert _tipos("El letrozol figura en la evidencia [E2]; T-DXd no aplica.") == []

    @pytest.mark.parametrize("texto", [
        "Ver NCT09999999.",
        "Según PMID 12345678.",
        "El beneficio absoluto fue de 5%.",
        "Como indica [E7].",
    ])
    def test_entidades_no_respaldadas(self, texto):
        assert _tipos(texto) == ["entidad_no_respaldada"]

    def test_formula_de_consejo_se_senala(self):
        assert _tipos("Se recomienda solicitar el ISH.") == ["consejo"]


class TestExploradorCypher:
    @pytest.mark.parametrize("query", [
        "DROP INDEX literature_vectors",
        "LOAD CSV FROM 'http://evil.example/x.csv' AS row RETURN row",
        "CALL apoc.refactor.rename.label('Paciente', 'X')",
        "MATCH (n) FOREACH (x IN [1] | SET n.a = 1)",
        "MATCH (n) RETURN n; MATCH (m) RETURN m",
    ])
    def test_bloquea(self, query):
        with pytest.raises(ValueError):
            validate_cypher(query)

    @pytest.mark.parametrize("query", [
        "MATCH (n:Paciente) WHERE n.nota = 'reset de dataset' RETURN n",  # palabras prohibidas dentro de un string
        "MATCH (n) WHERE n.subset_id = 1 RETURN n",  # 'set' como subcadena
        "MATCH (n) RETURN n SKIP 10 LIMIT 5",
    ])
    def test_no_bloquea_falsos_positivos(self, query):
        assert validate_cypher(query) == query

    def test_agrega_limite_si_falta(self):
        assert con_limite("MATCH (n) RETURN n;") == "MATCH (n) RETURN n LIMIT 100"
        assert con_limite("MATCH (n) RETURN n LIMIT 5") == "MATCH (n) RETURN n LIMIT 5"
