"""Lógica trivaluada de Kleene (app/dominio/tri.py)."""
import pytest

from app.dominio.tri import Tri, comparar, o, y

SI, NO, IND = Tri.SI, Tri.NO, Tri.INDETERMINADO


class TestConectivas:
    @pytest.mark.parametrize("a, b, esperado", [
        (SI, SI, SI), (SI, NO, NO), (SI, IND, IND),
        (NO, IND, NO),  # NO cortocircuita aunque el otro falte
        (IND, IND, IND),
    ])
    def test_y(self, a, b, esperado):
        assert y(a, b) is esperado
        assert (a & b) is esperado

    @pytest.mark.parametrize("a, b, esperado", [
        (NO, NO, NO), (SI, NO, SI), (NO, IND, IND),
        (SI, IND, SI),  # SI cortocircuita aunque el otro falte
        (IND, IND, IND),
    ])
    def test_o(self, a, b, esperado):
        assert o(a, b) is esperado
        assert (a | b) is esperado

    def test_negacion(self):
        assert ~SI is NO
        assert ~NO is SI
        assert ~IND is IND

    def test_acepta_iterables(self):
        assert y([SI, SI], SI) is SI
        assert o([NO, NO]) is NO


class TestDesdeDatos:
    def test_de_bool(self):
        assert Tri.de_bool(True) is SI
        assert Tri.de_bool(False) is NO
        assert Tri.de_bool(None) is IND

    def test_comparar_dato_ausente_es_indeterminado(self):
        assert comparar(None, lambda v: v > 3) is IND
        assert comparar(5, lambda v: v > 3) is SI
        assert comparar(0, lambda v: v > 3) is NO
