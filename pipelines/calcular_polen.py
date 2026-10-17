"""Calendario real de polen para Chamberí (sustituye al «Polen: Medio» inventado).

Entrada : data/raw/polen*.csv  (Comunidad de Madrid, histórico de mediciones de polen).
          Lee TODOS los ficheros que empiecen por "polen" (polen.csv, polen_2025.csv, polen_2024.csv...),
          así basta con dejar los años descargados en data/raw/. Las fechas repetidas se cuentan una vez.
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

Limitación: es un PATRÓN ESTACIONAL de los meses disponibles en los ficheros, no el polen
de hoy ni de una calle concreta. Los meses sin datos no aparecen en la salida.

Ejecutar desde la raíz del proyecto:
    python pipelines/calcular_polen.py
"""
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent.parent
RAW = BASE / "data" / "raw"
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


COLUMNAS = ["captador", "fecha_lectura", "tipo_polinico", "granos_de_polen_x_metro_cubico"]


def leer_fichero(ruta):
    """Lee un CSV de polen aunque venga en otra codificación (los años antiguos usan latin-1)
    o con otro separador, y deja las columnas con los nombres de polen.csv."""
    for codificacion in ("utf-8-sig", "latin-1"):
        try:
            df = pd.read_csv(ruta, sep=None, engine="python", encoding=codificacion)
            break
        except UnicodeDecodeError:
            continue
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    # Por si algún año trae los nombres algo distintos
    buscar = {"captador": "captador", "fecha": "fecha_lectura", "tipo": "tipo_polinico", "granos": "granos_de_polen_x_metro_cubico"}
    renombrar = {}
    for clave, destino in buscar.items():
        col = next((c for c in df.columns if clave in c), None)
        if col:
            renombrar[col] = destino
    df = df.rename(columns=renombrar)
    faltan = [c for c in COLUMNAS if c not in df.columns]
    if faltan:
        raise SystemExit(f"{ruta.name}: no encuentro las columnas {faltan}. Columnas del fichero: {list(df.columns)}")
    df = df[COLUMNAS].copy()
    # Fechas: primero AAAA-MM-DD (formato de polen.csv); si no encaja, DD/MM/AAAA (años antiguos)
    original = df["fecha_lectura"].astype(str).str.strip()
    # AAAA-MM-DD (2026) o AAAA/MM/DD (2024 y 2025): se unifican las barras a guiones
    fecha = pd.to_datetime(original.str[:10].str.replace("/", "-"), format="%Y-%m-%d", errors="coerce")
    resto = fecha.isna()
    if resto.any():   # cualquier otro formato (1/1/2024, 01-01-2024 0:00...), con el día delante
        fecha[resto] = pd.to_datetime(original[resto], dayfirst=True, format="mixed", errors="coerce")
    df["fecha_lectura"] = fecha.dt.strftime("%Y-%m-%d")
    df.attrs["ejemplo_fecha"] = original.iloc[0] if len(original) else ""
    df["granos_de_polen_x_metro_cubico"] = pd.to_numeric(
        df["granos_de_polen_x_metro_cubico"].astype(str).str.replace(",", "."), errors="coerce")
    df["captador"] = df["captador"].astype(str).str.strip().str.upper()
    df["tipo_polinico"] = df["tipo_polinico"].astype(str).str.strip()
    return df


def main():
    ficheros = sorted(RAW.glob("polen*.csv"))
    if not ficheros:
        raise SystemExit(f"No hay ningún polen*.csv en {RAW}")
    partes = []
    for f in ficheros:
        p = leer_fichero(f)
        utiles = p["captador"].isin(CAPTADORES_MADRID) & p["tipo_polinico"].isin(UMBRALES) & p["fecha_lectura"].notna()
        print(f"  {f.name}: {len(p)} filas, {int(utiles.sum())} útiles (Madrid ciudad, tipos con umbral, fecha válida)")
        if not utiles.any():
            print(f"    AVISO: nada útil en {f.name}. Captadores: {sorted(p['captador'].unique())[:12]}"
                  f" | tipos: {sorted(p['tipo_polinico'].unique())[:8]}..."
                  f" | fecha de ejemplo: {p.attrs.get('ejemplo_fecha')!r}"
                  f" | fechas válidas: {int(p['fecha_lectura'].notna().sum())}")
        partes.append(p)
    df = pd.concat(partes, ignore_index=True)
    df = df.dropna(subset=["fecha_lectura"])
    df = df.drop_duplicates(["captador", "fecha_lectura", "tipo_polinico"])
    print("Ficheros leídos:", ", ".join(f.name for f in ficheros))
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