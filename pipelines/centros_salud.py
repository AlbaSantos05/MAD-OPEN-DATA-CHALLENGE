"""Centros de salud de la Comunidad de Madrid en Chamberí y alrededores.

Entrada : data/raw/centros_salud.geojson
          Comunidad de Madrid · IDEM (Infraestructura de Datos Espaciales) · Centros de Salud
          https://idem.comunidad.madrid/ags/rest/services/Hosted/Centros_de_Salud/FeatureServer/0
          Descargado con una consulta recortada a la zona:
          .../query?where=1=1&geometry=-3.73,40.42,-3.68,40.45&geometryType=esriGeometryEnvelope
                   &inSR=4326&outFields=*&f=geojson
          data/dataset/tramos.geojson (para saber qué queda cerca de la zona del prototipo)
Salida  : data/processed/centros_salud.csv -> tabla Oracle CENTROS_SALUD

Criterio: a menos de 500 m de la red peatonal del prototipo (misma regla que el resto de recursos).
El dataset no contiene datos personales.

Ejecutar desde la raíz del proyecto:
    python pipelines/centros_salud.py
"""
import json
import math
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent.parent
ENTRADA = BASE / "data" / "raw" / "centros_salud.geojson"
TRAMOS = BASE / "data" / "dataset" / "tramos.geojson"
SALIDA = BASE / "data" / "processed" / "centros_salud.csv"
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


def main():
    datos = json.loads(ENTRADA.read_text(encoding="utf-8"))
    red = puntos_red()
    filas = []
    for f in datos["features"]:
        lon, lat = f["geometry"]["coordinates"][:2]
        p = f["properties"]
        dist = min(metros(lat, lon, a, b) for a, b in red)
        if dist > MARGEN_M:
            continue
        via = " ".join(x for x in (p.get("tipovial"), p.get("nomvia"), p.get("numero")) if x)
        filas.append({
            "centro_id": f"CS_{p['fid']}",
            "nombre": p["descr"].replace("C.S. ", "Centro de salud ").strip(),
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
    print(f"Centros en el fichero: {len(datos['features'])}. A <= {MARGEN_M} m de la red: {len(salida)}")
    print(f"Guardado en {SALIDA.relative_to(BASE)}\n")
    for r in salida.itertuples():
        print(f"  {r.nombre:<40} {r.direccion:<32} {r.dist_red_m:>4} m de la red")


if __name__ == "__main__":
    main()