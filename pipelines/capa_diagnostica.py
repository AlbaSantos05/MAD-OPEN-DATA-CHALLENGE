"""Capa diagnóstica: divide el distrito en zonas y puntúa cada una (dato extra, no va a la app).

Pensada para la administración: ¿qué zonas del distrito son más amables para una persona mayor
y cuáles conviene mirar primero? No usa datos nuevos: sale del mismo dataset que el acompañante.

Entradas (las que ya genera el proyecto; vale para cualquier distrito que cubra el dataset):
    data/dataset/tramos.geojson        red peatonal con sus indicadores por tramo
    data/dataset/poi.geojson           bancos, farmacias, fuentes, metro, bus y árboles
    data/processed/centros_salud.csv   centros de salud (Comunidad de Madrid)
    data/processed/polen_mes.csv       calendario de polen      (opcional)
    data/processed/especie_polen.csv   especie -> tipo de polen (opcional)
Salidas:
    data/processed/zonas_diagnostico.csv       una fila por zona: indicadores y puntuación
    data/processed/zonas_diagnostico.geojson   las mismas zonas como cuadrados, para verlas en un mapa
    data/processed/resumen_diagnostico.json    estadísticas del distrito completo

Zonas: cuadrícula de CELDA x CELDA metros (200 m por defecto). Cada tramo de calle cuenta en la
zona donde cae su punto medio, ponderado por su longitud. Se descartan las zonas con menos de
MIN_KM de red, porque con tan poca calle los porcentajes no son fiables.

Puntuación 0-100 (más alta = zona más cómoda y mejor dotada para una persona mayor). Es la media
ponderada del porcentaje de red de la zona que cumple cada criterio:
    descanso       25 %  banco a <= 100 m andando                     (Ayto. · bancos)
    transporte     15 %  metro o bus a <= 300 m andando               (CRTM · GTFS)
    farmacia       15 %  farmacia a <= 300 m andando                  (CM · farmacias)
    centro_salud   15 %  centro de salud a <= 500 m en línea recta    (CM · IDEM)
    llano          15 %  pendiente < 6 %                              (EU-DEM sobre red OSM)
    agua           10 %  fuente de agua a <= 300 m andando            (Ayto. · fuentes)
    sin_escaleras   5 %  el tramo no es una escalera                  (OSM)
Indicadores informativos que NO cuentan en la puntuación: bancos, fuentes y farmacias por km,
árboles con polen medio/alto en el mes elegido por km y % de red con transporte no accesible
(solo el metro informa de la accesibilidad; las paradas de bus salen como «desconocido»).

Cómo leerla: «dónde mirar primero», no un ranking de barrios buenos o malos.

Uso (desde la raíz del proyecto):
    python pipelines/capa_diagnostica.py                          # Chamberí, celdas de 200 m, polen de abril
    python pipelines/capa_diagnostica.py --distrito Tetuán --celda 250 --mes 5
"""
import argparse
import json
import math
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent.parent
DATASET = BASE / "data" / "dataset"
PROC = BASE / "data" / "processed"

PESOS = {"descanso": 0.25, "transporte": 0.15, "farmacia": 0.15, "centro_salud": 0.15,
         "llano": 0.15, "agua": 0.10, "sin_escaleras": 0.05}
UMBRALES = {"banco_m": 100, "transporte_m": 300, "farmacia_m": 300, "agua_m": 300,
            "centro_salud_m": 500, "pendiente_pct": 6}
MIN_KM = 0.2
NOMBRES = {"descanso": "pocos bancos cerca", "transporte": "transporte lejos", "farmacia": "farmacia lejos",
           "centro_salud": "centro de salud lejos", "llano": "cuestas", "agua": "fuentes lejos",
           "sin_escaleras": "escaleras"}
ASSERT_PESOS = abs(sum(PESOS.values()) - 1) < 1e-9


# ---------------------------------------------------------------- geometría sencilla
class Plano:
    """Proyección local en metros (equirrectangular) centrada en la zona: de sobra para celdas
    de cientos de metros y no necesita librerías GIS. Vale para cualquier distrito."""

    def __init__(self, lat0, lon0):
        self.lat0, self.lon0 = lat0, lon0
        self.kx = 111320 * math.cos(math.radians(lat0))
        self.ky = 110540

    def xy(self, lat, lon):
        return (lon - self.lon0) * self.kx, (lat - self.lat0) * self.ky

    def latlon(self, x, y):
        return self.lat0 + y / self.ky, self.lon0 + x / self.kx


def punto_medio(coords):
    """Punto a mitad de longitud de una línea [(lon, lat), ...]."""
    if len(coords) == 1:
        return coords[0][1], coords[0][0]
    tramos = [math.dist(a, b) for a, b in zip(coords, coords[1:])]
    mitad, acum = sum(tramos) / 2, 0.0
    for (a, b), d in zip(zip(coords, coords[1:]), tramos):
        if acum + d >= mitad and d > 0:
            t = (mitad - acum) / d
            return a[1] + t * (b[1] - a[1]), a[0] + t * (b[0] - a[0])
        acum += d
    return coords[-1][1], coords[-1][0]


# ---------------------------------------------------------------- datos
def leer_tramos(plano, celda):
    t = json.loads((DATASET / "tramos.geojson").read_text(encoding="utf-8"))
    filas = []
    for f in t["features"]:
        p = f["properties"]
        lat, lon = punto_medio(f["geometry"]["coordinates"])
        x, y = plano.xy(lat, lon)
        filas.append({**p, "lat": lat, "lon": lon, "x": x, "y": y,
                      "zona_id": f"{math.floor(x / celda)}_{math.floor(y / celda)}"})
    return pd.DataFrame(filas)


def leer_poi(plano, celda):
    d = json.loads((DATASET / "poi.geojson").read_text(encoding="utf-8"))
    filas = []
    for f in d["features"]:
        lon, lat = f["geometry"]["coordinates"][:2]
        x, y = plano.xy(lat, lon)
        filas.append({"tipo": f["properties"]["tipo"], "especie": f["properties"].get("especie"),
                      "zona_id": f"{math.floor(x / celda)}_{math.floor(y / celda)}"})
    return pd.DataFrame(filas)


def dist_centro_salud(tramos, plano):
    ruta = PROC / "centros_salud.csv"
    if not ruta.exists():
        print("  (sin centros_salud.csv: el criterio centro_salud cuenta como no cumplido)")
        return pd.Series(float("inf"), index=tramos.index)
    cs = pd.read_csv(ruta)
    pts = [plano.xy(la, lo) for la, lo in zip(cs["lat"], cs["lon"])]
    return pd.Series([min(math.hypot(x - a, y - b) for a, b in pts) for x, y in zip(tramos["x"], tramos["y"])],
                     index=tramos.index)


def arboles_alergenicos(poi, mes):
    """Árboles por zona cuyo polen suele estar en nivel medio o alto en ese mes."""
    rp, re = PROC / "polen_mes.csv", PROC / "especie_polen.csv"
    if not (rp.exists() and re.exists()):
        return pd.Series(dtype=float)
    polen = pd.read_csv(rp)
    activos = set(polen[(polen["mes"] == mes) & polen["nivel"].isin(["medio", "alto"])]["tipo_polen"])
    especie = dict(pd.read_csv(re)[["especie", "tipo_polen"]].values)
    a = poi[poi["tipo"] == "arbol"].copy()
    a = a[a["especie"].map(especie).isin(activos)]
    return a.groupby("zona_id").size()


# ---------------------------------------------------------------- cálculo
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--distrito", default="Chamberí", help="nombre del distrito (solo para las salidas)")
    ap.add_argument("--celda", type=int, default=200, help="lado de cada zona en metros")
    ap.add_argument("--mes", type=int, default=4, help="mes para los árboles alergénicos (1-12)")
    args = ap.parse_args()
    assert ASSERT_PESOS, "Los pesos deben sumar 1"

    t0 = json.loads((DATASET / "tramos.geojson").read_text(encoding="utf-8"))
    todos = [c for f in t0["features"] for c in f["geometry"]["coordinates"]]
    plano = Plano(sum(c[1] for c in todos) / len(todos), sum(c[0] for c in todos) / len(todos))

    t = leer_tramos(plano, args.celda)
    poi = leer_poi(plano, args.celda)
    t["dist_cs_m"] = dist_centro_salud(t, plano)

    # Criterios por tramo (1 = cumple)
    t["c_descanso"] = (t["dist_bench_m"] <= UMBRALES["banco_m"]).astype(float)
    t["c_transporte"] = (t["dist_transport_m"] <= UMBRALES["transporte_m"]).astype(float)
    t["c_farmacia"] = (t["dist_pharmacy_m"] <= UMBRALES["farmacia_m"]).astype(float)
    t["c_agua"] = (t["dist_water_m"] <= UMBRALES["agua_m"]).astype(float)
    t["c_centro_salud"] = (t["dist_cs_m"] <= UMBRALES["centro_salud_m"]).astype(float)
    t["c_llano"] = (t["slope_pct"].fillna(0) < UMBRALES["pendiente_pct"]).astype(float)
    t["c_sin_escaleras"] = (t["steps"] != "si").astype(float)
    t["no_accesible"] = (t["stop_accessible"] == "no").astype(float)

    def ponderada(g, col):
        return float((g[col] * g["length_m"]).sum() / g["length_m"].sum())

    # Identificador legible: prefijo del distrito + fila (de sur a norte) + columna (de oeste a este)
    ij = t["zona_id"].str.split("_", expand=True).astype(int)
    i0, j0 = ij[0].min(), ij[1].min()
    prefijo = "".join(c for c in args.distrito.upper() if c.isalpha())[:3]

    filas = []
    for zona, g in t.groupby("zona_id"):
        km = g["length_m"].sum() / 1000
        if km < MIN_KM:
            continue
        i, j = (int(v) for v in zona.split("_"))
        lat, lon = plano.latlon((i + 0.5) * args.celda, (j + 0.5) * args.celda)
        fila = {"zona_id": f"{prefijo}_F{j - j0:02d}_C{i - i0:02d}", "celda": zona, "distrito": args.distrito,
                "lat_centro": round(lat, 6), "lon_centro": round(lon, 6), "km_red": round(km, 2)}
        for k in PESOS:
            fila[f"pct_{k}"] = round(100 * ponderada(g, f"c_{k}"), 1)
        fila["puntuacion"] = round(sum(PESOS[k] * fila[f"pct_{k}"] for k in PESOS), 1)
        flojos = sorted(PESOS, key=lambda k: fila[f"pct_{k}"])
        fila["punto_debil"] = NOMBRES[flojos[0]] if fila[f"pct_{flojos[0]}"] < 80 else "ninguno destacable"
        n = poi[poi["zona_id"] == zona]["tipo"].value_counts()
        fila["bancos_por_km"] = round(n.get("banco", 0) / km, 1)
        fila["fuentes_por_km"] = round(n.get("fuente", 0) / km, 1)
        fila["farmacias"] = int(n.get("farmacia", 0))
        fila["paradas_transporte"] = int(n.get("metro", 0) + n.get("bus", 0))
        fila["pendiente_media_pct"] = round(ponderada(g, "slope_pct"), 1)
        fila["pct_transporte_no_accesible"] = round(100 * ponderada(g, "no_accesible"), 1)
        filas.append(fila)

    z = pd.DataFrame(filas)
    alerg = arboles_alergenicos(poi, args.mes)
    z[f"arboles_alergenicos_mes{args.mes}_por_km"] = (z["celda"].map(alerg).fillna(0) / z["km_red"]).round(1)
    z = z.sort_values("puntuacion").reset_index(drop=True)
    z.insert(1, "prioridad", range(1, len(z) + 1))        # 1 = la zona que conviene mirar primero
    PROC.mkdir(parents=True, exist_ok=True)
    z.drop(columns="celda").to_csv(PROC / "zonas_diagnostico.csv", index=False, encoding="utf-8")

    # GeoJSON con los cuadrados, para verlo en geojson.io o QGIS
    feats = []
    for r in z.itertuples():
        i, j = (int(v) for v in r.celda.split("_"))
        esquinas = [plano.latlon(a * args.celda, b * args.celda)
                    for a, b in ((i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1), (i, j))]
        feats.append({"type": "Feature", "properties": {k: v for k, v in r._asdict().items() if k not in ("Index", "celda")},
                      "geometry": {"type": "Polygon", "coordinates": [[[round(lo, 6), round(la, 6)] for la, lo in esquinas]]}})
    (PROC / "zonas_diagnostico.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False), encoding="utf-8")

    # Estadísticas del distrito completo (ponderadas por longitud de red)
    resumen = {"distrito": args.distrito, "celda_m": args.celda, "zonas": len(z),
               "km_red": round(t["length_m"].sum() / 1000, 1),
               "puntuacion_media": round(float((z["puntuacion"] * z["km_red"]).sum() / z["km_red"].sum()), 1),
               "puntuacion_min": float(z["puntuacion"].min()), "puntuacion_max": float(z["puntuacion"].max()),
               "pct_red_distrito": {k: round(100 * ponderada(t, f"c_{k}"), 1) for k in PESOS},
               "pesos": PESOS, "umbrales": UMBRALES,
               "punto_debil_mas_frecuente": z["punto_debil"].value_counts().to_dict()}
    (PROC / "resumen_diagnostico.json").write_text(json.dumps(resumen, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"{args.distrito}: {len(z)} zonas de {args.celda} m con red suficiente ({resumen['km_red']} km de calles)")
    print(f"Puntuación media {resumen['puntuacion_media']} (de {resumen['puntuacion_min']} a {resumen['puntuacion_max']})")
    print("% de red del distrito que cumple cada criterio: "
          + ", ".join(f"{k} {v}" for k, v in resumen["pct_red_distrito"].items()))
    print("\nZonas que conviene mirar primero:")
    for r in z.head(5).itertuples():
        print(f"  {r.prioridad:>2}. {r.zona_id:<14} puntuación {r.puntuacion:>5}  punto débil: {r.punto_debil}"
              f"  ({r.lat_centro}, {r.lon_centro})")
    print("\nZonas mejor dotadas:")
    for r in z.tail(3).iloc[::-1].itertuples():
        print(f"      {r.zona_id:<14} puntuación {r.puntuacion:>5}")
    print("\nGuardado en data/processed/: zonas_diagnostico.csv, zonas_diagnostico.geojson, resumen_diagnostico.json")


if __name__ == "__main__":
    main()