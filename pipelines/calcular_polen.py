"""Calendario real de polen para Chamberí (sustituye al «Polen: Medio» inventado).

Entrada : data/raw/polen.csv  (Comunidad de Madrid, histórico de mediciones de polen)
Salida  : data/processed/polen_mes.csv  -> columnas de la tabla Oracle POLEN_MES
          (tipo_polen, mes, nivel_medio, nivel)

Método
  1. Se usan los dos captadores de Madrid ciudad (AYTM y FACF); Chamberí queda entre ambos.
  2. Por cada tipo de polen y día se promedian los dos captadores (granos/m3).
  3. Por cada tipo y mes se calcula la media de esas concentraciones diarias -> nivel_medio.
  4. nivel_medio se clasifica con los umbrales oficiales del Ayuntamiento de Madrid
     (Informe de análisis del polen, tabla de niveles; percentiles PALINOCAM 2010-2019):
        bajo  : por debajo del umbral «Medio»
        medio : entre «Medio» y «Alto»
        alto  : «Alto» o superior (incluye «Muy alto»)
  5. Los tipos sin umbral oficial no se clasifican (se omiten).

Limitación: es un PATRÓN ESTACIONAL de los meses disponibles en polen.csv, no el polen
de hoy ni de una calle concreta. Los meses sin datos no aparecen en la salida.

Ejecutar desde la raíz del proyecto:
    python pipelines/calcular_polen.py
"""
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent.parent
ENTRADA = BASE / "data" / "raw" / "polen.csv"
SALIDA = BASE / "data" / "processed" / "polen_mes.csv"

CAPTADORES_MADRID = ["AYTM", "FACF"]

# Umbrales oficiales (granos/m3): (Medio, Alto). Fuente: Ayuntamiento de Madrid,
# Informe de análisis del polen (nov. 2025), niveles por percentiles PALINOCAM 2010-2019.
UMBRALES = {
    "Plátano de paseo": (81, 248),
    "Cupresáceas/Taxáceas": (59, 171),
    "Quercus": (55, 164),
    "Olivo": (34, 74),
    "Gramíneas": (26, 53),
    "Pinos": (54, 88),
    "Populus": (24, 46),
    "Olmos": (18, 38),
    "Plantago": (21, 32),
    "Urticaceae (Ortigas)": (10, 13),
    "Arce": (14, 27),
    "Quenopodiáceas/Amarantáceas": (8, 10),
    "Fresno": (20, 33),
    "Moreras": (15, 33),
    "Rumex (Acederas)": (9, 14),
}


def clasificar(tipo, valor):
    medio, alto = UMBRALES[tipo]
    if valor >= alto:
        return "alto"
    if valor >= medio:
        return "medio"
    return "bajo"


def main():
    df = pd.read_csv(ENTRADA, sep=";", encoding="utf-8-sig")
    df = df[df["captador"].isin(CAPTADORES_MADRID) & df["tipo_polinico"].isin(UMBRALES)].copy()
    df["fecha"] = pd.to_datetime(df["fecha_lectura"].str[:10])
    df["granos"] = pd.to_numeric(df["granos_de_polen_x_metro_cubico"], errors="coerce")
    df = df.dropna(subset=["granos"])

    # Media de los dos captadores para cada día y tipo
    diario = df.groupby(["tipo_polinico", "fecha"], as_index=False)["granos"].mean()
    diario["mes"] = diario["fecha"].dt.month

    # Media mensual de la concentración diaria
    mensual = (diario.groupby(["tipo_polinico", "mes"], as_index=False)
                     .agg(nivel_medio=("granos", "mean"), dias=("granos", "size")))
    mensual["nivel_medio"] = mensual["nivel_medio"].round(1)
    mensual["nivel"] = [clasificar(t, v) for t, v in zip(mensual["tipo_polinico"], mensual["nivel_medio"])]
    mensual = mensual.rename(columns={"tipo_polinico": "tipo_polen"})

    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    mensual[["tipo_polen", "mes", "nivel_medio", "nivel"]].to_csv(SALIDA, index=False, encoding="utf-8")

    # Resumen por pantalla
    print(f"Fechas: {diario['fecha'].min().date()} a {diario['fecha'].max().date()} "
          f"| captadores {CAPTADORES_MADRID} | {mensual['tipo_polen'].nunique()} tipos")
    print(f"Guardado {len(mensual)} filas en {SALIDA.relative_to(BASE)}")
    destacados = mensual[mensual["nivel"] != "bajo"].sort_values(["mes", "tipo_polen"])
    print("\nMeses con nivel medio o alto:")
    for _, f in destacados.iterrows():
        print(f"  mes {f['mes']:>2}  {f['tipo_polen']:<28} {f['nivel_medio']:>7} granos/m3  -> {f['nivel']}")


if __name__ == "__main__":
    main()