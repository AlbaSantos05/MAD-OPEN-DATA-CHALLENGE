"""Centros de salud de la Comunidad de Madrid para la zona del prototipo (cualquier zona).

Fuente  : Comunidad de Madrid · IDEM (Infraestructura de Datos Espaciales) · Centros de Salud
          https://idem.comunidad.madrid/ags/rest/services/Hosted/Centros_de_Salud/FeatureServer/0
Entrada : data/dataset/tramos.geojson  -> define la zona (la red peatonal del dataset)
Salida  : data/raw/centros_salud.geojson   (descarga tal cual, para que sea reproducible)
          data/processed/centros_salud.csv -> tabla Oracle CENTROS_SALUD (pipelines/cargar_ambiental.py)

Escalable: no hay nada fijado a Chamberí. El rectángulo de descarga se calcula a partir de
las calles del dataset más un margen de 500 m; si mañana el dataset cubre otro barrio u otro
municipio, basta con volver a ejecutar este script y el cargador.

Criterio: a menos de 500 m de la red peatonal (misma regla que el resto de recursos).
El dataset no contiene datos personales.

Uso (desde la raíz del proyecto):
    python pipelines/centros_salud.py              # descarga de la IDEM y filtra
    python pipelines/centros_salud.py --sin-red    # no descarga: usa data/raw/centros_salud.geojson
"""
import argparse
import json
import math
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent.parent
TRAMOS = BASE / "data" / "dataset" / "tramos.geojson"
CRUDO = BASE / "data" / "raw" / "centros_salud.geojson"
SALIDA = BASE / "data" / "processed" / "centros_salud.csv"
SERVICIO = "https://idem.comunidad.madrid/ags/rest/services/Hosted/Centros_de_Salud/FeatureServer/0/query"
MARGEN_M = 500
TIPOS = {"CS": "centro de salud", "CL": "consultorio local", "CEP": "centro de especialidades"}


def metros(lat1, lon1, lat2, lon2):
    """Distancia aproximada en metros (equirrectangular; de sobra para 500 m)."""
    kx = 111320 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot((lon2 - lon1) * kx, (lat2 - lat1) * 110540)


def puntos_red():
    datos = json.loads(TRAMOS.read_text(encoding="utf-8"))
    return list({(round(lat, 5), round(lon, 5))
                 for f in datos["features"] for lon, lat in f["geometry"]["coordinates"]})


def rectangulo(red, margen_m=MARGEN_M):
    """Rectángulo (lon_min, lat_min, lon_max, lat_max) de la red + margen, en grados."""
    lats, lons = [p[0] for p in red], [p[1] for p in red]
    dlat = margen_m / 110540
    dlon = margen_m / (111320 * math.cos(math.radians(sum(lats) / len(lats))))
    return min(lons) - dlon, min(lats) - dlat, max(lons) + dlon, max(lats) + dlat


def descargar(caja):
    """Consulta el servicio de la IDEM con el rectángulo de la zona (todas las páginas)."""
    features, desde = [], 0
    while True:
        params = {"where": "1=1", "geometry": ",".join(f"{v:.6f}" for v in caja),
                  "geometryType": "esriGeometryEnvelope", "inSR": "4326", "outSR": "4326",
                  "spatialRel": "esriSpatialRelIntersects", "outFields": "*", "f": "geojson",
                  "resultOffset": str(desde), "resultRecordCount": "1000"}
        with urllib.request.urlopen(SERVICIO + "?" + urllib.parse.urlencode(params), timeout=60) as r:
            datos = json.loads(r.read().decode("utf-8"))
        features += datos.get("features", [])
        if not datos.get("properties", {}).get("exceededTransferLimit"):
            break
        desde += 1000
    return {"type": "FeatureCollection", "features": features}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sin-red", action="store_true", help="no descargar; usar data/raw/centros_salud.geojson")
    args = ap.parse_args()

    red = puntos_red()
    caja = rectangulo(red)
    if args.sin_red:
        datos = json.loads(CRUDO.read_text(encoding="utf-8"))
        print(f"Usando la descarga guardada: {CRUDO.relative_to(BASE)}")
    else:
        print("Zona (rectángulo de la red + 500 m): " + ", ".join(f"{v:.4f}" for v in caja))
        try:
            datos = descargar(caja)
            CRUDO.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")
            print(f"Descargados {len(datos['features'])} centros de la IDEM -> {CRUDO.relative_to(BASE)}")
        except Exception as e:  # noqa: BLE001 - sin conexión: se usa la última descarga
            print(f"No se pudo descargar de la IDEM ({e}). Uso {CRUDO.relative_to(BASE)}.")
            datos = json.loads(CRUDO.read_text(encoding="utf-8"))

    filas = []
    for f in datos["features"]:
        lon, lat = f["geometry"]["coordinates"][:2]
        p = f["properties"]
        dist = min(metros(lat, lon, a, b) for a, b in red)
        if dist > MARGEN_M:
            continue
        via = " ".join(str(x) for x in (p.get("tipovial"), p.get("nomvia"), p.get("numero")) if x)
        filas.append({
            "centro_id": f"CS_{p['fid']}",
            "nombre": str(p["descr"]).replace("C.S. ", "Centro de salud ").strip(),
            "tipo_centro": TIPOS.get(p.get("tipocentro"), p.get("tipocentro")),
            "direccion": via.replace("c\\v", "c/v"),
            "url": p.get("url"),
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "dist_red_m": round(dist),
        })
    salida = pd.DataFrame(filas).sort_values("nombre")
    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    salida.drop(columns="dist_red_m").to_csv(SALIDA, index=False, encoding="utf-8")
    print(f"A <= {MARGEN_M} m de la red: {len(salida)} centros -> {SALIDA.relative_to(BASE)}\n")
    for r in salida.itertuples():
        print(f"  {r.nombre:<40} {r.direccion:<32} {r.dist_red_m:>4} m de la red")


if __name__ == "__main__":
    main()