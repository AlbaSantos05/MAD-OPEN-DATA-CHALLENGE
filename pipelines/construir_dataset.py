"""Construye el dataset propio del proyecto: la red peatonal enriquecida de la zona piloto.

Entrada : data/processed/chamberi_calles.geojson  (red OSM con pendiente, de limpieza_datos.py)
          data/raw/bancos.csv, farmacias.csv, stops_metro.txt, stops_bus.txt
          (opcionales) data/raw/fuentes.csv, data/raw/arbolado.csv
Salida  : data/dataset/tramos.geojson   -> tabla TRAMOS de Oracle
          data/dataset/poi.geojson      -> tabla POI de Oracle
          data/dataset/resumen.json     -> cifras para FUENTES y para comprobar la carga

Decisiones (documentadas en DICCIONARIO):
  * Un tramo = un segmento físico de calle. OSM da cada calle dos veces (ida y vuelta):
    nos quedamos con una sola fila por segmento.
  * Distancias a transporte y farmacia medidas POR LA RED PEATONAL, no en línea recta.
  * Los recursos se toman de la zona + 500 m alrededor, para que un tramo del borde
    no ignore la farmacia que está cruzando la calle fuera del distrito.
  * Todo el cálculo en metros (EPSG:25830); la salida en WGS84 (EPSG:4326) para Oracle y Leaflet.

Ejecutar desde la raíz del proyecto:  python pipelines/construir_dataset.py
"""
import json
import warnings
from pathlib import Path

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", message="Could not parse column")

BASE = Path(__file__).resolve().parent.parent
RAW = BASE / "data" / "raw"
PROC = BASE / "data" / "processed"
OUT = BASE / "data" / "dataset"
OUT.mkdir(parents=True, exist_ok=True)

ZONA = "chamberi"
CRS_M = 25830
MARGEN_RECURSOS = 500   # m alrededor de la zona para buscar recursos
RADIO_BANCOS = 20       # m: banco "en" el tramo
RADIO_ARBOLES = 15      # m: árbol "en" el tramo
SNAP_MAX = 150          # m: un recurso más lejos de cualquier cruce no se engancha a la red


# ---------------------------------------------------------------- lectura
def leer_csv(nombre, sep=";"):
    ruta = RAW / nombre
    return pd.read_csv(ruta, sep=sep, dtype=str, encoding_errors="replace") if ruta.exists() else None


def num(serie):
    return pd.to_numeric(serie.astype(str).str.replace(",", "."), errors="coerce")


def puntos(df, x, y, crs):
    df = df.copy()
    df["_x"], df["_y"] = num(df[x]), num(df[y])
    df = df.dropna(subset=["_x", "_y"])
    g = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df["_x"], df["_y"]), crs=crs)
    return g.drop(columns=["_x", "_y"]).to_crs(CRS_M)


def primero(v):
    """OSM guarda algunos campos como "['footway']": devolvemos el primer valor."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    s = str(v)
    if s.startswith("["):
        s = s.strip("[]").split(",")[0].strip().strip("'\"")
    return s or None


print(f"=== DATASET {ZONA.upper()} ===")

# ---------------------------------------------------------------- 1. tramos únicos
calles = gpd.read_file(PROC / f"{ZONA}_calles.geojson").to_crs(CRS_M)
calles["a"] = calles[["u", "v"]].min(axis=1).astype("int64")
calles["b"] = calles[["u", "v"]].max(axis=1).astype("int64")
tramos = calles.drop_duplicates(subset=["a", "b", "key"]).reset_index(drop=True)
tramos["tramo_id"] = np.arange(1, len(tramos) + 1)
tramos["nodo_u"], tramos["nodo_v"] = tramos["a"], tramos["b"]
# La geometría de OSM va de u a v; si hemos reordenado los nodos, le damos la vuelta
# para que SIEMPRE vaya de nodo_u a nodo_v (el motor lo usa para situar cada cruce).
girar = tramos["u"] != tramos["nodo_u"]
tramos.loc[girar, "geometry"] = tramos.loc[girar, "geometry"].reverse()
tramos["nombre_via"] = tramos["name"].apply(primero)
tramos["tipo_via"] = tramos["highway"].apply(primero)
tramos["length_m"] = tramos["length"].round(1)
tramos["slope_pct"] = tramos["pendiente_pct"].round(1)
tramos["steps"] = np.where(tramos["highway"].astype(str).str.contains("steps"), "si", "no")
print(f"[1] {len(calles)} aristas OSM -> {len(tramos)} tramos físicos")

# Avisos de calidad por tramo
flags = pd.Series([["pendiente_eudem25m"] for _ in range(len(tramos))])
grade_pct = tramos["grade"].abs() * 100
for i in tramos.index[tramos["length"] < 15]:
    flags[i].append("tramo_corto_pendiente_0")
for i in tramos.index[grade_pct > 12]:
    flags[i].append("pendiente_recortada_12")
for i in tramos.index[tramos["tunnel"].notna() | tramos["bridge"].notna()]:
    flags[i].append("puente_o_tunel")
tramos["quality_flags"] = flags.apply(";".join)

zona = (tramos.union_all() if hasattr(tramos, "union_all") else tramos.unary_union).convex_hull
zona_ampliada = zona.buffer(MARGEN_RECURSOS)

# ---------------------------------------------------------------- 2. recursos (POI)
poi = []

b = leer_csv("bancos.csv")
b = puntos(b, "COORD_GIS_X", "COORD_GIS_Y", "EPSG:25830")
b = b[b.within(zona_ampliada)]
poi.append(gpd.GeoDataFrame({
    "poi_id": "BANCO_" + b["ID"], "tipo": "banco",
    "nombre": ("Banco en " + b["TIPO_VIA"].fillna("") + " " + b["NOM_VIA"].fillna("") + " " + b["NUM_VIA"].fillna("")).str.strip(),
    "fuente_id": "AYTO_BANCOS", "accesible": None, "especie": None}, geometry=b.geometry, crs=CRS_M))

# Farmacias (Comunidad de Madrid). No cargamos el nombre del titular: es un dato personal
# que no aporta nada a la recomendación (minimización de datos).
f = leer_csv("farmacias.csv")
f = puntos(f, "LOCALIZACION_COORDENADA_X", "LOCALIZACION_COORDENADA_Y", "EPSG:25830")
f = f[f.within(zona_ampliada)]
poi.append(gpd.GeoDataFrame({
    "poi_id": "FARM_" + f["FARMACIA_NRO_SOE"], "tipo": "farmacia",
    "nombre": ("Farmacia en " + f["DIRECCION_VIAL_TIPO"].fillna("").str.title() + " "
               + f["DIRECCION_VIAL_NOMBRE"].fillna("") + ", " + f["DIRECCION_VIAL_NRO"].fillna("")).str.strip(" ,"),
    "fuente_id": "CM_FARMACIAS", "accesible": None, "especie": None}, geometry=f.geometry, crs=CRS_M))

# Metro (CRTM, GTFS modo 4). Usamos los ACCESOS (location_type=2), que es por donde se entra
# andando; la accesibilidad viene de su estación (wheelchair_boarding: 1 sí, 2 no, 0 sin dato).
m = pd.read_csv(RAW / "stops_metro.txt", dtype=str)
acc_estacion = m.set_index("stop_id")["wheelchair_boarding"].to_dict()
m = m[m["location_type"] == "2"].copy()
m["wb"] = m["parent_station"].map(acc_estacion).fillna("0")
m = puntos(m, "stop_lon", "stop_lat", "EPSG:4326")
m = m[m.within(zona_ampliada)]
poi.append(gpd.GeoDataFrame({
    "poi_id": "CRTM_" + m["stop_id"], "tipo": "metro",
    "nombre": "Metro " + m["parent_station"].map(
        pd.read_csv(RAW / "stops_metro.txt", dtype=str).set_index("stop_id")["stop_name"]).fillna("")
        + " (" + m["stop_name"] + ")",
    "fuente_id": "CRTM_GTFS_METRO",
    "accesible": m["wb"].map({"1": "si", "2": "no"}).fillna("desconocido"),
    "especie": None}, geometry=m.geometry, crs=CRS_M))

# Autobús urbano (EMT, GTFS modo 6 publicado por el CRTM). Sin dato de accesibilidad.
bus = pd.read_csv(RAW / "stops_bus.txt", dtype=str)
bus = puntos(bus, "stop_lon", "stop_lat", "EPSG:4326")
bus = bus[bus.within(zona_ampliada)]
poi.append(gpd.GeoDataFrame({
    "poi_id": "EMT_" + bus["stop_id"], "tipo": "bus", "nombre": "Bus " + bus["stop_name"],
    "fuente_id": "CRTM_GTFS_EMT", "accesible": "desconocido", "especie": None},
    geometry=bus.geometry, crs=CRS_M))

# Opcionales del Ayuntamiento: si están descargados se añaden, si no, las columnas quedan vacías
fu = leer_csv("fuentes.csv")
if fu is not None:
    xcol = next(c for c in fu.columns if c.upper().startswith("COORD_GIS_X") or c.upper() == "X")
    ycol = next(c for c in fu.columns if c.upper().startswith("COORD_GIS_Y") or c.upper() == "Y")
    fu = puntos(fu, xcol, ycol, "EPSG:25830")
    fu = fu[fu.within(zona_ampliada)]
    poi.append(gpd.GeoDataFrame({"poi_id": "FUENTE_" + fu.index.astype(str), "tipo": "fuente",
                                 "nombre": "Fuente de agua", "fuente_id": "AYTO_FUENTES",
                                 "accesible": None, "especie": None}, geometry=fu.geometry, crs=CRS_M))

ar = leer_csv("arbolado.csv")
if ar is not None:
    xcol = next(c for c in ar.columns if c.upper().startswith("COORD_GIS_X") or c.upper() == "X")
    ycol = next(c for c in ar.columns if c.upper().startswith("COORD_GIS_Y") or c.upper() == "Y")
    ecol = next((c for c in ar.columns if "ESPECIE" in c.upper()), None)
    ar = puntos(ar, xcol, ycol, "EPSG:25830")
    ar = ar[ar.within(zona_ampliada)]
    poi.append(gpd.GeoDataFrame({"poi_id": "ARBOL_" + ar.index.astype(str), "tipo": "arbol",
                                 "nombre": None, "fuente_id": "AYTO_ARBOLADO", "accesible": None,
                                 "especie": ar[ecol] if ecol else None}, geometry=ar.geometry, crs=CRS_M))

poi = gpd.GeoDataFrame(pd.concat(poi, ignore_index=True), crs=CRS_M)
poi = poi.drop_duplicates(subset="poi_id")
print("[2] Recursos:", poi["tipo"].value_counts().to_dict())

# ---------------------------------------------------------------- 3. recursos junto al tramo
def contar_cerca(tipo, radio):
    capa = poi[poi["tipo"] == tipo]
    if capa.empty:
        return pd.Series(np.nan, index=tramos.index)
    zonas = gpd.GeoDataFrame(geometry=tramos.geometry.buffer(radio), crs=CRS_M)
    cruce = gpd.sjoin(capa[["geometry"]], zonas, predicate="within")
    return cruce["index_right"].value_counts().reindex(tramos.index, fill_value=0)


tramos["benches_20m"] = contar_cerca("banco", RADIO_BANCOS)
tramos["trees_20m"] = contar_cerca("arbol", RADIO_ARBOLES)
print("[3] Bancos y árboles contados por tramo")

# ---------------------------------------------------------------- 4. distancias por la red
G = nx.Graph()
for u, v, l in zip(tramos["nodo_u"], tramos["nodo_v"], tramos["length"]):
    if not G.has_edge(u, v) or G[u][v]["w"] > l:
        G.add_edge(int(u), int(v), w=float(l))

coords = {}
for u, v, geom in zip(tramos["nodo_u"], tramos["nodo_v"], tramos.geometry):
    # la geometría ya va de nodo_u a nodo_v (ver paso 1)
    c = list(geom.coords)
    coords.setdefault(int(u), c[0])
    coords.setdefault(int(v), c[-1])
nodos = gpd.GeoDataFrame({"nodo": list(coords)},
                         geometry=gpd.points_from_xy(*zip(*coords.values())), crs=CRS_M)


def distancia_red(tipo):
    """Distancia por la red desde cada cruce al recurso más cercano de ese tipo.
    Truco: un nodo virtual 'S' unido a cada recurso con el coste de llegar andando desde la calle."""
    capa = poi[poi["tipo"].isin(tipo if isinstance(tipo, list) else [tipo])]
    if capa.empty:
        return {}, {}
    cerca = gpd.sjoin_nearest(capa[["poi_id", "geometry"]], nodos, distance_col="snap", max_distance=SNAP_MAX)
    H = G.copy()
    for poi_id, nodo, snap in zip(cerca["poi_id"], cerca["nodo"], cerca["snap"]):
        H.add_edge(("S", poi_id), int(nodo), w=float(snap))
        H.add_edge("S", ("S", poi_id), w=0.0)
    dist, caminos = nx.single_source_dijkstra(H, "S", weight="w")
    d = {n: dist[n] for n in G.nodes if n in dist}
    origen = {n: caminos[n][1][1] for n in G.nodes if n in caminos}   # ('S', poi_id) -> poi_id
    return d, origen


def asignar(tipo, col_dist, col_id=None):
    d, origen = distancia_red(tipo)
    du = tramos["nodo_u"].map(d)
    dv = tramos["nodo_v"].map(d)
    tramos[col_dist] = np.fmin(du, dv).round(1)
    if col_id:
        mejor = np.where(du <= dv, tramos["nodo_u"], tramos["nodo_v"])
        tramos[col_id] = pd.Series(mejor).map(origen)


asignar(["metro", "bus"], "dist_transport_m", "nearest_stop_id")
asignar("farmacia", "dist_pharmacy_m")
asignar("fuente", "dist_water_m")
asignar("banco", "dist_bench_m")
acc = poi.set_index("poi_id")["accesible"]
tramos["stop_accessible"] = tramos["nearest_stop_id"].map(acc).fillna("desconocido")
print("[4] Distancias por la red calculadas")

# ---------------------------------------------------------------- 5. exportar
COLS = ["tramo_id", "nodo_u", "nodo_v", "nombre_via", "tipo_via", "length_m", "slope_pct", "steps",
        "benches_20m", "dist_bench_m", "dist_transport_m", "nearest_stop_id", "stop_accessible",
        "dist_pharmacy_m", "dist_water_m", "trees_20m", "quality_flags", "geometry"]
salida = tramos[COLS].to_crs(4326)
salida.to_file(OUT / "tramos.geojson", driver="GeoJSON")
poi.to_crs(4326).to_file(OUT / "poi.geojson", driver="GeoJSON")

resumen = {
    "zona": ZONA,
    "tramos": int(len(salida)),
    "km_red": round(float(tramos["length"].sum()) / 1000, 1),
    "poi": {k: int(v) for k, v in poi["tipo"].value_counts().items()},
    "cobertura": {c: int(salida[c].notna().sum()) for c in COLS if c != "geometry"},
    "pct_tramos_transporte_300m": round(float((tramos["dist_transport_m"] <= 300).mean() * 100), 1),
    "pct_tramos_con_banco": round(float((tramos["benches_20m"] > 0).mean() * 100), 1),
}
(OUT / "resumen.json").write_text(json.dumps(resumen, indent=2, ensure_ascii=False), encoding="utf-8")
print("[5] Exportado en data/dataset/")
print(json.dumps(resumen, indent=2, ensure_ascii=False))
