"""Aviso ambiental (polen y calidad del aire) para un mes y una hora.

Lee los datos REALES que están en Oracle a través de la API de solo lectura:
    .../dataset/polen/?mes=M   -> tabla POLEN_MES      (pipelines/calcular_polen.py)
    .../dataset/aire/?mes=M    -> tabla AIRE_MES_HORA  (pipelines/calcular_aire.py)
Si la API no responde, usa la copia local data/processed/*.csv (los mismos datos).

Son PATRONES HABITUALES (polen 2024-2026, aire 2025-2026), no la medición de hoy:
el texto que se devuelve lo dice siempre.
"""
import json
import os
import urllib.request
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import pandas as pd

RAIZ = Path(__file__).resolve().parent.parent
PROCESADOS = RAIZ / "data" / "processed"
ORDS_URL_POR_DEFECTO = "https://g65cd3d29f3f6ee-madcompanadb.adb.eu-madrid-1.oraclecloudapps.com/ords/maddata"

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
ORDEN_POLEN = {"bajo": 0, "medio": 1, "alto": 2}
HORAS_PASEO = range(9, 21)        # para recomendar la mejor hora: de 9 a 20 h


def _api(recurso, mes):
    """Filas de la API de Oracle (ORDS devuelve {"items": [...]})."""
    base = os.environ.get("ORDS_URL", ORDS_URL_POR_DEFECTO).rstrip("/")
    url = f"{base}/dataset/{recurso}/?mes={mes}"
    with urllib.request.urlopen(url, timeout=10) as r:
        return pd.DataFrame(json.loads(r.read().decode("utf-8"))["items"])


@lru_cache(maxsize=32)
def _datos_mes(mes):
    """(polen, aire, origen) de un mes: primero la API; si falla, los CSV locales."""
    try:
        polen, aire = _api("polen", mes), _api("aire", mes)
        if polen.empty or aire.empty:
            raise ValueError("la API devolvió 0 filas")
        return polen, aire, "API de Oracle"
    except Exception as e:  # sin conexión, API caída...
        print(f"Aviso ambiental: no se pudo leer la API ({e}). Uso data/processed/.")
        polen = pd.read_csv(PROCESADOS / "polen_mes.csv")
        aire = pd.read_csv(PROCESADOS / "aire_mes_hora.csv")
        return polen[polen["mes"] == mes], aire[aire["mes"] == mes], "copia local"


def aviso_ambiental(mes=None, hora=None):
    """Polen del mes y calidad del aire de esa hora, con un texto listo para mostrar."""
    ahora = datetime.now()
    mes = int(mes or ahora.month)
    hora = int(ahora.hour if hora is None else hora)
    polen, aire, origen = _datos_mes(mes)

    # ---- polen: tipos en nivel medio o alto este mes
    polen = polen.assign(orden=polen["nivel"].map(ORDEN_POLEN))
    destacados = polen[polen["orden"] > 0].sort_values(["orden", "nivel_medio"], ascending=False)
    nivel_polen = "bajo" if destacados.empty else destacados.iloc[0]["nivel"]
    tipos = [{"tipo": r.tipo_polen, "nivel": r.nivel, "granos_m3": float(r.nivel_medio)}
             for r in destacados.itertuples()]

    # ---- aire: esta hora y la mejor hora para pasear del mes (menos NO2)
    fila = aire[aire["hora"] == hora]
    if fila.empty:
        raise ValueError(f"No hay datos de calidad del aire para el mes {mes} a las {hora} h")
    fila = fila.iloc[0]
    paseo = aire[aire["hora"].isin(HORAS_PASEO)]
    mejor = paseo.loc[paseo["no2_media"].idxmin()]

    # ---- texto
    if destacados.empty:
        txt_polen = f"Polen: bajo en {MESES[mes - 1]}."
    else:
        lista = ", ".join(f"{t['tipo'].lower()} ({t['nivel']})" for t in tipos[:3])
        txt_polen = f"Polen en {MESES[mes - 1]}: {lista}."
    txt_aire = (f"Aire a las {hora} h: {fila['categoria'].lower()} "
                f"(NO₂ {fila['no2_media']:.0f} µg/m³).")
    if int(mejor["hora"]) != hora and mejor["no2_media"] < fila["no2_media"] * 0.8:
        txt_aire += (f" La mejor hora para pasear suele ser las {int(mejor['hora'])} h "
                     f"(NO₂ {mejor['no2_media']:.0f} µg/m³).")
    texto = f"{txt_polen} {txt_aire} Es lo habitual en Chamberí según datos oficiales, no la medición de hoy."

    return {
        "mes": mes,
        "hora": hora,
        "polen": {"nivel": nivel_polen, "tipos_destacados": tipos},
        "aire": {
            "categoria": fila["categoria"],
            "no2": float(fila["no2_media"]),
            "pm10": float(fila["pm10_media"]),
            "pm25": float(fila["pm25_media"]),
            "pct_horas_regular_o_peor": float(fila["pct_regular_o_peor"]),
            "mejor_hora_paseo": int(mejor["hora"]),
        },
        "texto": texto,
        "origen_datos": origen,
    }