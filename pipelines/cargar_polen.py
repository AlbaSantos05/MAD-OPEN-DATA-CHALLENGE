"""Carga en Oracle las tablas de polen calculadas por Python.

    data/processed/polen_mes.csv      -> POLEN_MES      (pipelines/calcular_polen.py)
    data/processed/especie_polen.csv  -> ESPECIE_POLEN  (pipelines/especie_polen.py)

Usa la misma configuración que pipelines/cargar_oracle.py (fichero .env con ORACLE_USER,
ORACLE_PASSWORD, ORACLE_DSN, ORACLE_WALLET_DIR, ORACLE_WALLET_PASSWORD). Necesita el
puerto 1522: no funciona en eduroam.

Solo toca POLEN_MES y ESPECIE_POLEN (no borra POI ni TRAMOS). Es idempotente: cada
ejecución vacía esas dos tablas y las vuelve a llenar con los CSV.

Uso:
    python pipelines/cargar_polen.py --dry-run   # comprueba los CSV sin conectarse
    python pipelines/cargar_polen.py             # carga en Oracle
"""
import argparse
import os
import sys
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent.parent
PROC = BASE / "data" / "processed"
NIVELES = {"bajo", "medio", "alto"}


def cargar_env():
    env = BASE / ".env"
    if env.exists():
        for linea in env.read_text(encoding="utf-8").splitlines():
            if "=" in linea and not linea.strip().startswith("#"):
                k, v = linea.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def leer():
    polen = pd.read_csv(PROC / "polen_mes.csv", encoding="utf-8")
    especies = pd.read_csv(PROC / "especie_polen.csv", encoding="utf-8")

    # Las mismas reglas que las restricciones de oracle/02_tablas.sql
    assert list(polen.columns) == ["tipo_polen", "mes", "nivel_medio", "nivel"], "Columnas de polen_mes.csv"
    assert list(especies.columns) == ["especie", "tipo_polen"], "Columnas de especie_polen.csv"
    assert polen["mes"].between(1, 12).all(), "Hay meses fuera de 1-12"
    assert set(polen["nivel"]) <= NIVELES, f"Niveles no válidos: {set(polen['nivel']) - NIVELES}"
    assert not polen.duplicated(["tipo_polen", "mes"]).any(), "tipo_polen + mes repetidos"
    assert especies["especie"].is_unique, "Especies repetidas"
    sin_calendario = set(especies["tipo_polen"]) - set(polen["tipo_polen"])
    assert not sin_calendario, f"Tipos de polen sin calendario en polen_mes.csv: {sin_calendario}"

    filas_polen = [(r.tipo_polen, int(r.mes), float(r.nivel_medio), r.nivel) for r in polen.itertuples()]
    filas_especies = [(r.especie, r.tipo_polen) for r in especies.itertuples()]
    return filas_polen, filas_especies


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="valida los CSV sin conectarse a Oracle")
    args = ap.parse_args()

    filas_polen, filas_especies = leer()
    print(f"CSV correctos: {len(filas_polen)} filas de POLEN_MES y {len(filas_especies)} de ESPECIE_POLEN.")
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
        cur.execute("DELETE FROM especie_polen")
        cur.execute("DELETE FROM polen_mes")
        cur.executemany("INSERT INTO polen_mes (tipo_polen, mes, nivel_medio, nivel) VALUES (:1, :2, :3, :4)",
                        filas_polen)
        cur.executemany("INSERT INTO especie_polen (especie, tipo_polen) VALUES (:1, :2)", filas_especies)
        for tabla, n, origen in (("POLEN_MES", len(filas_polen), "data/processed/polen_mes.csv"),
                                 ("ESPECIE_POLEN", len(filas_especies), "data/processed/especie_polen.csv")):
            cur.execute("INSERT INTO cargas (tabla, filas, origen) VALUES (:1, :2, :3)", [tabla, n, origen])
        conn.commit()

        cur.execute("SELECT COUNT(*) FROM polen_mes")
        n_polen = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM especie_polen")
        n_esp = cur.fetchone()[0]
        # Cuántos árboles de POI quedan enlazados con un tipo de polen
        cur.execute("""SELECT COUNT(*) FROM poi p JOIN especie_polen e ON e.especie = p.especie
                       WHERE p.tipo = 'arbol'""")
        n_arboles = cur.fetchone()[0]
    conn.close()

    print(f"\nCarga terminada: {n_polen} filas en POLEN_MES, {n_esp} en ESPECIE_POLEN.")
    print(f"Árboles de POI enlazados con un tipo de polen: {n_arboles} (esperado: 8953).")


if __name__ == "__main__":
    main()