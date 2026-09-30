"""
Dossier clínico en markdown, armado por código (sin LLM).

Es la respuesta completa cuando no hay modelo disponible, y es también el
único material que recibe el modelo cuando lo hay: el médico y el LLM leen
exactamente lo mismo, así que cualquier cosa que el modelo agregue se puede
contrastar (ver lenguaje/verificacion.py).
"""
from __future__ import annotations

from app.conocimiento.evidencia import ETIQUETA_FUENTE, Evidencia
from app.conocimiento.recuperacion import FuenteFallida
from app.dominio import perfil as P
from app.dominio.extraccion import Traza
from app.dominio.perfil import PerfilClinico
from app.dominio.reglas.catalogo import opcion_por_id
from app.dominio.reglas.motor import Estado, Evaluacion, OpcionEvaluada
from app.dominio.tri import Tri

ETIQUETA_SUBTIPO = {
    P.SUBTIPO_HER2_POSITIVO: "HER2 positivo",
    P.SUBTIPO_TRIPLE_NEGATIVO: "Triple negativo",
    P.SUBTIPO_RH_POSITIVO_HER2_NEGATIVO: "RH positivo / HER2 negativo",
}

_ICONO = {Estado.CUMPLE: "✅", Estado.CONDICIONAL: "⏳", Estado.NO_APLICA: "⛔"}
_TEXTO_ESTADO = {Estado.CUMPLE: "cumple criterios", Estado.CONDICIONAL: "condicional", Estado.NO_APLICA: "no aplica"}


def _si_no(valor: bool | None, si: str = "sí", no: str = "no") -> str | None:
    return None if valor is None else (si if valor else no)


def _receptor(pct: int | None, estado: str | None) -> str | None:
    return f"{pct} %" if pct is not None else estado


def filas_perfil(p: PerfilClinico) -> list[tuple[str, str, str]]:
    """(clave de origen, dato, valor) de todo lo que el perfil tiene informado."""
    receptor = _receptor
    tnm = " ".join(x for x in (p.t, p.n, p.m) if x)
    genes = ", ".join(f"{g} {'mutado' if m else 'sin mutación'}" for g, m in p.variantes.items())
    candidatos = [
        ("edad", "Edad", f"{p.edad} años" if p.edad is not None else None),
        ("sexo", "Sexo", {"F": "femenino", "M": "masculino"}.get(p.sexo or "")),
        ("re_pct" if p.re_pct is not None else "re", "Receptor de estrógeno", receptor(p.re_pct, p.re)),
        ("rp_pct" if p.rp_pct is not None else "rp", "Receptor de progesterona", receptor(p.rp_pct, p.rp)),
        ("her2_ihq", "HER2 IHQ", f"{p.her2_ihq}+" if p.her2_ihq is not None else None),
        ("her2_ish", "HER2 ISH", {"amplificado": "amplificado", "no_amplificado": "no amplificado"}.get(p.her2_ish or "")),
        ("her2_informado", "HER2 (estado informado)", p.her2_informado),
        ("her2_low_informado", "HER2-low (informado)", _si_no(p.her2_low_informado)),
        ("ki67_pct", "Ki-67", f"{p.ki67_pct} %" if p.ki67_pct is not None else None),
        ("grado", "Grado histológico", f"G{p.grado}" if p.grado else None),
        ("t", "TNM", tnm or None),
        ("estadio", "Estadio informado", p.estadio),
        ("tamano_cm", "Tamaño tumoral", f"{p.tamano_cm:g} cm" if p.tamano_cm is not None else None),
        ("ganglios_positivos", "Ganglios positivos", str(p.ganglios_positivos) if p.ganglios_positivos is not None else None),
        ("metastasico", "Enfermedad metastásica", _si_no(p.metastasico)),
        ("neoadyuvancia_previa", "Neoadyuvancia previa", _si_no(p.neoadyuvancia_previa)),
        ("respuesta_patologica_completa", "Respuesta patológica completa", _si_no(p.respuesta_patologica_completa)),
        ("cps_eg", "CPS+EG", str(p.cps_eg) if p.cps_eg is not None else None),
        ("quimio_previa_metastasico", "Quimioterapia previa (avanzada)", _si_no(p.quimio_previa_metastasico)),
        ("endocrino_previo_metastasico", "Hormonoterapia previa (avanzada)", _si_no(p.endocrino_previo_metastasico)),
        ("anti_her2_previo_metastasico", "Anti-HER2 previo (avanzada)", _si_no(p.anti_her2_previo_metastasico)),
        ("lineas_previas_metastasico", "Líneas previas (avanzada)", str(p.lineas_previas_metastasico) if p.lineas_previas_metastasico is not None else None),
        ("ecog", "ECOG", str(p.ecog) if p.ecog is not None else None),
        ("menopausia", "Estado menopáusico", {"pre": "pre/perimenopausia", "post": "posmenopausia"}.get(p.menopausia or "")),
        ("brca_germinal", "BRCA1/2 germinal", {"mutado": "variante patogénica", "no_mutado": "sin variante patogénica"}.get(p.brca_germinal or "")),
        ("variantes", "Variantes somáticas", genes or None),
        ("pdl1_cps", "PD-L1 CPS", str(p.pdl1_cps) if p.pdl1_cps is not None else None),
        ("recurrence_score", "Oncotype DX RS", str(p.recurrence_score) if p.recurrence_score is not None else None),
        ("mammaprint_alto_riesgo", "MammaPrint", _si_no(p.mammaprint_alto_riesgo, "alto riesgo", "bajo riesgo")),
    ]
    return [(clave, dato, valor) for clave, dato, valor in candidatos if valor]


_ETIQUETA_CAMPO = {
    "metastasico": "Enfermedad metastásica",
    "her2_informado": "HER2 (estado informado)",
    "re_pct": "Receptor de estrógeno",
    "rp_pct": "Receptor de progesterona",
    "ki67_pct": "Ki-67",
    "menopausia": "Estado menopáusico",
    "neoadyuvancia_previa": "Neoadyuvancia previa",
    "respuesta_patologica_completa": "Respuesta patológica completa",
}


def _etiqueta_campo(campo: str) -> str:
    return _ETIQUETA_CAMPO.get(campo) or P.ETIQUETAS_DATOS.get(campo) or campo


def _describir_subtipo(ev: Evaluacion, perfil: PerfilClinico) -> str:
    if ev.subtipo:
        return ETIQUETA_SUBTIPO[ev.subtipo]
    if not ev.subtipos_compatibles:
        return "sin subtipo compatible (revisar receptores)"
    compatibles = " · ".join(ETIQUETA_SUBTIPO[s] for s in ev.subtipos_compatibles)
    faltan = []
    for c in ("re", "rp", "her2_ihq"):
        if P.falta_dato(perfil, c):
            faltan.append(P.etiqueta_dato(c))
    if P.her2_equivoco_sin_ish(perfil):
        faltan.append(P.etiqueta_dato("her2_ish"))
    return f"indeterminado — compatibles: {compatibles}" + (f" (se define con: {', '.join(faltan)})" if faltan else "")


def _describir_escenario(ev: Evaluacion) -> str:
    return {
        Tri.SI: "Enfermedad metastásica / avanzada",
        Tri.NO: "Enfermedad temprana (M0)",
        Tri.INDETERMINADO: "Sin determinar (falta estadificación sistémica)",
    }[ev.metastasico]


def _linea_opcion(o: OpcionEvaluada) -> list[str]:
    lineas = [f"- {_ICONO[o.estado]} **{o.opcion.nombre}** — {_TEXTO_ESTADO[o.estado]} · _{o.opcion.fuente}_"]
    if o.estado is Estado.CONDICIONAL:
        if o.bloqueantes:
            lineas.append(f"  - Falta en el caso: {', '.join(P.etiqueta_dato(d) for d in o.bloqueantes)}")
        else:
            lineas.append("  - Requiere revisión manual de un criterio que el perfil no permite evaluar.")
    if o.estado is Estado.NO_APLICA:
        lineas.append(f"  - Criterio no cumplido: {'; '.join(c.texto for c in o.criterios_no_cumplidos)}")
    lineas += [f"  - Nota: {n}" for n in o.notas]
    return lineas


def _bloque_evidencia(e: Evidencia) -> list[str]:
    encabezado = f"**[{e.id}] {ETIQUETA_FUENTE[e.fuente]}**"
    if e.sobre_otras_personas:
        encabezado += " _(describe a otras personas, no a esta paciente)_"
    lineas = [f"{encabezado} — {e.titulo}", f"> {e.detalle}"]
    meta = []
    if e.nivel:
        meta.append(f"nivel {e.nivel}")
    if e.referencias:
        meta.append(", ".join(e.referencias))
    if e.opciones:
        meta.append("relacionada con: " + ", ".join(opcion_por_id(i).nombre for i in e.opciones))
    if e.url:
        meta.append(e.url)
    if meta:
        lineas.append(f"> _{' · '.join(meta)}_")
    return lineas


def dossier_markdown(
    perfil: PerfilClinico,
    origen: dict[str, str],
    descartados: tuple[Traza, ...],
    evaluacion: Evaluacion,
    evidencias: tuple[Evidencia, ...],
    fuentes_fallidas: tuple[FuenteFallida, ...],
) -> str:
    md: list[str] = ["## Perfil interpretado", "", "| Dato | Valor | Origen |", "|---|---|---|"]
    filas = filas_perfil(perfil)
    md += [f"| {dato} | {valor} | {origen.get(clave, '—')} |" for clave, dato, valor in filas] or ["| — | Sin datos reconocidos | — |"]
    if descartados:
        md += ["", "Mencionado pero **no tomado como dato**:"]
        md += [f"- {_etiqueta_campo(t.campo)}: «{t.fragmento}»" for t in descartados]
    md += [
        "",
        f"**Subtipo molecular:** {_describir_subtipo(evaluacion, perfil)}",
        f"**Escenario:** {_describir_escenario(evaluacion)} · **Estadio:** {evaluacion.estadio or 'no determinado'}",
    ]
    if evaluacion.datos_faltantes:
        md.append(f"**Datos básicos sin registrar:** {', '.join(P.etiqueta_dato(d) for d in evaluacion.datos_faltantes)}")

    md += ["", f"## Evaluación del motor de reglas (v{evaluacion.version_reglas} — {evaluacion.estado_validacion})"]
    for titulo, tipo in (("Estudios", "estudio"), ("Opciones terapéuticas", "tratamiento")):
        abiertas = [o for o in evaluacion.opciones if o.opcion.tipo == tipo and o.estado is not Estado.NO_APLICA]
        abiertas.sort(key=lambda o: o.estado is not Estado.CUMPLE)
        md += ["", f"### {titulo}"]
        md += [linea for o in abiertas for linea in _linea_opcion(o)] or ["- Ninguna abierta para este perfil."]
    no_aplican = evaluacion.por_estado(Estado.NO_APLICA)
    if no_aplican:
        md += ["", "### No aplican a este perfil"]
        md += [linea for o in no_aplican for linea in _linea_opcion(o)]

    if evaluacion.valor_informacion:
        md += ["", "## Valor de la información pendiente"]
        for i, v in enumerate(evaluacion.valor_informacion, start=1):
            decide = (
                f"; completándolo solo, quedan decididas: {', '.join(opcion_por_id(x).nombre for x in v.opciones_que_decide)}"
                if v.opciones_que_decide else ""
            )
            md.append(f"{i}. **{v.etiqueta}** — bloquea {len(v.opciones_bloqueadas)} opción(es){decide}")

    md += _seccion_evidencia(evidencias, fuentes_fallidas)
    md += ["", "---", _AVISO + " Los estados provienen de reglas versionadas que deben ser validadas por un comité oncológico._"]
    return "\n".join(md)


_AVISO = "_Pharox no emite diagnósticos ni prescribe tratamientos; la decisión es siempre del médico."


def _seccion_evidencia(evidencias: tuple[Evidencia, ...], fuentes_fallidas: tuple[FuenteFallida, ...]) -> list[str]:
    md = ["", "## Evidencia de la base de conocimiento"]
    if evidencias:
        for e in evidencias:
            md += [""] + _bloque_evidencia(e)
    else:
        md.append("La base no devolvió evidencia para esta consulta.")
    if fuentes_fallidas:
        md += ["", "Fuentes no consultadas: " + "; ".join(f"{f.fuente} ({f.error})" for f in fuentes_fallidas)]
    return md


def dossier_general_markdown(
    perfil: PerfilClinico,
    farmacos: tuple[str, ...],
    motivo_modo: str,
    evidencias: tuple[Evidencia, ...],
    fuentes_fallidas: tuple[FuenteFallida, ...],
) -> str:
    """Dossier de una consulta general: qué se reconoció en la pregunta y la evidencia recuperada."""
    md = ["## Consulta general", "", f"_{motivo_modo[0].upper()}{motivo_modo[1:]}: no se aplica el catálogo de reglas a un perfil._"]
    reconocido = [f"{dato}: {valor}" for _clave, dato, valor in filas_perfil(perfil)]
    subtipos = P.subtipos_compatibles(perfil)
    if len(subtipos) < len(P.SUBTIPOS):
        reconocido.append("Subtipo(s): " + " · ".join(ETIQUETA_SUBTIPO[s] for s in subtipos))
    if perfil.genes_mencionados:
        reconocido.append("Genes: " + ", ".join(perfil.genes_mencionados))
    if farmacos:
        reconocido.append("Fármacos: " + ", ".join(farmacos))
    if reconocido:
        md += ["", "**Términos reconocidos en la pregunta** (dirigen la búsqueda):"] + [f"- {r}" for r in reconocido]
    md += _seccion_evidencia(evidencias, fuentes_fallidas)
    md += ["", "---", _AVISO + "_"]
    return "\n".join(md)


def bloque_verificacion(hallazgos: tuple[str, ...]) -> str:
    """Aviso visible cuando la redacción menciona datos que no están en la evidencia (política 'advertir')."""
    lineas = ["> ⚠️ **Verificación automática:** la redacción del modelo menciona datos que no están en la evidencia recuperada. Tratalos como no respaldados:"]
    lineas += [f"> - {h}" for h in hallazgos]
    return "\n".join(lineas)
