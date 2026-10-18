"""Carga en Oracle las tablas calculadas por Python (polen, calidad del aire y centros de salud).

    data/processed/polen_mes.csv      -> POLEN_MES      (pipelines/calcular_polen.py)
    data/processed/especie_polen.csv  -> ESPECIE_POLEN  (pipelines/especie_polen.py)
    data/processed/aire_mes_hora.csv  -> AIRE_MES_HORA  (pipelines/calcular_aire.py)
    data/processed/centros_salud.csv  -> CENTROS_SALUD  (pipelines/centros_salud.py)

Antes: ejecutar oracle/07_aire.sql (crea AIRE_MES_HORA) y oracle/11_centros_salud.sql
(crea CENTROS_SALUD). POLEN_MES y ESPECIE_POLEN ya existen desde oracle/02_tablas.sql.

Usa la misma configuración que pipelines/cargar_oracle.py (fichero .env con ORACLE_USER,
ORACLE_PASSWORD, ORACLE_DSN, ORACLE_WALLET_DIR, ORACLE_WALLET_PASSWORD). Necesita el
puerto 1522: no funciona en eduroam.

Solo toca esas cuatro tablas (no borra POI ni TRAMOS). Es idempotente: cada ejecución las
vacía y las vuelve a llenar con los CSV.

Uso:
    python pipelines/cargar_ambiental.py --dry-run   # comprueba los CSV sin conectarse
    python pipelines/cargar_ambiental.py             # carga en Oracle
"""
import argparse
import os
import sys
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent.parent
PROC = BASE / "data" / "processed"
NIVELES = {"bajo", "medio", "alto"}
COLS_CENTROS = ["centro_id", "nombre", "tipo_centro", "direccion", "url", "lat", "lon"]
CATEGORIAS = {"Buena", "Razonablemente buena", "Regular", "Desfavorable",
              "Muy desfavorable", "Extremadamente desfavorable"}
COLS_AIRE = ["mes", "hora", "no2_media", "pm10_media", "pm25_media",
             "categoria", "pct_regular_o_peor", "n_horas"]


def cargar_env():
    env = BASE / ".env"
    if env.exists():
        for linea in env.read_text(encoding="utf-8").splitlines():
            if "=" in linea and not linea.strip().startswith("#"):
                k, v = linea.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def num(v):
    """NaN -> None y numpy -> float de Python (oracledb no acepta numpy)."""
    return None if pd.isna(v) else float(v)


def texto(v):
    """NaN -> None (columna de texto opcional)."""
    return None if pd.isna(v) else str(v)


def leer():
    polen = pd.read_csv(PROC / "polen_mes.csv", encoding="utf-8")
    especies = pd.read_csv(PROC / "especie_polen.csv", encoding="utf-8")
    aire = pd.read_csv(PROC / "aire_mes_hora.csv", encoding="utf-8")
    centros = pd.read_csv(PROC / "centros_salud.csv", encoding="utf-8")

    # Las mismas reglas que las restricciones de las tablas en Oracle
    assert list(polen.columns) == ["tipo_polen", "mes", "nivel_medio", "nivel"], "Columnas de polen_mes.csv"
    assert list(especies.columns) == ["especie", "tipo_polen"], "Columnas de especie_polen.csv"
    assert list(aire.columns) == COLS_AIRE, "Columnas de aire_mes_hora.csv"
    assert polen["mes"].between(1, 12).all(), "polen: meses fuera de 1-12"
    assert set(polen["nivel"]) <= NIVELES, f"polen: niveles no válidos {set(polen['nivel']) - NIVELES}"
    assert not polen.duplicated(["tipo_polen", "mes"]).any(), "polen: tipo_polen + mes repetidos"
    assert especies["especie"].is_unique, "especies repetidas"
    sin_calendario = set(especies["tipo_polen"]) - set(polen["tipo_polen"])
    assert not sin_calendario, f"Tipos de polen sin calendario: {sin_calendario}"
    assert aire["mes"].between(1, 12).all() and aire["hora"].between(0, 23).all(), "aire: mes u hora fuera de rango"
    assert set(aire["categoria"]) <= CATEGORIAS, f"aire: categorías no válidas {set(aire['categoria']) - CATEGORIAS}"
    assert not aire.duplicated(["mes", "hora"]).any(), "aire: mes + hora repetidos"
    assert list(centros.columns) == COLS_CENTROS, "Columnas de centros_salud.csv"
    assert centros["centro_id"].is_unique, "centros de salud repetidos"
    assert centros["lat"].between(39.8, 41.2).all() and centros["lon"].between(-4.6, -3.0).all(), \
        "centros de salud fuera de la Comunidad de Madrid"

    filas = {
        "POLEN_MES": [(r.tipo_polen, int(r.mes), num(r.nivel_medio), r.nivel) for r in polen.itertuples()],
        "ESPECIE_POLEN": [(r.especie, r.tipo_polen) for r in especies.itertuples()],
        "AIRE_MES_HORA": [(int(r.mes), int(r.hora), num(r.no2_media), num(r.pm10_media), num(r.pm25_media),
                           r.categoria, num(r.pct_regular_o_peor), int(r.n_horas)) for r in aire.itertuples()],
        "CENTROS_SALUD": [(r.centro_id, r.nombre, texto(r.tipo_centro), texto(r.direccion), texto(r.url),
                           float(r.lat), float(r.lon)) for r in centros.itertuples()],
    }
    return filas


SQL = {
    "POLEN_MES": "INSERT INTO polen_mes (tipo_polen, mes, nivel_medio, nivel) VALUES (:1, :2, :3, :4)",
    "ESPECIE_POLEN": "INSERT INTO especie_polen (especie, tipo_polen) VALUES (:1, :2)",
    "AIRE_MES_HORA": f"INSERT INTO aire_mes_hora ({', '.join(COLS_AIRE)}) "
                     "VALUES (:1, :2, :3, :4, :5, :6, :7, :8)",
    "CENTROS_SALUD": f"INSERT INTO centros_salud ({', '.join(COLS_CENTROS)}) "
                     "VALUES (:1, :2, :3, :4, :5, :6, :7)",
}
ORIGEN = {
    "POLEN_MES": "data/processed/polen_mes.csv",
    "ESPECIE_POLEN": "data/processed/especie_polen.csv",
    "AIRE_MES_HORA": "data/processed/aire_mes_hora.csv",
    "CENTROS_SALUD": "data/processed/centros_salud.csv",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="valida los CSV sin conectarse a Oracle")
    args = ap.parse_args()

    filas = leer()
    print("CSV correctos: " + ", ".join(f"{len(v)} filas de {k}" for k, v in filas.items()) + ".")
    if args.dry_run:
        print("Modo --dry-run: no se ha tocado la base de datos.")
        return

    import oracledb
    cargar_env()
    falta = [k for k in ("ORACLE_USER", "ORACLE_PASSWORD", "ORACLE_DSN", "ORACLE_WALLET_DIR")
             if not os.environ.get(k)]
    if falta:
        sys.exit(f"Faltan variables de entorno: {falta}. Mira la cabecera de cargar_oracle.py.")

    conn = oracledb.connect(
        user=os.environ["ORACLE_USER"], password=os.environ["ORACLE_PASSWORD"],
        dsn=os.environ["ORACLE_DSN"], config_dir=os.environ["ORACLE_WALLET_DIR"],
        wallet_location=os.environ["ORACLE_WALLET_DIR"],
        wallet_password=os.environ.get("ORACLE_WALLET_PASSWORD"))
    print(f"Conectado a Oracle como {os.environ['ORACLE_USER'].upper()}")

    with conn.cursor() as cur:
        for tabla, script in (("AIRE_MES_HORA", "07_aire.sql"), ("CENTROS_SALUD", "11_centros_salud.sql")):
            cur.execute("SELECT COUNT(*) FROM user_tables WHERE table_name = :1", [tabla])
            if cur.fetchone()[0] == 0:
                sys.exit(f"No existe la tabla {tabla}: ejecuta antes oracle/{script} en APEX o Database Actions.")

        # Hija antes que madre: ESPECIE_POLEN -> POLEN_MES
        for tabla in ("ESPECIE_POLEN", "POLEN_MES", "AIRE_MES_HORA", "CENTROS_SALUD"):
            cur.execute(f"DELETE FROM {tabla}")
        for tabla in ("POLEN_MES", "ESPECIE_POLEN", "AIRE_MES_HORA", "CENTROS_SALUD"):
            cur.executemany(SQL[tabla], filas[tabla])
            cur.execute("INSERT INTO cargas (tabla, filas, origen) VALUES (:1, :2, :3)",
                        [tabla, len(filas[tabla]), ORIGEN[tabla]])
            print(f"   {tabla}: {len(filas[tabla])} filas")
        conn.commit()

        resumen = {}
        for tabla in filas:
            cur.execute(f"SELECT COUNT(*) FROM {tabla}")
            resumen[tabla] = cur.fetchone()[0]
        cur.execute("""SELECT COUNT(*) FROM poi p JOIN especie_polen e ON e.especie = p.especie
                       WHERE p.tipo = 'arbol'""")
        n_arboles = cur.fetchone()[0]
    conn.close()

    print("\nCarga terminada: " + ", ".join(f"{k} {v}" for k, v in resumen.items()) + ".")
    print(f"Árboles de POI enlazados con un tipo de polen: {n_arboles} (esperado: 8953).")


if __name__ == "__main__":
    main()