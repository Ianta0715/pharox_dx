"""
Texto libre de una consulta médica → PerfilClinico, sin LLM.

Cada dato que se extrae deja una `Traza` con el fragmento exacto del texto que
lo produjo, para que el médico vea qué entendió el sistema y pueda corregirlo
enviando el perfil estructurado. Lo que no se reconoce queda en None — el
motor lo trata como INDETERMINADO, nunca como negativo.

Tres reglas de diseño, todas nacidas de errores reales del copiloto anterior:

1. Los datos se extraen campo por campo y el subtipo se DERIVA después
   (dominio/perfil.py). Antes, una frase como "RE+" o "luminal" fijaba el
   subtipo RH_positivo_HER2_negativo aunque el mismo texto dijera HER2 2+ sin
   ISH: la frase le ganaba al dato.
2. Lo que está en condicional no es un dato: en "si el ISH da no amplificado"
   no hay un ISH realizado. Las coincidencias precedidas por "si", "en caso
   de", "de ser"... dentro de la misma cláusula se descartan (y se informan).
3. M y estadio explícitos le ganan a menciones sueltas de "metastásico": una
   pregunta como "¿T-DXd solo está aprobado en enfermedad metastásica?" no
   convierte en metastásica a una paciente cT2 cN1 M0.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.dominio.perfil import PerfilClinico, normalizar_estadio

EDAD_MINIMA_PLAUSIBLE = 15
EDAD_MAXIMA_PLAUSIBLE = 110

# Genes con relevancia terapéutica o de riesgo en mama. Mencionarlos alimenta la
# búsqueda de evidencia; nunca se interpreta como que la paciente los tiene
# mutados. Lista cerrada a propósito: la versión anterior tomaba cualquier token
# en mayúsculas como símbolo génico ("CDI", "PAAF", "TC"...).
GENES_MAMA = (
    "BRCA1", "BRCA2", "PALB2", "TP53", "PIK3CA", "AKT1", "PTEN", "ESR1", "ERBB2",
    "CDH1", "ATM", "CHEK2", "GATA3", "MAP3K1", "CDK4", "CDK6", "CCND1", "FGFR1",
    "NF1", "RB1", "MYC", "KMT2C",
)
# Genes cuyo estado mutacional el motor usa como criterio.
GENES_CON_ESTADO = ("PIK3CA", "AKT1", "PTEN", "ESR1", "TP53", "ERBB2")


@dataclass(frozen=True)
class Traza:
    campo: str
    valor: str
    fragmento: str


@dataclass(frozen=True)
class Extraccion:
    perfil: PerfilClinico
    trazas: tuple[Traza, ...]
    descartados: tuple[Traza, ...]


def _normalizar(texto: str) -> str:
    """Minúsculas y sin tildes, preservando la longitud (los índices siguen alineados con el original)."""
    return "".join(unicodedata.normalize("NFKD", ch)[0].lower() for ch in texto)


_HIPOTETICO = re.compile(
    r"\b(?:si(?!\s+bien)|en caso de(?: que)?|de ser|suponiendo|hipoteticamente|ante un)\b[^.;:?!¿]*$"
)
_CONTEXTO_TOXICIDAD = re.compile(r"(toxicidad|adverso|neutropenia|ctcae|mucositis|diarrea|nausea)[^.;]*$")
_NEGACION_CERCANA = re.compile(r"\b(sin|no|descart\w*|libre de|ausencia de|negativ\w* para)\b[^.;,]*$")

_ROMANO_A_ENTERO = {"i": 1, "ii": 2, "iii": 3, "1": 1, "2": 2, "3": 3}
_GUIONES = "-−–"


class _Extractor:
    def __init__(self, texto: str):
        self.orig = texto or ""
        self.norm = _normalizar(self.orig)
        self.valores: dict = {}
        self.variantes: dict[str, bool] = {}
        self.trazas: list[Traza] = []
        self.descartados: list[Traza] = []

    # -- utilidades ---------------------------------------------------------
    def fragmento(self, m: re.Match) -> str:
        inicio = max(0, m.start() - 12)
        fin = min(len(self.orig), m.end() + 12)
        return " ".join(self.orig[inicio:fin].split())

    def antes(self, m: re.Match, ancho: int = 45) -> str:
        return self.norm[max(0, m.start() - ancho):m.start()]

    def es_hipotetico(self, m: re.Match) -> bool:
        return bool(_HIPOTETICO.search(self.antes(m)))

    def buscar(self, patron: str, original: bool = False):
        texto = self.orig if original else self.norm
        return re.finditer(patron, texto)

    def asignar(self, campo: str, valor, m: re.Match) -> bool:
        """Asigna el primer valor encontrado para `campo`; los siguientes se ignoran."""
        if self.es_hipotetico(m):
            self.descartados.append(Traza(campo, str(valor), self.fragmento(m) + "  [condicional, no es un dato]"))
            return False
        if campo in self.valores:
            return False
        self.valores[campo] = valor
        self.trazas.append(Traza(campo, str(valor), self.fragmento(m)))
        return True

    def asignar_variante(self, gen: str, mutado: bool, m: re.Match) -> None:
        if self.es_hipotetico(m):
            self.descartados.append(Traza(f"gen_{gen}", str(mutado), self.fragmento(m) + "  [condicional, no es un dato]"))
            return
        if gen not in self.variantes:
            self.variantes[gen] = mutado
            self.trazas.append(Traza(f"gen_{gen}", "mutado" if mutado else "sin mutación", self.fragmento(m)))

    # -- extracción por dominio -------------------------------------------
    def demografia(self) -> None:
        for m in self.buscar(r"\b(\d{2,3})\s*anos?\b"):
            if re.search(r"\b(hace|durante|por|desde|tras|luego de|despues de)\s*$", self.antes(m, 15)):
                continue
            edad = int(m.group(1))
            if EDAD_MINIMA_PLAUSIBLE <= edad <= EDAD_MAXIMA_PLAUSIBLE and self.asignar("edad", edad, m):
                break
        for m in self.buscar(r"\b(mujer|femenin[oa])\b"):
            self.asignar("sexo", "F", m)
        for m in self.buscar(r"\b(varon|hombre|masculin[oa])\b"):
            self.asignar("sexo", "M", m)

    def receptores(self) -> None:
        for campo, sigla in (("re", r"(?:re|receptor(?:es)? de estrogenos?)"), ("rp", r"(?:rp|receptor(?:es)? de progesterona)")):
            for m in self.buscar(rf"\b{sigla}\b\D{{0,12}}?(\d{{1,3}})\s*%"):
                pct = int(m.group(1))
                if pct <= 100:
                    self.asignar(f"{campo}_pct", pct, m)
            for m in self.buscar(rf"\b{sigla}\s*(?:\(\s*\+\s*\)|\+|positiv\w*)"):
                self.asignar(campo, "positivo", m)
            # Un guion seguido de letra es un prefijo ("re-estadificación"), no RE negativo.
            for m in self.buscar(rf"\b{sigla}\s*(?:\(\s*[{_GUIONES}]\s*\)|[{_GUIONES}](?!\w)|negativ\w*)"):
                self.asignar(campo, "negativo", m)

        for m in self.buscar(r"\b(triple[- ]negativ\w*|tnbc)\b"):
            if self.asignar("re", "negativo", m):
                self.asignar("rp", "negativo", m)
                self.asignar("her2_informado", "negativo", m)

    def her2(self) -> None:
        her2 = r"\b(?:her-?2|erbb2)\b"
        for m in self.buscar(her2 + r"(?:\s*(?:ihq|ihc|por\s+inmunohistoquimica))?\D{0,20}?([0-3])\s*\+"):
            self.asignar("her2_ihq", int(m.group(1)), m)
        for m in self.buscar(her2 + r"\s*(?:\(?\s*(?:ihq|ihc)\s*\)?)?\s*[:=]?\s*0\b(?!\s*[%,.]\d)"):
            self.asignar("her2_ihq", 0, m)

        ish = r"\b(?:fish|sish|cish|ish(?: dual)?)\b"
        for m in self.buscar(ish + r"[^.;,]{0,25}?\b(?:no amplificad\w*|sin amplificacion|negativ\w*)"):
            self.asignar("her2_ish", "no_amplificado", m)
        for m in self.buscar(ish + r"[^.;,]{0,25}?\b(?:amplificad\w*|positiv\w*)"):
            if not re.search(r"\b(?:no|sin)\s+amplific", self.norm[m.start():m.end()]):
                self.asignar("her2_ish", "amplificado", m)
        for m in self.buscar(her2 + r"\s*no amplificad\w*"):
            self.asignar("her2_ish", "no_amplificado", m)
        for m in self.buscar(r"\bamplificacion de (?:her-?2|erbb2)\b|" + her2 + r"\s*amplificad\w*"):
            self.asignar("her2_ish", "amplificado", m)

        for m in self.buscar(her2 + r"[- ]?(?:low|bajo)\b"):
            self.asignar("her2_low_informado", True, m)
        for m in self.buscar(her2 + r"\s*(?:positiv\w*|\(\s*\+\s*\)|\+(?!\s*\d))"):
            self.asignar("her2_informado", "positivo", m)
        for m in self.buscar(her2 + rf"\s*(?:negativ\w*|\(\s*[{_GUIONES}]\s*\)|[{_GUIONES}](?!\s*\d))"):
            self.asignar("her2_informado", "negativo", m)

    def histologia(self) -> None:
        for m in self.buscar(r"\bki[- ]?67\D{0,10}?(\d{1,3})\s*%"):
            self.asignar("ki67_pct", int(m.group(1)), m)
        for m in self.buscar(r"\bgrado\s*(?:histologico\s*|de nottingham\s*|nottingham\s*|sbr\s*)?:?\s*(iii|ii|i|[123])\b"):
            if _CONTEXTO_TOXICIDAD.search(self.antes(m, 30)):
                continue
            self.asignar("grado", _ROMANO_A_ENTERO[m.group(1)], m)
        for m in self.buscar(r"\bG([123])\b", original=True):
            self.asignar("grado", int(m.group(1)), m)

    def estadificacion(self) -> None:
        for m in self.buscar(r"\b(y?[cp]?)T(is|[0-4][a-d]?|X)\b", original=True):
            prefijo, categoria = m.group(1), m.group(2)
            if categoria == "X":
                continue
            if self.asignar("t", f"T{categoria}", m) and prefijo.startswith("y"):
                self.asignar("neoadyuvancia_previa", True, m)
        for m in self.buscar(r"\b(y?[cp]?)N([0-3](?:mi|[a-c])?)\b", original=True):
            prefijo = m.group(1)
            if self.asignar("n", f"N{m.group(2)}", m):
                if prefijo:  # sin prefijo no se sabe si es clínico o patológico
                    self.asignar("n_patologico", "p" in prefijo, m)
                if prefijo.startswith("y"):
                    self.asignar("neoadyuvancia_previa", True, m)
        for m in self.buscar(r"\b[cp]?M([01])\b", original=True):
            self.asignar("m", f"M{m.group(1)}", m)

        for m in self.buscar(r"\bestadio\s*(?:clinico\s*|patologico\s*|anatomico\s*)?(?:[cp]\s*)?(0|iv|i{1,3}[abc]?)\b"):
            estadio = normalizar_estadio(m.group(1))
            if estadio:
                self.asignar("estadio", estadio, m)

        for m in self.buscar(r"(\d{1,3}(?:[.,]\d+)?)\s*(cm|mm)\b"):
            if re.search(r"(adenopat\w*|ganglio\w*|axil\w*)[^.;]*$", self.antes(m, 25)):
                continue
            valor = float(m.group(1).replace(",", "."))
            cm = valor / 10 if m.group(2) == "mm" else valor
            if 0.1 <= cm <= 20:
                self.asignar("tamano_cm", round(cm, 2), m)

        for m in self.buscar(r"\b(\d{1,2})\s*(?:/\s*\d{1,2}\s*)?ganglios?\s*(?:axilares\s*)?(?:positivos|comprometidos|metastasicos|con metastasis)"):
            self.asignar("ganglios_positivos", int(m.group(1)), m)
        for m in self.buscar(r"\b(?:y?[cp])?n[0-3][a-c]?\s*\(\s*(\d{1,2})\s*/\s*\d{1,2}\s*\)"):
            self.asignar("ganglios_positivos", int(m.group(1)), m)

    def escenario(self) -> None:
        for m in self.buscar(r"\b(metastasic[oa]s?|metastasis|enfermedad avanzada|recaida a distancia|estadio iv)\b"):
            if _NEGACION_CERCANA.search(self.antes(m, 25)):
                self.asignar("metastasico", False, m)
            else:
                self.asignar("metastasico", True, m)

    def tratamientos_previos(self) -> None:
        for m in self.buscar(
            r"\b(?:tras|post|posterior a|luego de|despues de|recibio|completo)\s+(?:la\s+|una\s+)?"
            r"(?:quimioterapia\s+|terapia\s+|tratamiento\s+)?neoadyuvan\w*|\bneoadyuvancia previa\b"
        ):
            self.asignar("neoadyuvancia_previa", True, m)
        for m in self.buscar(r"\b(?:sin|no recibio)\s+(?:tratamiento\s+|terapia\s+)?neoadyuvan\w*"):
            self.asignar("neoadyuvancia_previa", False, m)

        for m in self.buscar(
            r"\b(?:enfermedad residual|sin respuesta patologica completa|no alcanzo (?:la )?(?:respuesta patologica completa|pcr)"
            r"|rcb[- ]?(?:iii|ii|i|[123]))\b"
        ):
            self.asignar("respuesta_patologica_completa", False, m)
        for m in self.buscar(r"\b(?:respuesta patologica completa|pcr|rcb[- ]?0)\b"):
            if not re.search(r"\b(sin|no|no alcanzo)\s+(la\s+)?$", self.antes(m, 15)):
                self.asignar("respuesta_patologica_completa", True, m)
        for m in self.buscar(r"\bcps\s*\+\s*eg\s*(?:score\s*)?(?:de\s*|:\s*|=\s*)?([0-6])\b"):
            self.asignar("cps_eg", int(m.group(1)), m)

        progresion = r"\b(?:progres\w*|recaida|recayo|refractari\w*)\b[^.;]{0,40}?\b"
        for m in self.buscar(progresion + r"(?:letrozol|anastrozol|exemestano|tamoxifeno|fulvestrant|inhibidor(?:es)? de aromatasa|hormonoterapia|terapia endocrina|endocrin\w*)"):
            self.asignar("endocrino_previo_metastasico", True, m)
        for m in self.buscar(progresion + r"(?:quimioterapia|capecitabina|paclitaxel|docetaxel|taxano\w*|antraciclin\w*|carboplatino|eribulina)"):
            self.asignar("quimio_previa_metastasico", True, m)
        for m in self.buscar(r"\bsin quimioterapia previa\b"):
            self.asignar("quimio_previa_metastasico", False, m)
        for m in self.buscar(progresion + r"(?:trastuzumab|pertuzumab|anti-?her2|t-?dm1)"):
            self.asignar("anti_her2_previo_metastasico", True, m)
        for m in self.buscar(r"\b(\d)\s*(?:lineas?|l)\s*(?:de tratamiento\s*)?(?:sistemicas?\s*)?previas?\b"):
            self.asignar("lineas_previas_metastasico", int(m.group(1)), m)

    def funcional(self) -> None:
        for m in self.buscar(r"\b(?:ecog|ps)\s*(?:de\s*|:\s*|=\s*)?([0-4])\b"):
            self.asignar("ecog", int(m.group(1)), m)
        for m in self.buscar(r"\b(?:pos|post)[- ]?menopaus\w*"):
            self.asignar("menopausia", "post", m)
        for m in self.buscar(r"\b(?:pre|peri)[- ]?menopaus\w*"):
            self.asignar("menopausia", "pre", m)

    def genomica(self) -> None:
        estado_neg = r"(?:no mutad\w*|sin mutacion\w*|wild[- ]?type|wt\b|negativ\w*|no se detect\w*)"
        estado_pos = r"(?:mutad\w*|mutacion\w*|positiv\w*|patogenic\w*|portador\w*|alterad\w*|[a-z]\d{2,4}[a-z*])"

        for m in self.buscar(r"\bg?brca\s*(?:1|2|1/2|1\s*y\s*2)?\b(?:\s*germinal)?[^.;,]{0,20}?\b" + estado_neg):
            self.asignar("brca_germinal", "no_mutado", m)
        for m in self.buscar(r"\bg?brca\s*(?:1|2|1/2|1\s*y\s*2)?\b(?:\s*germinal)?[^.;,]{0,20}?\b" + estado_pos):
            if "somatic" in self.norm[m.start():m.end() + 15]:
                continue  # el criterio de OlympiA / OlympiAD es BRCA germinal
            self.asignar("brca_germinal", "mutado", m)
        for m in self.buscar(r"\b(?:portadora|mutacion germinal|variante patogenica)\s+(?:de\s+|en\s+)?brca"):
            self.asignar("brca_germinal", "mutado", m)

        for gen in GENES_CON_ESTADO:
            g = gen.lower()
            for m in self.buscar(rf"\b{g}\b[^.;,]{{0,20}}?\b{estado_neg}"):
                self.asignar_variante(gen, False, m)
            for m in self.buscar(rf"\b{g}\b[^.;,]{{0,20}}?\b{estado_pos}|\b(?:mutacion|variante)\s+(?:de\s+|en\s+)?{g}\b"):
                if gen == "ERBB2" and "amplific" in self.norm[m.start():m.end()]:
                    continue  # la amplificación de ERBB2 es HER2, no una mutación
                if re.search(estado_neg, self.norm[m.start():m.end()]):
                    continue
                self.asignar_variante(gen, True, m)

        for m in self.buscar(r"\bpd-?l1\b[^.;]{0,25}?\bcps\s*(?:de\s*|:\s*|=\s*)?[≥>=]*\s*(\d{1,3})\b"):
            self.asignar("pdl1_cps", int(m.group(1)), m)
        # "RS ≤25" (un umbral citado) no matchea a propósito: solo un valor informado.
        for m in self.buscar(r"\b(?:oncotype(?: dx)?|recurrence score|rs)\s*(?:de\s*|:\s*|=\s*)?(\d{1,2})\b"):
            self.asignar("recurrence_score", int(m.group(1)), m)
        for m in self.buscar(r"\bmammaprint\b[^.;,]{0,20}?\b(alto|high)\b"):
            self.asignar("mammaprint_alto_riesgo", True, m)
        for m in self.buscar(r"\bmammaprint\b[^.;,]{0,20}?\b(bajo|low)\b"):
            self.asignar("mammaprint_alto_riesgo", False, m)

        # "(?!\s*/\s*\d)": "CDK4/6" nombra una clase de fármacos, no el gen CDK4.
        mencionados = [g for g in GENES_MAMA if re.search(rf"\b{g}\b(?!\s*/\s*\d)", self.orig)]
        if re.search(r"\bHER-?2\b", self.orig) and "ERBB2" not in mencionados:
            mencionados.append("ERBB2")
        self.valores["genes_mencionados"] = tuple(mencionados)

        if re.search(r"\b(?:ca\s*15[- ]?3|ca\s*27[.,-]?29|cea)\b", self.norm):
            self.valores["menciona_marcadores_sericos"] = True

    def resolver_conflictos(self) -> None:
        # Regla 3 del módulo: M o estadio explícitos mandan sobre "metastásico" suelto.
        if "metastasico" in self.valores and ("m" in self.valores or "estadio" in self.valores):
            valor = self.valores.pop("metastasico")
            traza = next(t for t in self.trazas if t.campo == "metastasico")
            self.trazas.remove(traza)
            self.descartados.append(Traza("metastasico", str(valor), traza.fragmento + "  [se prioriza M/estadio explícito]"))

    def extraer(self) -> Extraccion:
        self.demografia()
        self.receptores()
        self.her2()
        self.histologia()
        self.estadificacion()
        self.escenario()
        self.tratamientos_previos()
        self.funcional()
        self.genomica()
        self.resolver_conflictos()
        perfil = PerfilClinico(**self.valores, variantes=dict(self.variantes))
        return Extraccion(perfil=perfil, trazas=tuple(self.trazas), descartados=tuple(self.descartados))


def extraer_perfil(texto: str | None) -> Extraccion:
    """Extrae el perfil clínico de una consulta en texto libre. Determinística y sin I/O."""
    return _Extractor(texto or "").extraer()
