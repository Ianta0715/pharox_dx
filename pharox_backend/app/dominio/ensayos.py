"""
Paso determinístico de elegibilidad a ensayos clínicos (Pharox_Documento_v5 §11 Capa 1).

Compara SOLO campos estructurados (estado de reclutamiento, sexo, rango
etario, subtipo molecular). Nunca concluye HABILITA: con los datos
estructurados disponibles solo se puede descartar con confianza, no confirmar
elegibilidad completa. El resultado es EXCLUYE (descarte objetivo) o
CONDICIONA (quedan criterios en texto libre por verificar).

Vive en el dominio porque lo usan dos caminos: el batch que persiste las
relaciones HABILITA/CONDICIONA/EXCLUYE_TRIAL (app/gold/reglas_elegibilidad_trials.py)
y el copiloto, que filtra en vivo los ensayos candidatos de una consulta.
"""
from __future__ import annotations

import re

from app.dominio import perfil as P
from app.dominio.perfil import PerfilClinico

_MAPA_SEXO_PACIENTE = {"Mujer": "FEMALE", "Hombre": "MALE", "F": "FEMALE", "M": "MALE"}


def veredicto(veredicto: str, motivo: str, regla: str, criterio_pendiente: str | None = None) -> dict:
    return {"veredicto": veredicto, "motivo": motivo, "regla": regla, "criterio_pendiente": criterio_pendiente}


def evaluar_criterios_deterministicos(paciente: dict, ensayo: dict) -> dict:
    """
    `paciente` y `ensayo` usan las mismas claves que las propiedades de
    :RegistroTumor y :EnsayoClinico (ver paciente_para_ensayos para armarlo
    desde un PerfilClinico).
    """
    if ensayo.get("estado") != "RECRUITING":
        return veredicto(
            "EXCLUYE",
            f"El ensayo no está reclutando actualmente (estado: {ensayo.get('estado') or 'desconocido'}).",
            regla="estado_reclutamiento",
        )

    sexo_paciente = _MAPA_SEXO_PACIENTE.get((paciente.get("sexo") or "").strip())
    sexo_ensayo = (ensayo.get("sexo") or "ALL").upper()
    if sexo_paciente and sexo_ensayo != "ALL" and sexo_paciente != sexo_ensayo:
        return veredicto(
            "EXCLUYE",
            f"El ensayo solo acepta sexo {sexo_ensayo.lower()} (paciente: {sexo_paciente.lower()}).",
            regla="sexo",
        )

    edad = paciente.get("edad")
    edad_min = ensayo.get("edad_minima_anios")
    edad_max = ensayo.get("edad_maxima_anios")
    if edad is not None and edad_min is not None and edad < edad_min:
        return veredicto("EXCLUYE", f"Edad ({edad}) por debajo del mínimo del ensayo ({edad_min} años).", regla="edad_minima")
    if edad is not None and edad_max is not None and edad > edad_max:
        return veredicto("EXCLUYE", f"Edad ({edad}) por encima del máximo del ensayo ({edad_max} años).", regla="edad_maxima")

    subtipo_paciente = paciente.get("subtipo_molecular") or "desconocido"
    subtipos_ensayo = ensayo.get("subtipos_relacionados") or []
    if subtipo_paciente != "desconocido" and subtipos_ensayo and subtipo_paciente not in subtipos_ensayo:
        return veredicto(
            "EXCLUYE",
            f"Perfil molecular de la paciente ({subtipo_paciente}) no coincide con el perfil del ensayo ({', '.join(subtipos_ensayo)}).",
            regla="subtipo_molecular",
        )

    return veredicto(
        "CONDICIONA",
        "Ningún criterio estructurado (reclutamiento, sexo, edad, subtipo molecular) descarta a la paciente.",
        regla="filtros_estructurados_ok",
        criterio_pendiente="Quedan por verificar los criterios en texto libre del protocolo (líneas de tratamiento previas, biomarcadores adicionales, comorbilidades, etc.).",
    )


def paciente_para_ensayos(perfil: PerfilClinico) -> dict:
    """Proyecta un PerfilClinico sobre las claves que espera evaluar_criterios_deterministicos."""
    return {
        "edad": perfil.edad,
        "sexo": perfil.sexo,
        "subtipo_molecular": P.subtipo_molecular(perfil) or "desconocido",
    }


# Con límites de palabra: "stage i" como subcadena también matchearía "stage iv".
_PATRON_METASTASICO = re.compile(r"\b(metastatic|advanced|unresectable|stage iv|recurrent)\b")
_PATRON_TEMPRANO = re.compile(r"\b(early|neoadjuvant|adjuvant|operable|localized|primary breast)\b|\bstage i{1,3}[abc]?\b")


def escenario_del_ensayo(ensayo: dict) -> str | None:
    """'metastasico', 'temprano', 'ambos' o None, según el texto del protocolo."""
    texto = " ".join(
        [ensayo.get("titulo") or "", ensayo.get("resumen") or ""]
        + list(ensayo.get("condiciones") or [])
    ).lower()
    metastasico = bool(_PATRON_METASTASICO.search(texto))
    temprano = bool(_PATRON_TEMPRANO.search(texto))
    if metastasico and temprano:
        return "ambos"
    if metastasico:
        return "metastasico"
    if temprano:
        return "temprano"
    return None
