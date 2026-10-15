"""Carga el dataset (data/dataset/*.geojson) en Oracle Autonomous Database.

Requisitos:   pip install oracledb geopandas
Antes:        ejecutar oracle/01, 02 y 03 en Database Actions
Configuración (variables de entorno o un fichero .env en la raíz, que NO se sube a GitHub):
    ORACLE_USER=maddata
    ORACLE_PASSWORD=...
    ORACLE_DSN=madopendata_low          # alias de tnsnames.ora dentro del wallet
    ORACLE_WALLET_DIR=/ruta/al/wallet   # carpeta con el zip del wallet DESCOMPRIMIDO
    ORACLE_WALLET_PASSWORD=...          # la que pusiste al descargar el wallet

Uso:
    python pipelines/cargar_oracle.py --dry-run   # comprueba los datos sin conectarse
    python pipelines/cargar_oracle.py             # borra y vuelve a cargar POI y TRAMOS

Es idempotente: cada ejecución deja la base igual que los ficheros de data/dataset/.
"""
import argparse
import math
import os
import sys
from pathlib import Path

import geopandas as gpd

BASE = Path(__file__).resolve().parent.parent
DATASET = BASE / "data" / "dataset"
LOTE = 1000


def cargar_env():
    env = BASE / ".env"
    if env.exists():
        for linea in env.read_text(encoding="utf-8").splitlines():
            if "=" in linea and not linea.strip().startswith("#"):
                k, v = linea.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def limpio(v):
    """NaN/None -> None; numpy -> tipos de Python (oracledb no acepta numpy)."""
    if v is None:
        return None
    if hasattr(v, "item"):
        v = v.item()
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def leer():
    poi = gpd.read_file(DATASET / "poi.geojson")
    tramos = gpd.read_file(DATASET / "tramos.geojson")
    assert poi.crs.to_epsg() == 4326 and tramos.crs.to_epsg() == 4326, "Los ficheros deben estar en EPSG:4326"
    assert poi["poi_id"].is_unique, "poi_id repetidos"
    assert tramos["tramo_id"].is_unique, "tramo_id repetidos"
    faltan = set(tramos["nearest_stop_id"].dropna()) - set(poi["poi_id"])
    assert not faltan, f"Tramos que apuntan a paradas que no están en POI: {list(faltan)[:5]}"
    assert (poi.geom_type == "Point").all() and (tramos.geom_type == "LineString").all()

    filas_poi = [(r.poi_id, r.tipo, limpio(r.nombre), r.fuente_id, limpio(r.accesible), limpio(r.especie),
                  round(r.geometry.x, 7), round(r.geometry.y, 7))
                 for r in poi.itertuples()]
    cols = ["tramo_id", "nodo_u", "nodo_v", "nombre_via", "tipo_via", "length_m", "slope_pct", "steps",
            "benches_20m", "dist_bench_m", "dist_transport_m", "nearest_stop_id", "stop_accessible",
            "dist_pharmacy_m", "dist_water_m", "trees_20m", "quality_flags"]
    filas_tramos = [tuple(limpio(getattr(r, c)) for c in cols) + (r.geometry.wkt,)
                    for r in tramos.itertuples()]
    return filas_poi, filas_tramos, cols


SQL_POI = """INSERT INTO poi (poi_id, tipo, nombre, fuente_id, accesible, especie, geom)
             VALUES (:1, :2, :3, :4, :5, :6,
                     SDO_GEOMETRY(2001, 4326, SDO_POINT_TYPE(:7, :8, NULL), NULL, NULL))"""


def sql_tramos(cols):
    marcas = ", ".join(f":{i}" for i in range(1, len(cols) + 1))
    return (f"INSERT INTO tramos ({', '.join(cols)}, geom) "
            f"VALUES ({marcas}, SDO_GEOMETRY(:{len(cols) + 1}, 4326))")


def tipos(filas):
    """Fija el tipo de cada columna: si la primera fila trae None, oracledb no sabría
    que la columna es numérica y fallaría al llegar el primer número."""
    out = []
    for j in range(len(filas[0])):
        muestra = next((f[j] for f in filas if f[j] is not None), None)
        out.append(oracledb.DB_TYPE_NUMBER if isinstance(muestra, (int, float)) or muestra is None
                   else oracledb.DB_TYPE_VARCHAR)
    return out


def insertar(cur, sql, filas, nombre):
    cur.setinputsizes(*tipos(filas))
    for i in range(0, len(filas), LOTE):
        cur.executemany(sql, filas[i:i + LOTE], batcherrors=True)
        errores = cur.getbatcherrors()
        if errores:
            for e in errores[:5]:
                print(f"   ERROR {nombre} fila {i + e.offset}: {e.message}")
            raise SystemExit(f"Carga de {nombre} abortada ({len(errores)} errores en el lote).")
        print(f"   {nombre}: {min(i + LOTE, len(filas))}/{len(filas)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="valida los ficheros sin conectarse a Oracle")
    args = ap.parse_args()

    filas_poi, filas_tramos, cols = leer()
    print(f"Ficheros correctos: {len(filas_poi)} POI y {len(filas_tramos)} tramos.")
    print(f"WKT más largo: {max(len(f[-1]) for f in filas_tramos)} caracteres (límite 32767).")
    if args.dry_run:
        print("Modo --dry-run: no se ha tocado la base de datos.")
        return

    global oracledb
    import oracledb
    cargar_env()
    falta = [k for k in ("ORACLE_USER", "ORACLE_PASSWORD", "ORACLE_DSN", "ORACLE_WALLET_DIR")
             if not os.environ.get(k)]
    if falta:
        sys.exit(f"Faltan variables de entorno: {falta}. Mira la cabecera de este script.")

    conn = oracledb.connect(
        user=os.environ["ORACLE_USER"], password=os.environ["ORACLE_PASSWORD"],
        dsn=os.environ["ORACLE_DSN"], config_dir=os.environ["ORACLE_WALLET_DIR"],
        wallet_location=os.environ["ORACLE_WALLET_DIR"],
        wallet_password=os.environ.get("ORACLE_WALLET_PASSWORD"))
    print(f"Conectado a Oracle {conn.version} como {os.environ['ORACLE_USER'].upper()}")

    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM fuentes")
        if cur.fetchone()[0] == 0:
            sys.exit("La tabla FUENTES está vacía: ejecuta antes oracle/03_fuentes_y_diccionario.sql")
        cur.execute("SELECT fuente_id FROM fuentes")
        existentes = {r[0] for r in cur}
        faltan = {f[3] for f in filas_poi} - existentes
        if faltan:
            sys.exit(f"Estas fuentes no están en FUENTES: {faltan}. Añádelas en el script 03.")

        print("Borrando la carga anterior...")
        cur.execute("TRUNCATE TABLE tramos")      # hija primero
        cur.execute("DELETE FROM poi")            # TRUNCATE no se permite en una tabla con FK entrante

        print("Cargando POI...")
        insertar(cur, SQL_POI, filas_poi, "POI")
        print("Cargando TRAMOS...")
        insertar(cur, sql_tramos(cols), filas_tramos, "TRAMOS")

        for tabla, n in (("POI", len(filas_poi)), ("TRAMOS", len(filas_tramos))):
            cur.execute("INSERT INTO cargas (tabla, filas, origen) VALUES (:1, :2, :3)",
                        [tabla, n, f"data/dataset/{tabla.lower()}.geojson"])
        conn.commit()

        cur.execute("SELECT COUNT(*) FROM poi")
        n_poi = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM tramos")
        n_tramos = cur.fetchone()[0]
        cur.execute("""SELECT COUNT(*) FROM tramos
                       WHERE SDO_GEOM.VALIDATE_GEOMETRY_WITH_CONTEXT(geom, 0.05) <> 'TRUE'""")
        invalidas = cur.fetchone()[0]
    conn.close()

    print(f"\nCarga terminada: {n_poi} POI y {n_tramos} tramos en Oracle; geometrías no válidas: {invalidas}.")
    if invalidas:
        print("Revisa con: SELECT tramo_id, SDO_GEOM.VALIDATE_GEOMETRY_WITH_CONTEXT(geom, 0.05) FROM tramos ...")


if __name__ == "__main__":
    main()
