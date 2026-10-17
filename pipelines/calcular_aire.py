"""Calidad del aire en Chamberí por mes y hora (dato real; sustituye a «Calidad del aire: Buena»).

Entrada : data/raw/calidad_aire.csv  (Ayuntamiento de Madrid, «Calidad del aire. Datos horarios»,
          fichero desde 2025; formato PROVINCIA;MUNICIPIO;ESTACION;MAGNITUD;...;H01;V01;...;H24;V24)
Salida  : data/processed/aire_mes_hora.csv
          mes, hora, no2_media, pm10_media, pm25_media, categoria, pct_regular_o_peor, n_horas

Método
  1. Estación 38 (Cuatro Caminos, Chamberí). Contaminantes: NO2 (8), PM10 (10), PM2.5 (9).
  2. Solo horas validadas (V = "V"). Hxx es la hora que TERMINA a las xx: H01 = 00:00-01:00,
     guardada como hora 0.
  3. Cada hora se clasifica con el Índice de Calidad del Aire (ICA) del MITECO:
       NO2 con el valor horario; PM10 y PM2.5 con la media móvil de 24 h (mínimo 18 h válidas).
     La categoría de la hora es la PEOR de los tres contaminantes (así funciona el ICA).
  4. Por mes y hora del día (todas las fechas disponibles juntas):
       - medias de NO2, PM10 y PM2.5 (µg/m3),
       - categoria  = ICA aplicado a esas medias (lo «típico» a esa hora),
       - pct_regular_o_peor = % de horas en «Regular» o peor (lo que distingue horas malas;
         las medias salen casi siempre «Buena»).

Limitación: patrón histórico (ene 2025 - sep 2026) de UNA estación, no el aire de hoy ni de cada
calle. Sin ozono: la estación 38 no lo mide.

Ejecutar desde la raíz del proyecto:
    python pipelines/calcular_aire.py
"""
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent.parent
ENTRADA = BASE / "data" / "raw" / "calidad_aire.csv"
SALIDA = BASE / "data" / "processed" / "aire_mes_hora.csv"

ESTACION = 38                                   # Cuatro Caminos (Chamberí)
MAGNITUDES = {8: "no2", 10: "pm10", 9: "pm25"}

# ICA del MITECO (µg/m3): límite superior de cada categoría, en orden.
# Fuente: Resolución de la D.G. de Calidad y Evaluación Ambiental que actualiza el ICA (Orden TEC/351/2019).
CATEGORIAS = ["Buena", "Razonablemente buena", "Regular", "Desfavorable",
              "Muy desfavorable", "Extremadamente desfavorable"]
LIMITES = {
    "no2":  [40, 90, 120, 230, 340],
    "pm10": [20, 40, 50, 100, 150],
    "pm25": [10, 20, 25, 50, 75],
}
REGULAR = CATEGORIAS.index("Regular")


def indice(contaminante, valor):
    """Posición de la categoría ICA (0 = Buena ... 5 = Extremadamente desfavorable)."""
    if pd.isna(valor):
        return pd.NA
    for i, limite in enumerate(LIMITES[contaminante]):
        if valor <= limite:
            return i
    return len(LIMITES[contaminante])


def leer_horario():
    df = pd.read_csv(ENTRADA, sep=";", encoding="utf-8-sig", dtype=str)
    df.columns = [c.strip().lower() for c in df.columns]
    df = df[(df["estacion"].astype(int) == ESTACION) & df["magnitud"].astype(int).isin(MAGNITUDES)]
    if df.empty:
        raise SystemExit(f"No hay datos de la estación {ESTACION} en {ENTRADA.name}")

    # De formato ancho (H01..H24) a una fila por hora
    partes = []
    for h in range(1, 25):
        p = df[["magnitud", "ano", "mes", "dia"]].copy()
        p["hora"] = h - 1
        p["valor"] = pd.to_numeric(df[f"h{h:02d}"].str.replace(",", "."), errors="coerce")
        p["valida"] = df[f"v{h:02d}"].str.strip() == "V"
        partes.append(p)
    largo = pd.concat(partes, ignore_index=True)
    largo.loc[~largo["valida"], "valor"] = pd.NA
    largo["contaminante"] = largo["magnitud"].astype(int).map(MAGNITUDES)
    largo["fecha_hora"] = pd.to_datetime(
        largo["ano"] + "-" + largo["mes"] + "-" + largo["dia"]) + pd.to_timedelta(largo["hora"], unit="h")

    # Una columna por contaminante, una fila por fecha y hora
    tabla = largo.pivot_table(index="fecha_hora", columns="contaminante", values="valor", aggfunc="first")
    tabla = tabla.reindex(pd.date_range(tabla.index.min(), tabla.index.max(), freq="h"))
    return tabla, largo["valida"].mean()


def main():
    t, pct_validas = leer_horario()

    # Valor que usa el ICA: NO2 horario; partículas, media móvil de 24 h
    ica = pd.DataFrame(index=t.index)
    ica["no2"] = t["no2"]
    for c in ("pm10", "pm25"):
        ica[c] = t[c].rolling(24, min_periods=18).mean()

    cat = pd.DataFrame({c: ica[c].map(lambda v, c=c: indice(c, v)) for c in MAGNITUDES.values()})
    t["cat_hora"] = cat.max(axis=1, skipna=True)        # la peor de las tres
    t["mes"] = t.index.month
    t["hora"] = t.index.hour
    t[["pm10_24h", "pm25_24h"]] = ica[["pm10", "pm25"]]

    validas = t.dropna(subset=["cat_hora"])
    g = validas.groupby(["mes", "hora"])
    res = pd.DataFrame({
        "no2_media": g["no2"].mean(),
        "pm10_media": g["pm10_24h"].mean(),
        "pm25_media": g["pm25_24h"].mean(),
        "pct_regular_o_peor": g["cat_hora"].apply(lambda s: (s.astype(int) >= REGULAR).mean() * 100),
        "n_horas": g.size(),
    }).reset_index()

    res["categoria"] = [
        CATEGORIAS[max(indice("no2", a), indice("pm10", b), indice("pm25", c))]
        for a, b, c in zip(res["no2_media"], res["pm10_media"], res["pm25_media"])
    ]
    for c in ("no2_media", "pm10_media", "pm25_media", "pct_regular_o_peor"):
        res[c] = res[c].round(1)
    res = res[["mes", "hora", "no2_media", "pm10_media", "pm25_media",
               "categoria", "pct_regular_o_peor", "n_horas"]]

    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    res.to_csv(SALIDA, index=False, encoding="utf-8")

    # Resumen por pantalla
    print(f"Estación {ESTACION} | {t.index.min():%Y-%m-%d} a {t.index.max():%Y-%m-%d} "
          f"| horas válidas: {pct_validas:.0%}")
    print(f"Guardado {len(res)} filas (12 meses x 24 horas) en {SALIDA.relative_to(BASE)}")
    print(f"Categorías típicas: {res['categoria'].value_counts().to_dict()}\n")
    print("Mejor y peor hora para pasear (entre 8 y 21 h), por mes:")
    dia = res[res["hora"].between(8, 21)]
    for mes, m in dia.groupby("mes"):
        mejor = m.loc[m["pct_regular_o_peor"].idxmin()]
        peor = m.loc[m["pct_regular_o_peor"].idxmax()]
        print(f"  mes {mes:>2}  mejor {int(mejor['hora']):>2} h ({mejor['pct_regular_o_peor']:>4}% regular o peor)"
              f"   peor {int(peor['hora']):>2} h ({peor['pct_regular_o_peor']:>4}%)")


if __name__ == "__main__":
    main()