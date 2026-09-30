"""
Catálogo versionado de opciones terapéuticas y estudios para cáncer de mama.

Las reglas las define y mantiene el equipo clínico; el sistema las ejecuta.
Cada opción cita su fuente (guía + ensayo pivotal) para que un comité
oncológico pueda auditarla, y declara su ESCENARIO: una indicación de
enfermedad metastásica lleva el criterio "Enfermedad metastásica" y por lo
tanto da NO_APLICA en una paciente M0 — en vez de quedar "condicional" a un
FISH, que era el error del catálogo del MVP con T-DXd en estadio IIB.

`drogas` (nombres en inglés, como los usan CIViC y ClinicalTrials.gov) y
`genes` no deciden nada: dirigen la búsqueda de evidencia en el grafo hacia
las opciones que el motor dejó abiertas.

VERSION_REGLAS se sube con cualquier cambio de criterio: queda registrada en
cada respuesta para saber con qué reglas se evaluó un caso.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

from app.dominio import perfil as P
from app.dominio.perfil import PerfilClinico
from app.dominio.reglas import criterios as C
from app.dominio.reglas.criterios import Criterio

VERSION_REGLAS = "2026.09.0-borrador"
ESTADO_VALIDACION = "Borrador pendiente de validación por comité oncológico"

TipoOpcion = Literal["tratamiento", "estudio"]
Escenario = Literal["temprano", "metastasico", "cualquiera"]


@dataclass(frozen=True)
class Opcion:
    id: str
    nombre: str
    tipo: TipoOpcion
    escenario: Escenario
    familia: str
    fuente: str
    resumen: str
    criterios: tuple[Criterio, ...]
    drogas: tuple[str, ...] = ()
    genes: tuple[str, ...] = ()
    notas: Callable[[PerfilClinico], tuple[str, ...]] | None = None


# ---------------------------------------------------------------------------
# Notas contextuales (determinísticas: dependen solo del perfil)
# ---------------------------------------------------------------------------
def _notas_firma_genomica(p: PerfilClinico) -> tuple[str, ...]:
    notas = []
    rango = P.rango_ganglios_positivos(p)
    n = P.n_num(p)
    con_ganglios = (rango is not None and rango[0] >= 1) or (rango is None and n == 1)
    if con_ganglios:
        notas.append(
            "RxPONDER (1–3 ganglios, RS ≤25): sin beneficio de agregar quimioterapia en posmenopáusicas; "
            "con beneficio en premenopáusicas."
        )
        if p.menopausia is None:
            notas.append("El estado menopáusico no está registrado y cambia cómo se interpreta el resultado.")
    elif n == 0 or (rango is not None and rango[1] == 0):
        notas.append("TAILORx (N0): con RS 16–25 hubo beneficio parcial de la quimioterapia en ≤50 años.")
    if p.neoadyuvancia_previa:
        notas.append("Las firmas genómicas no están validadas sobre tejido tratado con neoadyuvancia.")
    return tuple(notas)


def _notas_estadificacion(p: PerfilClinico) -> tuple[str, ...]:
    notas = ["NCCN reserva las imágenes sistémicas para estadio III o síntomas; ESMO las indica con ganglios positivos o T3–T4."]
    if p.menciona_marcadores_sericos:
        notas.append(
            "Los marcadores séricos (CA 15-3, CEA) no se recomiendan para estadificar ni para decidir tratamiento "
            "en enfermedad temprana (ASCO)."
        )
    return tuple(notas)


def _notas_supresion_en_premenopausia(p: PerfilClinico) -> tuple[str, ...]:
    if p.menopausia == "post":
        return ()
    return ("Se combina con inhibidor de aromatasa; en premenopáusicas, con supresión ovárica.",)


def _nota_fija(*textos: str) -> Callable[[PerfilClinico], tuple[str, ...]]:
    return lambda _p: textos


# ---------------------------------------------------------------------------
# Catálogo
# ---------------------------------------------------------------------------
CATALOGO: tuple[Opcion, ...] = (
    # ---- Estudios ---------------------------------------------------------
    Opcion(
        id="ish-her2",
        nombre="Confirmación de HER2 por ISH (FISH / ISH dual)",
        tipo="estudio",
        escenario="cualquiera",
        familia="Biomarcadores",
        fuente="ASCO/CAP HER2 Testing Guideline 2023",
        resumen="Un HER2 IHQ 2+ es equívoco: el ISH define si el tumor es HER2 positivo o HER2-low.",
        criterios=(C.HER2_EQUIVOCO_SIN_ISH,),
        genes=("ERBB2",),
    ),
    Opcion(
        id="test-germinal-brca",
        nombre="Test germinal BRCA1/2",
        tipo="estudio",
        escenario="cualquiera",
        familia="Genética",
        fuente="ASCO–SSO 2024 (Bedrosian, JCO 2024) · NCCN Genetic/Familial High-Risk",
        resumen="Define riesgo hereditario y elegibilidad a inhibidores de PARP (OlympiA, OlympiAD).",
        criterios=(C.INDICACION_TEST_GERMINAL, C.SIN_TEST_GERMINAL_PREVIO),
        genes=("BRCA1", "BRCA2", "PALB2"),
        notas=_nota_fija("Los antecedentes familiares no están modelados en el perfil; también amplían la indicación."),
    ),
    Opcion(
        id="estadificacion-sistemica",
        nombre="Estadificación sistémica por imágenes",
        tipo="estudio",
        escenario="temprano",
        familia="Estadificación",
        fuente="ESMO Early Breast Cancer 2024 · NCCN BINV-1",
        resumen="TC de tórax-abdomen y centellograma óseo, o PET-TC, para descartar enfermedad a distancia.",
        criterios=(C.SIN_M1_CONFIRMADO, C.RIESGO_ESTADIFICACION),
        notas=_notas_estadificacion,
    ),
    Opcion(
        id="firma-genomica",
        nombre="Firma genómica (Oncotype DX u otra) para decidir quimioterapia adyuvante",
        tipo="estudio",
        escenario="temprano",
        familia="Perfil genómico",
        fuente="NCCN · TAILORx (Sparano, NEJM 2018) · RxPONDER (Kalinsky, NEJM 2021)",
        resumen="En RH+/HER2− con 0–3 ganglios, estima el beneficio de agregar quimioterapia a la hormonoterapia.",
        criterios=(C.RH_POSITIVO, C.HER2_NEGATIVO, C.NO_METASTASICO, C.HASTA_3_GANGLIOS, C.SIN_FIRMA_PREVIA),
        notas=_notas_firma_genomica,
    ),
    # ---- Temprano: HER2 positivo ------------------------------------------
    Opcion(
        id="neo-anti-her2",
        nombre="Quimioterapia neoadyuvante con trastuzumab + pertuzumab",
        tipo="tratamiento",
        escenario="temprano",
        familia="Terapia anti-HER2",
        fuente="NCCN Breast Cancer · NeoSphere / TRYPHAENA",
        resumen="Doble bloqueo anti-HER2 con quimioterapia antes de la cirugía en HER2+ cT2+ o cN+.",
        criterios=(C.HER2_POSITIVO, C.NO_METASTASICO, C.CANDIDATA_NEOADYUVANCIA_HER2),
        drogas=("trastuzumab", "pertuzumab"),
        genes=("ERBB2",),
        notas=_nota_fija("Con enfermedad residual tras la neoadyuvancia, la opción adyuvante cambia (ver KATHERINE)."),
    ),
    Opcion(
        id="tdm1-katherine",
        nombre="T-DM1 adyuvante por enfermedad residual",
        tipo="tratamiento",
        escenario="temprano",
        familia="Conjugado anticuerpo-fármaco",
        fuente="NCCN · KATHERINE (von Minckwitz, NEJM 2019)",
        resumen="Trastuzumab emtansina en HER2+ con enfermedad invasora residual tras neoadyuvancia.",
        criterios=(C.HER2_POSITIVO, C.NO_METASTASICO, C.RESIDUAL_TRAS_NEOADYUVANCIA),
        drogas=("trastuzumab emtansine",),
        genes=("ERBB2",),
    ),
    # ---- Temprano: triple negativo ----------------------------------------
    Opcion(
        id="pembrolizumab-kn522",
        nombre="Pembrolizumab + quimioterapia neoadyuvante",
        tipo="tratamiento",
        escenario="temprano",
        familia="Inmunoterapia",
        fuente="NCCN · KEYNOTE-522 (Schmid, NEJM 2020)",
        resumen="Inmunoterapia sumada a quimioterapia neoadyuvante, continuada en adyuvancia, en TNBC de alto riesgo.",
        criterios=(C.TRIPLE_NEGATIVO, C.NO_METASTASICO, C.POBLACION_KEYNOTE522),
        drogas=("pembrolizumab",),
    ),
    Opcion(
        id="capecitabina-createx",
        nombre="Capecitabina adyuvante por enfermedad residual",
        tipo="tratamiento",
        escenario="temprano",
        familia="Quimioterapia",
        fuente="NCCN · CREATE-X (Masuda, NEJM 2017)",
        resumen="Capecitabina en TNBC con enfermedad residual tras neoadyuvancia.",
        criterios=(C.TRIPLE_NEGATIVO, C.NO_METASTASICO, C.RESIDUAL_TRAS_NEOADYUVANCIA),
        drogas=("capecitabine",),
    ),
    # ---- Temprano: BRCA ---------------------------------------------------
    Opcion(
        id="olaparib-olympia",
        nombre="Olaparib adyuvante",
        tipo="tratamiento",
        escenario="temprano",
        familia="Inhibidor de PARP",
        fuente="NCCN · OlympiA (Tutt, NEJM 2021)",
        resumen="Un año de olaparib en gBRCA, HER2−, de alto riesgo tras tratamiento local y quimioterapia.",
        criterios=(C.BRCA_GERMINAL_MUTADO, C.HER2_NEGATIVO, C.NO_METASTASICO, C.ALTO_RIESGO_OLYMPIA),
        drogas=("olaparib",),
        genes=("BRCA1", "BRCA2"),
    ),
    # ---- Temprano: receptor hormonal positivo ------------------------------
    Opcion(
        id="abemaciclib-monarche",
        nombre="Abemaciclib adyuvante + hormonoterapia",
        tipo="tratamiento",
        escenario="temprano",
        familia="Inhibidor de CDK4/6",
        fuente="NCCN · monarchE (Johnston, JCO 2020; etiqueta FDA 2023)",
        resumen="Dos años de abemaciclib con hormonoterapia en RH+/HER2− con ganglios positivos de alto riesgo.",
        criterios=(C.RH_POSITIVO, C.HER2_NEGATIVO, C.NO_METASTASICO, C.ALTO_RIESGO_MONARCHE),
        drogas=("abemaciclib",),
    ),
    Opcion(
        id="ribociclib-natalee",
        nombre="Ribociclib adyuvante + inhibidor de aromatasa",
        tipo="tratamiento",
        escenario="temprano",
        familia="Inhibidor de CDK4/6",
        fuente="NCCN · NATALEE (Slamon, NEJM 2024)",
        resumen="Tres años de ribociclib con inhibidor de aromatasa en RH+/HER2− estadio II–III de riesgo.",
        criterios=(C.RH_POSITIVO, C.HER2_NEGATIVO, C.NO_METASTASICO, C.POBLACION_NATALEE),
        drogas=("ribociclib",),
        notas=_notas_supresion_en_premenopausia,
    ),
    Opcion(
        id="ia-adyuvante",
        nombre="Inhibidor de aromatasa adyuvante sin supresión ovárica",
        tipo="tratamiento",
        escenario="temprano",
        familia="Hormonoterapia",
        fuente="NCCN / ESMO · ATAC, BIG 1-98",
        resumen="Letrozol, anastrozol o exemestano; sin supresión ovárica solo en posmenopáusicas.",
        criterios=(C.RH_POSITIVO, C.NO_METASTASICO, C.POSMENOPAUSIA),
        drogas=("letrozole", "anastrozole", "exemestane"),
    ),
    Opcion(
        id="supresion-ovarica",
        nombre="Supresión ovárica + inhibidor de aromatasa o tamoxifeno",
        tipo="tratamiento",
        escenario="temprano",
        familia="Hormonoterapia",
        fuente="NCCN / ESMO · SOFT/TEXT (Francis, NEJM 2018)",
        resumen="En premenopáusicas de mayor riesgo, la supresión ovárica mejora el resultado de la hormonoterapia.",
        criterios=(C.RH_POSITIVO, C.NO_METASTASICO, C.PREMENOPAUSIA),
        drogas=("goserelin", "leuprolide", "exemestane", "tamoxifen"),
    ),
    Opcion(
        id="tamoxifeno-adyuvante",
        nombre="Tamoxifeno adyuvante",
        tipo="tratamiento",
        escenario="temprano",
        familia="Hormonoterapia",
        fuente="NCCN / ESMO · EBCTCG",
        resumen="Hormonoterapia válida con independencia del estado menopáusico.",
        criterios=(C.RH_POSITIVO, C.NO_METASTASICO),
        drogas=("tamoxifen",),
    ),
    # ---- Metastásico ------------------------------------------------------
    Opcion(
        id="cdk46-primera-linea",
        nombre="Inhibidor de CDK4/6 + hormonoterapia en primera línea",
        tipo="tratamiento",
        escenario="metastasico",
        familia="Inhibidor de CDK4/6",
        fuente="NCCN · PALOMA-2 / MONALEESA-2 / MONARCH 3",
        resumen="Palbociclib, ribociclib o abemaciclib con inhibidor de aromatasa o fulvestrant.",
        criterios=(C.RH_POSITIVO, C.HER2_NEGATIVO, C.METASTASICO),
        drogas=("palbociclib", "ribociclib", "abemaciclib"),
        notas=_nota_fija("En premenopáusicas requiere supresión ovárica (MONALEESA-7)."),
    ),
    Opcion(
        id="thp-cleopatra",
        nombre="Taxano + trastuzumab + pertuzumab en primera línea",
        tipo="tratamiento",
        escenario="metastasico",
        familia="Terapia anti-HER2",
        fuente="NCCN · CLEOPATRA (Swain, NEJM 2015)",
        resumen="Doble bloqueo anti-HER2 con docetaxel en HER2+ metastásico.",
        criterios=(C.HER2_POSITIVO, C.METASTASICO),
        drogas=("trastuzumab", "pertuzumab", "docetaxel"),
        genes=("ERBB2",),
    ),
    Opcion(
        id="tdxd-her2-positivo-db03",
        nombre="Trastuzumab deruxtecan en HER2 positivo pretratado",
        tipo="tratamiento",
        escenario="metastasico",
        familia="Conjugado anticuerpo-fármaco",
        fuente="NCCN · DESTINY-Breast03 (Cortés, NEJM 2022)",
        resumen="T-DXd tras tratamiento anti-HER2 previo en enfermedad avanzada.",
        criterios=(C.HER2_POSITIVO, C.METASTASICO, C.ANTI_HER2_PREVIO_METASTASICO),
        drogas=("trastuzumab deruxtecan",),
        genes=("ERBB2",),
    ),
    Opcion(
        id="tdxd-her2-low-db04",
        nombre="Trastuzumab deruxtecan en HER2-low tras quimioterapia",
        tipo="tratamiento",
        escenario="metastasico",
        familia="Conjugado anticuerpo-fármaco",
        fuente="NCCN · DESTINY-Breast04 (Modi, NEJM 2022)",
        resumen="T-DXd en HER2-low metastásico o irresecable con quimioterapia previa.",
        criterios=(C.HER2_LOW, C.METASTASICO, C.QUIMIO_PREVIA_METASTASICO),
        drogas=("trastuzumab deruxtecan",),
        genes=("ERBB2",),
    ),
    Opcion(
        id="tdxd-her2-low-db06",
        nombre="Trastuzumab deruxtecan en RH+/HER2-low tras hormonoterapia, sin quimioterapia previa",
        tipo="tratamiento",
        escenario="metastasico",
        familia="Conjugado anticuerpo-fármaco",
        fuente="NCCN · DESTINY-Breast06 (Bardia, NEJM 2024)",
        resumen="T-DXd tras progresión a hormonoterapia en enfermedad avanzada, antes de la quimioterapia.",
        criterios=(C.RH_POSITIVO, C.HER2_LOW, C.METASTASICO, C.ENDOCRINO_PREVIO_METASTASICO, C.SIN_QUIMIO_PREVIA_METASTASICO),
        drogas=("trastuzumab deruxtecan",),
        genes=("ERBB2",),
        notas=_nota_fija("DESTINY-Breast06 incluyó también HER2-ultralow (IHQ 0 con tinción débil ≤10 %), que el perfil no modela."),
    ),
    Opcion(
        id="pi3k-akt-fulvestrant",
        nombre="Inhibidor de PI3K/AKT + fulvestrant",
        tipo="tratamiento",
        escenario="metastasico",
        familia="Terapia dirigida",
        fuente="NCCN · SOLAR-1 (André, NEJM 2019) · CAPItello-291 (Turner, NEJM 2023)",
        resumen="Alpelisib (PIK3CA) o capivasertib (PIK3CA/AKT1/PTEN) con fulvestrant tras hormonoterapia.",
        criterios=(
            C.RH_POSITIVO,
            C.HER2_NEGATIVO,
            C.METASTASICO,
            C.alteracion_en(("PIK3CA", "AKT1", "PTEN"), "Alteración en PIK3CA, AKT1 o PTEN"),
            C.ENDOCRINO_PREVIO_METASTASICO,
        ),
        drogas=("alpelisib", "capivasertib", "fulvestrant"),
        genes=("PIK3CA", "AKT1", "PTEN"),
    ),
    Opcion(
        id="elacestrant-emerald",
        nombre="Elacestrant",
        tipo="tratamiento",
        escenario="metastasico",
        familia="Degradador selectivo del receptor de estrógeno",
        fuente="NCCN · EMERALD (Bidard, JCO 2022)",
        resumen="SERD oral en RH+/HER2− con mutación de ESR1 tras hormonoterapia con CDK4/6.",
        criterios=(
            C.RH_POSITIVO,
            C.HER2_NEGATIVO,
            C.METASTASICO,
            C.alteracion_en(("ESR1",), "Mutación de ESR1"),
            C.ENDOCRINO_PREVIO_METASTASICO,
        ),
        drogas=("elacestrant",),
        genes=("ESR1",),
    ),
    Opcion(
        id="parpi-metastasico",
        nombre="Inhibidor de PARP (olaparib o talazoparib)",
        tipo="tratamiento",
        escenario="metastasico",
        familia="Inhibidor de PARP",
        fuente="NCCN · OlympiAD (Robson, NEJM 2017) · EMBRACA (Litton, NEJM 2018)",
        resumen="En gBRCA, HER2− metastásico.",
        criterios=(C.BRCA_GERMINAL_MUTADO, C.HER2_NEGATIVO, C.METASTASICO),
        drogas=("olaparib", "talazoparib"),
        genes=("BRCA1", "BRCA2"),
    ),
    Opcion(
        id="pembrolizumab-kn355",
        nombre="Pembrolizumab + quimioterapia en primera línea",
        tipo="tratamiento",
        escenario="metastasico",
        familia="Inmunoterapia",
        fuente="NCCN · KEYNOTE-355 (Cortés, NEJM 2022)",
        resumen="En TNBC metastásico con PD-L1 CPS ≥10.",
        criterios=(C.TRIPLE_NEGATIVO, C.METASTASICO, C.pdl1_cps_al_menos(10)),
        drogas=("pembrolizumab",),
    ),
    Opcion(
        id="sacituzumab-ascent",
        nombre="Sacituzumab govitecán",
        tipo="tratamiento",
        escenario="metastasico",
        familia="Conjugado anticuerpo-fármaco",
        fuente="NCCN · ASCENT (Bardia, NEJM 2021)",
        resumen="Anti-Trop-2 en TNBC metastásico pretratado.",
        criterios=(C.TRIPLE_NEGATIVO, C.METASTASICO, C.LINEAS_ASCENT),
        drogas=("sacituzumab govitecan",),
    ),
)

_POR_ID = {o.id: o for o in CATALOGO}
assert len(_POR_ID) == len(CATALOGO), "ids de opción duplicados en el catálogo"


def opcion_por_id(opcion_id: str) -> Opcion:
    return _POR_ID[opcion_id]
