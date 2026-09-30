"""
Lógica trivaluada (Kleene fuerte): SI, NO, INDETERMINADO.

Es la base de todo el motor. "INDETERMINADO" no es lo mismo que "NO": un caso
con HER2 IHQ 2+ sin ISH no es HER2-negativo, es HER2 sin determinar, y
tratarlo como negativo descartaría opciones que siguen abiertas. Con dos
valores esa distinción se pierde; con tres, queda explícita en cada criterio.

Las conectivas cortocircuitan como en la clínica: RE positivo ya descarta el
triple negativo aunque falten RP y HER2 (NO en una conjunción gana sobre
INDETERMINADO), y una sola indicación que se cumple alcanza para una
disyunción aunque las otras no se puedan evaluar.
"""
from __future__ import annotations

from enum import Enum
from typing import Iterable


class Tri(str, Enum):
    SI = "si"
    NO = "no"
    INDETERMINADO = "indeterminado"

    @classmethod
    def de_bool(cls, valor: bool | None) -> Tri:
        """True → SI, False → NO, None (dato ausente) → INDETERMINADO."""
        if valor is None:
            return cls.INDETERMINADO
        return cls.SI if valor else cls.NO

    def __invert__(self) -> Tri:
        if self is Tri.SI:
            return Tri.NO
        if self is Tri.NO:
            return Tri.SI
        return Tri.INDETERMINADO

    def __and__(self, otro: Tri) -> Tri:
        return y(self, otro)

    def __or__(self, otro: Tri) -> Tri:
        return o(self, otro)


def y(*valores: Tri | Iterable[Tri]) -> Tri:
    """Conjunción de Kleene: NO si alguno es NO; SI si todos son SI; si no, INDETERMINADO."""
    planos = _aplanar(valores)
    if any(v is Tri.NO for v in planos):
        return Tri.NO
    if all(v is Tri.SI for v in planos):
        return Tri.SI
    return Tri.INDETERMINADO


def o(*valores: Tri | Iterable[Tri]) -> Tri:
    """Disyunción de Kleene: SI si alguno es SI; NO si todos son NO; si no, INDETERMINADO."""
    planos = _aplanar(valores)
    if any(v is Tri.SI for v in planos):
        return Tri.SI
    if all(v is Tri.NO for v in planos):
        return Tri.NO
    return Tri.INDETERMINADO


def no(valor: Tri) -> Tri:
    return ~valor


def comparar(valor: float | int | None, predicado) -> Tri:
    """Aplica un predicado numérico a un dato que puede faltar. Ausente ⇒ INDETERMINADO."""
    if valor is None:
        return Tri.INDETERMINADO
    return Tri.SI if predicado(valor) else Tri.NO


def _aplanar(valores) -> list[Tri]:
    planos: list[Tri] = []
    for v in valores:
        if isinstance(v, Tri):
            planos.append(v)
        else:
            planos.extend(v)
    return planos
