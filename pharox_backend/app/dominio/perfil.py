"""
Perfil clínico tipado y todo lo que se deriva de él sin inferir.

`PerfilClinico` es la única representación de "la paciente" que ve el motor
de reglas, venga de un texto libre (dominio/extraccion.py), de un JSON
estructurado del front o de un :RegistroTumor real (dominio/registro.py).
Todos los campos son opcionales a propósito: `None` significa "no está en el
caso", nunca "negativo".

Las derivaciones (receptor hormonal, estado HER2, HER2-low, triple negativo,
escenario metastásico, estadio anatómico, subtipo molecular) devuelven `Tri`,
y siguen los mismos criterios que ya usaba el resto del backend:
- RE/RP positivos si ≥1 % (ASCO/CAP).
- HER2 positivo si IHQ 3+ o ISH amplificado; negativo si IHQ 0/1+ o IHQ 2+
  con ISH no amplificado; IHQ 2+ sin ISH queda INDETERMINADO — no se fuerza
  una clasificación que el dato no sostiene.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, fields, replace
from typing import Literal

from app.dominio.tri import Tri, comparar, o, y

Receptor = Literal["positivo", "negativo"]
Her2Ish = Literal["amplificado", "no_amplificado"]
Menopausia = Literal["pre", "post"]
EstadoBrca = Literal["mutado", "no_mutado"]
Sexo = Literal["F", "M"]

# Vocabulario cerrado de subtipo molecular: el mismo que usan
# :RegistroTumor.subtipo_molecular, :ProtocoloTratamiento.subtipo_molecular_match
# y :EnsayoClinico.subtipos_relacionados en el grafo.
SUBTIPO_HER2_POSITIVO = "HER2_positivo"
SUBTIPO_TRIPLE_NEGATIVO = "Triple_negativo"
SUBTIPO_RH_POSITIVO_HER2_NEGATIVO = "RH_positivo_HER2_negativo"
SUBTIPOS = (SUBTIPO_HER2_POSITIVO, SUBTIPO_TRIPLE_NEGATIVO, SUBTIPO_RH_POSITIVO_HER2_NEGATIVO)


@dataclass
class PerfilClinico:
    # Demografía
    edad: int | None = None
    sexo: Sexo | None = None
    # Receptores (porcentaje si se informó; si no, el estado final informado)
    re_pct: int | None = None
    re: Receptor | None = None
    rp_pct: int | None = None
    rp: Receptor | None = None
    her2_ihq: int | None = None  # 0, 1, 2 o 3 (+)
    her2_ish: Her2Ish | None = None
    her2_informado: Receptor | None = None  # estado final sin score (p. ej. registro "(+)")
    her2_low_informado: bool | None = None  # "HER2-low" informado sin score de IHQ
    ki67_pct: int | None = None
    grado: int | None = None  # 1-3 (Nottingham)
    # Estadificación. T/N/M sin prefijo (c/p/yp): "T2", "Tis", "N1", "M0".
    t: str | None = None
    n: str | None = None
    m: str | None = None
    n_patologico: bool | None = None  # True si N viene de pN/ypN (define rango de ganglios)
    estadio: str | None = None  # estadio explícito del informe ("IIB")
    tamano_cm: float | None = None
    ganglios_positivos: int | None = None  # conteo patológico
    metastasico: bool | None = None
    # Historia terapéutica
    neoadyuvancia_previa: bool | None = None
    respuesta_patologica_completa: bool | None = None
    cps_eg: int | None = None
    quimio_previa_metastasico: bool | None = None
    endocrino_previo_metastasico: bool | None = None
    anti_her2_previo_metastasico: bool | None = None
    lineas_previas_metastasico: int | None = None
    # Funcional
    ecog: int | None = None
    menopausia: Menopausia | None = None
    # Genómica: variantes[gen] = True (mutado) / False (estudiado, sin mutación).
    # Un gen ausente del dict NO está estudiado.
    brca_germinal: EstadoBrca | None = None
    variantes: dict[str, bool] = field(default_factory=dict)
    pdl1_cps: int | None = None
    recurrence_score: int | None = None  # Oncotype DX
    mammaprint_alto_riesgo: bool | None = None
    # Solo para recuperar evidencia — nunca para decidir: nombrar un gen no es
    # tener una mutación en él.
    genes_mencionados: tuple[str, ...] = ()
    menciona_marcadores_sericos: bool = False


def fusionar(base: PerfilClinico, prioridad: PerfilClinico) -> PerfilClinico:
    """
    Combina dos perfiles: cada campo informado en `prioridad` pisa al de `base`.
    Se usa para que un perfil estructurado (lo que el front sabe con certeza)
    tenga precedencia sobre lo extraído de un texto libre.
    """
    cambios: dict = {}
    for f in fields(PerfilClinico):
        valor = getattr(prioridad, f.name)
        if f.name == "variantes":
            cambios["variantes"] = {**base.variantes, **prioridad.variantes}
        elif f.name == "genes_mencionados":
            cambios["genes_mencionados"] = tuple(dict.fromkeys(base.genes_mencionados + prioridad.genes_mencionados))
        elif f.name == "menciona_marcadores_sericos":
            cambios[f.name] = base.menciona_marcadores_sericos or prioridad.menciona_marcadores_sericos
        elif valor is not None:
            cambios[f.name] = valor
    return replace(base, **cambios)


# ---------------------------------------------------------------------------
# Receptores y HER2
# ---------------------------------------------------------------------------
def _receptor(pct: int | None, estado: Receptor | None) -> Tri:
    if pct is not None:
        return Tri.SI if pct >= 1 else Tri.NO
    if estado is not None:
        return Tri.SI if estado == "positivo" else Tri.NO
    return Tri.INDETERMINADO


def re_positivo(p: PerfilClinico) -> Tri:
    return _receptor(p.re_pct, p.re)


def rp_positivo(p: PerfilClinico) -> Tri:
    return _receptor(p.rp_pct, p.rp)


def rh_positivo(p: PerfilClinico) -> Tri:
    """Receptor hormonal positivo: RE o RP positivos."""
    return o(re_positivo(p), rp_positivo(p))


def her2_estado(p: PerfilClinico) -> Receptor | None:
    if p.her2_ish == "amplificado":
        return "positivo"
    if p.her2_ihq is not None:
        if p.her2_ihq == 3:
            return "positivo"
        if p.her2_ihq in (0, 1):
            return "negativo"
        if p.her2_ihq == 2:
            return "negativo" if p.her2_ish == "no_amplificado" else None
    if p.her2_ish == "no_amplificado" or p.her2_low_informado:
        return "negativo"
    return p.her2_informado


def her2_positivo(p: PerfilClinico) -> Tri:
    estado = her2_estado(p)
    return Tri.INDETERMINADO if estado is None else Tri.de_bool(estado == "positivo")


def her2_negativo(p: PerfilClinico) -> Tri:
    return ~her2_positivo(p)


def her2_equivoco_sin_ish(p: PerfilClinico) -> bool:
    return p.her2_ihq == 2 and p.her2_ish is None


def her2_low(p: PerfilClinico) -> Tri:
    """HER2-low: IHQ 1+, o IHQ 2+ con ISH no amplificado (DESTINY-Breast04)."""
    if p.her2_ish == "amplificado" or p.her2_ihq in (0, 3):
        return Tri.NO
    if p.her2_ihq == 1:
        return Tri.SI
    if p.her2_ihq == 2:
        return Tri.SI if p.her2_ish == "no_amplificado" else Tri.INDETERMINADO
    if p.her2_low_informado is not None:
        return Tri.de_bool(p.her2_low_informado)
    if p.her2_informado == "positivo":
        return Tri.NO
    # "HER2 negativo" sin score no distingue HER2-0 de HER2-low.
    return Tri.INDETERMINADO


def triple_negativo(p: PerfilClinico) -> Tri:
    return y(~re_positivo(p), ~rp_positivo(p), her2_negativo(p))


# ---------------------------------------------------------------------------
# Estadificación y escenario
# ---------------------------------------------------------------------------
_ROMANOS_ESTADIO = re.compile(r"^(0|IS|IV|I{1,3}[ABC]?)$")


def normalizar_estadio(estadio: str | None) -> str | None:
    if not estadio:
        return None
    limpio = estadio.strip().upper().replace("ESTADIO", "").strip()
    return limpio if _ROMANOS_ESTADIO.match(limpio) else None


def _num_categoria(valor: str | None, letra: str) -> int | None:
    """"T2" → 2, "T1c" → 1, "Tis" → 0, "N2a" → 2, "TX"/None → None."""
    if not valor:
        return None
    v = valor.strip().upper()
    if v.startswith(letra):
        v = v[1:]
    if v.startswith("IS"):
        return 0
    m = re.match(r"^([0-4])", v)
    return int(m.group(1)) if m else None


def t_num(p: PerfilClinico) -> int | None:
    return _num_categoria(p.t, "T")


def n_num(p: PerfilClinico) -> int | None:
    return _num_categoria(p.n, "N")


def estadio_anatomico(t: str | None, n: str | None, m: str | None) -> str | None:
    """Grupo de estadio anatómico AJCC 8.ª ed. para mama a partir de T/N/M."""
    if m and m.upper() == "M1":
        return "IV"
    tn = _num_categoria(t, "T")
    nn = _num_categoria(n, "N")
    if tn is None or nn is None:
        return None
    if t and t.strip().upper().lstrip("T").startswith("IS"):
        return "0" if nn == 0 else None
    if nn == 3:
        return "IIIC"
    if tn == 4:
        return "IIIB"
    if nn == 2:
        return "IIIA"
    if tn == 3:
        return "IIIA" if nn == 1 else "IIB"
    if tn == 2:
        return "IIB" if nn == 1 else "IIA"
    if nn == 1:  # T0-T1 N1
        return "IIA"
    if tn == 1:
        return "IA"
    return None


def estadio_efectivo(p: PerfilClinico) -> str | None:
    """Estadio explícito si se informó; si no, el anatómico calculado desde T/N/M."""
    return normalizar_estadio(p.estadio) or estadio_anatomico(p.t, p.n, p.m)


_ORDINAL_ESTADIO = {
    "0": 0.0, "IS": 0.0,
    "I": 1.0, "IA": 1.0, "IB": 1.1, "IC": 1.2,
    "II": 2.0, "IIA": 2.0, "IIB": 2.1,
    "III": 3.0, "IIIA": 3.0, "IIIB": 3.1, "IIIC": 3.2,
    "IV": 4.0,
}


def estadio_ordinal(estadio: str | None) -> float | None:
    return _ORDINAL_ESTADIO.get(estadio) if estadio else None


def metastasico(p: PerfilClinico) -> Tri:
    """
    SI con M1, estadio IV o mención explícita; NO con M0 o un estadio
    explícito I-III. El estadio CALCULADO desde T/N sin M no alcanza para
    afirmar M0: ahí el escenario queda INDETERMINADO.
    """
    if p.metastasico is not None:
        return Tri.de_bool(p.metastasico)
    if p.m:
        return Tri.de_bool(p.m.upper() == "M1")
    explicito = normalizar_estadio(p.estadio)
    if explicito:
        return Tri.de_bool(explicito == "IV")
    return Tri.INDETERMINADO


# pN de AJCC: pN1 = 1-3 ganglios axilares, pN2 = 4-9, pN3 = 10 o más.
_RANGO_PN = {0: (0, 0), 1: (1, 3), 2: (4, 9), 3: (10, 10_000)}


def rango_ganglios_positivos(p: PerfilClinico) -> tuple[int, int] | None:
    """
    Rango de ganglios positivos que sostiene la anatomía patológica: exacto si
    se informó el conteo, o el rango de la categoría pN. El cN clínico NO
    alcanza (el conteo real recién se conoce con la cirugía).
    """
    if p.ganglios_positivos is not None:
        return (p.ganglios_positivos, p.ganglios_positivos)
    if p.n_patologico:
        n = n_num(p)
        if n is not None:
            return _RANGO_PN[n]
    return None


def ganglios_clinicos_positivos(p: PerfilClinico) -> Tri:
    if p.ganglios_positivos is not None:
        return Tri.de_bool(p.ganglios_positivos > 0)
    return comparar(n_num(p), lambda n: n > 0)


def riesgo_genomico_alto(p: PerfilClinico) -> Tri:
    """Oncotype DX RS ≥26 o MammaPrint de alto riesgo."""
    if p.recurrence_score is not None:
        return Tri.de_bool(p.recurrence_score >= 26)
    return Tri.de_bool(p.mammaprint_alto_riesgo)


def tamano_al_menos_5cm(p: PerfilClinico) -> Tri:
    if p.tamano_cm is not None:
        return Tri.de_bool(p.tamano_cm >= 5)
    t = t_num(p)
    if t is None:
        return Tri.INDETERMINADO
    return Tri.de_bool(t >= 3)  # T2 es >2 y ≤5 cm; T3, >5 cm


# ---------------------------------------------------------------------------
# Subtipo molecular
# ---------------------------------------------------------------------------
def pertenencia_subtipos(p: PerfilClinico) -> dict[str, Tri]:
    return {
        SUBTIPO_HER2_POSITIVO: her2_positivo(p),
        SUBTIPO_TRIPLE_NEGATIVO: triple_negativo(p),
        SUBTIPO_RH_POSITIVO_HER2_NEGATIVO: y(rh_positivo(p), her2_negativo(p)),
    }


def subtipo_molecular(p: PerfilClinico) -> str | None:
    """El subtipo que el dato sostiene con certeza, o None si todavía no se puede determinar."""
    for subtipo, valor in pertenencia_subtipos(p).items():
        if valor is Tri.SI:
            return subtipo
    return None


def subtipos_compatibles(p: PerfilClinico) -> tuple[str, ...]:
    """
    Subtipos que el perfil todavía no descarta. Con HER2 IHQ 2+ sin ISH y RE+
    son dos (HER2_positivo y RH_positivo_HER2_negativo): la evidencia se busca
    para ambos y se presenta condicionada al resultado del ISH, en vez de
    elegir uno por inferencia.
    """
    return tuple(s for s, v in pertenencia_subtipos(p).items() if v is not Tri.NO)


# ---------------------------------------------------------------------------
# Datos del caso: qué falta y cómo se llama para un oncólogo
# ---------------------------------------------------------------------------
ETIQUETAS_DATOS: dict[str, str] = {
    "edad": "Edad",
    "sexo": "Sexo",
    "re": "Receptor de estrógeno",
    "rp": "Receptor de progesterona",
    "her2_ihq": "HER2 por inmunohistoquímica",
    "her2_ish": "HER2 por ISH (FISH / ISH dual)",
    "ki67": "Ki-67",
    "grado": "Grado histológico",
    "tnm": "Estadificación TNM (T y N)",
    "estadificacion_sistemica": "Estadificación sistémica (M)",
    "tamano_tumoral": "Tamaño tumoral",
    "ganglios_patologicos": "Número de ganglios positivos (anatomía patológica)",
    "neoadyuvancia": "Tratamiento neoadyuvante previo",
    "respuesta_patologica": "Respuesta patológica tras neoadyuvancia",
    "cps_eg": "Score CPS+EG",
    "quimio_previa_metastasico": "Quimioterapia previa en enfermedad avanzada",
    "endocrino_previo_metastasico": "Hormonoterapia previa en enfermedad avanzada",
    "anti_her2_previo_metastasico": "Anti-HER2 previo en enfermedad avanzada",
    "lineas_previas_metastasico": "Líneas sistémicas previas en enfermedad avanzada",
    "ecog": "Performance status ECOG",
    "menopausia": "Estado menopáusico",
    "brca_germinal": "Test germinal BRCA1/2",
    "pdl1_cps": "PD-L1 (CPS)",
    "firma_genomica": "Firma genómica (Oncotype DX / MammaPrint)",
    "gen_PIK3CA": "Estado mutacional de PIK3CA",
    "gen_AKT1": "Estado mutacional de AKT1",
    "gen_PTEN": "Estado mutacional de PTEN",
    "gen_ESR1": "Estado mutacional de ESR1 (idealmente en ADN circulante)",
}


def falta_dato(p: PerfilClinico, clave: str) -> bool:
    """True si el dato identificado por `clave` no está en el perfil."""
    if clave.startswith("gen_"):
        return clave[4:] not in p.variantes
    faltas = {
        "edad": p.edad is None,
        "sexo": p.sexo is None,
        "re": p.re_pct is None and p.re is None,
        "rp": p.rp_pct is None and p.rp is None,
        "her2_ihq": p.her2_ihq is None,
        "her2_ish": p.her2_ish is None,
        "ki67": p.ki67_pct is None,
        "grado": p.grado is None,
        "tnm": p.t is None or p.n is None,
        "estadificacion_sistemica": metastasico(p) is Tri.INDETERMINADO,
        "tamano_tumoral": p.tamano_cm is None and t_num(p) is None,
        "ganglios_patologicos": rango_ganglios_positivos(p) is None,
        "neoadyuvancia": p.neoadyuvancia_previa is None,
        "respuesta_patologica": p.respuesta_patologica_completa is None,
        "cps_eg": p.cps_eg is None,
        "quimio_previa_metastasico": p.quimio_previa_metastasico is None,
        "endocrino_previo_metastasico": p.endocrino_previo_metastasico is None,
        "anti_her2_previo_metastasico": p.anti_her2_previo_metastasico is None,
        "lineas_previas_metastasico": p.lineas_previas_metastasico is None,
        "ecog": p.ecog is None,
        "menopausia": p.menopausia is None,
        "brca_germinal": p.brca_germinal is None,
        "pdl1_cps": p.pdl1_cps is None,
        "firma_genomica": p.recurrence_score is None and p.mammaprint_alto_riesgo is None,
    }
    if clave not in faltas:
        raise KeyError(f"Dato desconocido para el motor: '{clave}'")
    return faltas[clave]


def etiqueta_dato(clave: str) -> str:
    return ETIQUETAS_DATOS.get(clave, clave)
