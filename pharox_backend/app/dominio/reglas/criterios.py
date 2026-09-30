"""
Criterios reutilizables del catálogo.

Un criterio separa dos cosas: QUÉ condición evalúa (`evaluar`, trivaluada) y
QUÉ datos del caso lo destrabarían si hoy es INDETERMINADO (`datos`). Esa
separación es la que permite decirle al médico no solo "condicional", sino
"condicional: falta el ISH de HER2".

Cada criterio reproduce una definición publicada (etiqueta de la indicación o
población del ensayo pivotal) y la nombra en su texto, para que el comité
clínico pueda auditarla contra la fuente.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.dominio import perfil as P
from app.dominio.perfil import PerfilClinico
from app.dominio.tri import Tri, comparar, o, y


@dataclass(frozen=True)
class Criterio:
    id: str
    texto: str
    evaluar: Callable[[PerfilClinico], Tri]
    # Datos que podrían destrabar el criterio; solo se reportan los que faltan.
    datos: tuple[str, ...] | Callable[[PerfilClinico], tuple[str, ...]] = ()

    def datos_faltantes(self, p: PerfilClinico) -> tuple[str, ...]:
        candidatos = self.datos(p) if callable(self.datos) else self.datos
        return tuple(d for d in dict.fromkeys(candidatos) if P.falta_dato(p, d))


# ---------------------------------------------------------------------------
# Datos requeridos que dependen del caso
# ---------------------------------------------------------------------------
def _datos_her2(p: PerfilClinico) -> tuple[str, ...]:
    """Sin IHQ hace falta la IHQ; con IHQ 2+ hace falta el ISH. Con IHQ 0/1+/3+ no falta nada."""
    if p.her2_ihq is None:
        return ("her2_ihq",)
    if p.her2_ihq == 2:
        return ("her2_ish",)
    return ()


def _datos_tnbc(p: PerfilClinico) -> tuple[str, ...]:
    return ("re", "rp") + _datos_her2(p)


def _datos_genes(genes: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(f"gen_{g}" for g in genes)


# ---------------------------------------------------------------------------
# Biología tumoral
# ---------------------------------------------------------------------------
RH_POSITIVO = Criterio("rh+", "Receptor hormonal positivo (RE o RP ≥1 %)", P.rh_positivo, ("re", "rp"))
HER2_POSITIVO = Criterio("her2+", "HER2 positivo (IHQ 3+ o ISH amplificado)", P.her2_positivo, _datos_her2)
HER2_NEGATIVO = Criterio("her2-", "HER2 negativo (IHQ 0/1+, o 2+ con ISH no amplificado)", P.her2_negativo, _datos_her2)
HER2_LOW = Criterio("her2-low", "HER2-low (IHQ 1+, o 2+ con ISH no amplificado)", P.her2_low, _datos_her2)
TRIPLE_NEGATIVO = Criterio("tnbc", "Triple negativo (RE <1 %, RP <1 %, HER2 negativo)", P.triple_negativo, _datos_tnbc)

BRCA_GERMINAL_MUTADO = Criterio(
    "gbrca",
    "Variante patogénica germinal en BRCA1/2",
    lambda p: Tri.INDETERMINADO if p.brca_germinal is None else Tri.de_bool(p.brca_germinal == "mutado"),
    ("brca_germinal",),
)


def alteracion_en(genes: tuple[str, ...], texto: str) -> Criterio:
    """Disyunción sobre genes: una alteración alcanza; todos estudiados sin alteración ⇒ NO."""

    def evaluar(p: PerfilClinico) -> Tri:
        return o(*[Tri.INDETERMINADO if g not in p.variantes else Tri.de_bool(p.variantes[g]) for g in genes])

    return Criterio(f"alteracion-{'-'.join(g.lower() for g in genes)}", texto, evaluar, _datos_genes(genes))


def pdl1_cps_al_menos(umbral: int) -> Criterio:
    return Criterio(f"pdl1-cps>={umbral}", f"PD-L1 CPS ≥{umbral}", lambda p: comparar(p.pdl1_cps, lambda v: v >= umbral), ("pdl1_cps",))


# ---------------------------------------------------------------------------
# Escenario y extensión
# ---------------------------------------------------------------------------
NO_METASTASICO = Criterio("m0", "Enfermedad no metastásica (M0)", lambda p: ~P.metastasico(p), ("estadificacion_sistemica",))
METASTASICO = Criterio("m1", "Enfermedad metastásica o irresecable", P.metastasico, ("estadificacion_sistemica",))

CANDIDATA_NEOADYUVANCIA_HER2 = Criterio(
    "ct2-o-cn+",
    "Tumor cT2 o mayor, o ganglios clínicamente positivos (NCCN: neoadyuvancia preferida en HER2+)",
    lambda p: o(comparar(P.t_num(p), lambda t: t >= 2), P.ganglios_clinicos_positivos(p)),
    ("tnm",),
)


def _poblacion_keynote522(p: PerfilClinico) -> Tri:
    t, n = P.t_num(p), P.n_num(p)
    if t is not None and n is not None:
        if n == 3 or t == 0:
            return Tri.NO
        if t >= 2:
            return Tri.SI
        # T1: solo T1c con N1-2
        if n == 0:
            return Tri.NO
        sub = (p.t or "").upper().removeprefix("T").removeprefix("1")
        if sub == "C":
            return Tri.SI
        return Tri.NO if sub in ("A", "B", "MI") else Tri.INDETERMINADO
    orden = P.estadio_ordinal(P.estadio_efectivo(p))
    if orden is None:
        return Tri.INDETERMINADO
    return Tri.de_bool(2.0 <= orden <= 3.1)  # II a IIIB; IIIC es N3


POBLACION_KEYNOTE522 = Criterio(
    "kn522",
    "Población KEYNOTE-522: cT1c N1–2 o cT2–4 N0–2",
    _poblacion_keynote522,
    ("tnm",),
)

RESIDUAL_TRAS_NEOADYUVANCIA = Criterio(
    "residual-post-neo",
    "Enfermedad residual tras tratamiento neoadyuvante (sin respuesta patológica completa)",
    lambda p: y(Tri.de_bool(p.neoadyuvancia_previa), ~Tri.de_bool(p.respuesta_patologica_completa)),
    ("neoadyuvancia", "respuesta_patologica"),
)


def _alto_riesgo_olympia(p: PerfilClinico) -> Tri:
    """
    OlympiA (olaparib adyuvante, gBRCA, HER2−):
    - Tras neoadyuvancia con enfermedad residual: TNBC alcanza; RH+ exige además CPS+EG ≥3.
    - Cirugía de inicio: TNBC con pT ≥2 o pN+; RH+ con ≥4 ganglios positivos.
    """
    if p.neoadyuvancia_previa is None:
        return Tri.INDETERMINADO
    tnbc, rh = P.triple_negativo(p), P.rh_positivo(p)
    if p.neoadyuvancia_previa:
        residual = ~Tri.de_bool(p.respuesta_patologica_completa)
        cps = comparar(p.cps_eg, lambda v: v >= 3)
        return o(y(tnbc, residual), y(rh, residual, cps))
    rango = P.rango_ganglios_positivos(p)
    ganglios_positivos = Tri.INDETERMINADO if rango is None else Tri.de_bool(rango[0] >= 1)
    cuatro_o_mas = Tri.INDETERMINADO if rango is None else (Tri.SI if rango[0] >= 4 else Tri.NO if rango[1] < 4 else Tri.INDETERMINADO)
    t_mayor_2cm = comparar(p.tamano_cm, lambda v: v > 2) if p.tamano_cm is not None else comparar(P.t_num(p), lambda t: t >= 2)
    return o(y(tnbc, o(t_mayor_2cm, ganglios_positivos)), y(rh, cuatro_o_mas))


def _datos_olympia(p: PerfilClinico) -> tuple[str, ...]:
    """Solo los datos de la rama (TNBC / RH+) que el perfil todavía no descartó."""
    if p.neoadyuvancia_previa is None:
        return ("neoadyuvancia",)
    rh = P.rh_positivo(p)
    datos = ["re", "rp"] if rh is Tri.INDETERMINADO else []
    if p.neoadyuvancia_previa:
        datos.append("respuesta_patologica")
        if rh is not Tri.NO:
            datos.append("cps_eg")
    else:
        datos.append("ganglios_patologicos")
        if P.triple_negativo(p) is not Tri.NO:
            datos.append("tamano_tumoral")
    return tuple(datos)


ALTO_RIESGO_OLYMPIA = Criterio(
    "olympia",
    "Alto riesgo según OlympiA (tras neoadyuvancia: residual, y en RH+ CPS+EG ≥3; cirugía de inicio: TNBC pT≥2 o pN+, RH+ ≥4 ganglios)",
    _alto_riesgo_olympia,
    _datos_olympia,
)


def _alto_riesgo_monarche(p: PerfilClinico) -> Tri:
    rango = P.rango_ganglios_positivos(p)
    if rango is None:
        return Tri.INDETERMINADO
    minimo, maximo = rango
    if minimo >= 4:
        return Tri.SI
    if maximo == 0:
        return Tri.NO
    riesgo = o(comparar(p.grado, lambda g: g == 3), P.tamano_al_menos_5cm(p))
    if minimo >= 1 and maximo <= 3:
        return riesgo
    return Tri.INDETERMINADO


ALTO_RIESGO_MONARCHE = Criterio(
    "monarche-c1",
    "Alto riesgo monarchE (cohorte 1): ≥4 ganglios positivos, o 1–3 con grado 3 o tumor ≥5 cm",
    _alto_riesgo_monarche,
    ("ganglios_patologicos", "grado", "tamano_tumoral"),
)


def _poblacion_natalee(p: PerfilClinico) -> Tri:
    estadio = P.estadio_efectivo(p)
    orden = P.estadio_ordinal(estadio)
    if orden is None:
        return Tri.INDETERMINADO
    if orden < 2.0 or orden >= 4.0:
        return Tri.NO
    if orden >= 2.1:  # IIB, III
        return Tri.SI
    # IIA: N1 alcanza; N0 exige G3, o G2 con Ki-67 ≥20 % o riesgo genómico alto.
    ganglios = P.ganglios_clinicos_positivos(p)
    if ganglios is Tri.SI:
        return Tri.SI
    riesgo_n0 = o(
        comparar(p.grado, lambda g: g == 3),
        y(comparar(p.grado, lambda g: g == 2), o(comparar(p.ki67_pct, lambda k: k >= 20), P.riesgo_genomico_alto(p))),
    )
    if ganglios is Tri.NO:
        return riesgo_n0
    return o(Tri.INDETERMINADO, riesgo_n0)


POBLACION_NATALEE = Criterio(
    "natalee",
    "Población NATALEE: estadio IIB–III, o IIA con N1, o IIA N0 con G3 / G2 + (Ki-67 ≥20 % o riesgo genómico alto)",
    _poblacion_natalee,
    ("tnm", "grado", "ki67", "firma_genomica"),
)


def _hasta_3_ganglios(p: PerfilClinico) -> Tri:
    rango = P.rango_ganglios_positivos(p)
    if rango is not None:
        if rango[1] <= 3:
            return Tri.SI
        return Tri.NO if rango[0] >= 4 else Tri.INDETERMINADO
    n = P.n_num(p)
    if n is None:
        return Tri.INDETERMINADO
    if n == 0:
        return Tri.SI
    return Tri.NO if n >= 2 else Tri.INDETERMINADO  # cN1: el conteo real sale de la cirugía


HASTA_3_GANGLIOS = Criterio(
    "0-3-ganglios",
    "0 a 3 ganglios positivos (poblaciones TAILORx / RxPONDER)",
    _hasta_3_ganglios,
    ("ganglios_patologicos", "tnm"),
)

SIN_FIRMA_PREVIA = Criterio(
    "sin-firma-previa",
    "Sin firma genómica ya informada",
    lambda p: Tri.de_bool(P.falta_dato(p, "firma_genomica")),
)

# ---------------------------------------------------------------------------
# Estado funcional y hormonal
# ---------------------------------------------------------------------------
def _menopausia_es(valor: str) -> Callable[[PerfilClinico], Tri]:
    return lambda p: Tri.INDETERMINADO if p.menopausia is None else Tri.de_bool(p.menopausia == valor)


# La edad no alcanza para inferirlo: a los 54 años una paciente puede seguir
# siendo premenopáusica, y el esquema endocrino depende de eso.
POSMENOPAUSIA = Criterio("posmenopausia", "Posmenopausia", _menopausia_es("post"), ("menopausia",))
PREMENOPAUSIA = Criterio("premenopausia", "Premenopausia o perimenopausia", _menopausia_es("pre"), ("menopausia",))


def ecog_hasta(maximo: int) -> Criterio:
    return Criterio(f"ecog<={maximo}", f"Performance status ECOG 0–{maximo}", lambda p: comparar(p.ecog, lambda e: e <= maximo), ("ecog",))


# ---------------------------------------------------------------------------
# Tratamientos previos en enfermedad avanzada
# ---------------------------------------------------------------------------
QUIMIO_PREVIA_METASTASICO = Criterio(
    "quimio-previa-m1",
    "Quimioterapia previa en enfermedad avanzada (o recaída ≤6 meses de quimioterapia adyuvante)",
    lambda p: Tri.de_bool(p.quimio_previa_metastasico),
    ("quimio_previa_metastasico",),
)
SIN_QUIMIO_PREVIA_METASTASICO = Criterio(
    "sin-quimio-previa-m1",
    "Sin quimioterapia previa en enfermedad avanzada",
    lambda p: ~Tri.de_bool(p.quimio_previa_metastasico),
    ("quimio_previa_metastasico",),
)
ENDOCRINO_PREVIO_METASTASICO = Criterio(
    "endocrino-previo-m1",
    "Progresión a hormonoterapia previa",
    lambda p: Tri.de_bool(p.endocrino_previo_metastasico),
    ("endocrino_previo_metastasico",),
)
ANTI_HER2_PREVIO_METASTASICO = Criterio(
    "anti-her2-previo-m1",
    "Tratamiento anti-HER2 previo en enfermedad avanzada",
    lambda p: Tri.de_bool(p.anti_her2_previo_metastasico),
    ("anti_her2_previo_metastasico",),
)


def _lineas_ascent(p: PerfilClinico) -> Tri:
    lineas = p.lineas_previas_metastasico
    if lineas is None:
        return Tri.INDETERMINADO
    if lineas >= 2:
        return Tri.SI
    # Con una línea en avanzada cuenta además la terapia del escenario temprano,
    # que el perfil no modela: queda para revisión.
    return Tri.NO if lineas == 0 else Tri.INDETERMINADO


LINEAS_ASCENT = Criterio(
    "ascent-lineas",
    "Dos o más líneas sistémicas previas, al menos una en enfermedad avanzada",
    _lineas_ascent,
    ("lineas_previas_metastasico",),
)

# ---------------------------------------------------------------------------
# Estudios
# ---------------------------------------------------------------------------
HER2_EQUIVOCO_SIN_ISH = Criterio(
    "her2-2+-sin-ish",
    "HER2 IHQ 2+ (equívoco) sin ISH realizado (ASCO/CAP 2023)",
    lambda p: Tri.INDETERMINADO if p.her2_ihq is None else Tri.de_bool(P.her2_equivoco_sin_ish(p)),
    ("her2_ihq",),
)

INDICACION_TEST_GERMINAL = Criterio(
    "indicacion-germinal",
    "Diagnóstico a los ≤65 años, TNBC, sexo masculino o candidata a inhibidor de PARP (ASCO–SSO 2024)",
    lambda p: o(
        comparar(p.edad, lambda e: e <= 65),
        P.triple_negativo(p),
        Tri.INDETERMINADO if p.sexo is None else Tri.de_bool(p.sexo == "M"),
        y(P.metastasico(p), P.her2_negativo(p)),
    ),
    ("edad", "sexo", "re", "rp"),
)
SIN_TEST_GERMINAL_PREVIO = Criterio(
    "sin-germinal-previo",
    "Sin resultado germinal BRCA1/2 previo",
    lambda p: Tri.de_bool(p.brca_germinal is None),
)

SIN_M1_CONFIRMADO = Criterio(
    "sin-m1",
    "Sin metástasis a distancia ya confirmadas",
    lambda p: Tri.NO if P.metastasico(p) is Tri.SI else Tri.SI,
)
RIESGO_ESTADIFICACION = Criterio(
    "cn+-o-ct3",
    "Ganglios clínicamente positivos o tumor cT3–T4 (ESMO 2024)",
    lambda p: o(P.ganglios_clinicos_positivos(p), comparar(P.t_num(p), lambda t: t >= 3)),
    ("tnm",),
)
