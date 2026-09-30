"""
:RegistroTumor (registro real del Hospital Central - Mendoza) → PerfilClinico.

Traduce el vocabulario de la planilla de origen (ver
app/gold/registro_tumores_to_neo4j.py) sin inferir nada que la planilla no
diga. En particular:
- "Se ignora", "IGN", "X", "..." → dato ausente (None), nunca negativo.
- HER2 "Dudoso" (equívoco) → estado HER2 sin determinar. No se asume IHQ 2+:
  la planilla no informa el score.
- Se prefiere T/N patológico cuando existe (conteo real de ganglios por pN);
  si no, el clínico.
"""
from __future__ import annotations

from app.dominio.perfil import PerfilClinico, normalizar_estadio

_SIN_DATO = {"", "...", "IGN", "X", "N/D", "SE IGNORA", "DESCONOCIDO", "SELECCIONE..."}

_GRADO = {
    "bien diferenciado": 1,
    "moderadamente diferenciado": 2,
    "pobremente diferenciado": 3,
}


def _limpio(valor) -> str | None:
    if valor is None:
        return None
    texto = str(valor).strip()
    return None if texto.upper() in _SIN_DATO else texto


def _receptor(valor) -> str | None:
    v = _limpio(valor)
    if v == "(+)":
        return "positivo"
    if v == "(-)":
        return "negativo"
    return None


def _categoria(valor, letra: str) -> str | None:
    """"2" → "T2", "1b" → "T1b", "IS" → "Tis"."""
    v = _limpio(valor)
    if v is None:
        return None
    if v.upper() == "IS":
        return "Tis" if letra == "T" else None
    return f"{letra}{v.lower()}"


def _ecog(valor) -> int | None:
    v = _limpio(valor)
    if v and v[0].isdigit():
        return int(v[0])
    return None


def perfil_desde_registro(registro: dict) -> PerfilClinico:
    t_pat = _categoria(registro.get("estadio_patologico_t"), "T")
    n_pat = _categoria(registro.get("estadio_patologico_n"), "N")
    t_cli = _categoria(registro.get("estadio_clinico_t"), "T")
    n_cli = _categoria(registro.get("estadio_clinico_n"), "N")
    m = _limpio(registro.get("estadio_clinico_m"))

    sexo = {"Mujer": "F", "Hombre": "M"}.get(_limpio(registro.get("sexo")) or "")
    her2 = _receptor(registro.get("her2"))
    grado = _GRADO.get((_limpio(registro.get("grado_diferenciacion")) or "").lower())

    return PerfilClinico(
        edad=registro.get("edad"),
        sexo=sexo,
        re=_receptor(registro.get("receptor_estrogeno")),
        rp=_receptor(registro.get("receptor_progesterona")),
        her2_informado=her2,
        grado=grado,
        t=t_pat or t_cli,
        n=n_pat or n_cli,
        n_patologico=True if n_pat else (False if n_cli else None),
        m=f"M{m}" if m in ("0", "1") else None,
        estadio=normalizar_estadio(_limpio(registro.get("estadio_clinico")) or _limpio(registro.get("estadio_patologico"))),
        ecog=_ecog(registro.get("ecog")),
    )
