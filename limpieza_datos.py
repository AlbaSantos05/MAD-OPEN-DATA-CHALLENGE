import time
from pathlib import Path

import pandas as pd
import geopandas as gpd
import osmnx as ox
import requests

# Rutas relativas a la carpeta donde está este script
BASE = Path(__file__).resolve().parent
RAW = BASE / "datos_raw"
OUT = BASE / "datos_limpios"
OUT.mkdir(exist_ok=True)  # crea la carpeta de salida si no existe


def leer_tabla(nombre):
    """Lee bancos/farmacias tanto si están en .csv como en .xls/.xlsx."""
    for ext in (".csv", ".xlsx", ".xls"):
        ruta = RAW / f"{nombre}{ext}"
        if ruta.exists():
            print(f"  -> Leyendo {ruta.name}")
            if ext == ".csv":
                return pd.read_csv(ruta, sep=";", dtype=str, encoding_errors="replace")
            return pd.read_excel(ruta, dtype=str)
    raise FileNotFoundError(f"No encuentro {nombre}.csv/.xlsx/.xls en {RAW}")


def anadir_elevaciones(G, lote=100, pausa=1.1):
    """Pide la altitud de cada nodo a Open Topo Data (modelo europeo EU-DEM, 25 m).
    Límites gratuitos: 100 puntos por consulta, 1 consulta/segundo, 1000 consultas/día."""
    url = "https://api.opentopodata.org/v1/eudem25m"
    ids = list(G.nodes)
    total = (len(ids) + lote - 1) // lote
    for i in range(0, len(ids), lote):
        grupo = ids[i:i + lote]
        locs = "|".join(f"{G.nodes[n]['y']},{G.nodes[n]['x']}" for n in grupo)
        r = requests.get(url, params={"locations": locs}, timeout=60)
        r.raise_for_status()
        datos = r.json()
        if datos.get("status") != "OK":
            raise RuntimeError(datos)
        for n, res in zip(grupo, datos["results"]):
            elev = res.get("elevation")
            G.nodes[n]["elevation"] = float(elev) if elev is not None else float("nan")
        print(f"     consulta {i // lote + 1}/{total}")
        time.sleep(pausa)
    return G


print("=== PROCESAMIENTO DE DATOS PARA CHAMBERÍ (versión 3) ===")

# 1. Red de calles y límite de Chamberí
print("[1/5] Cargando mapa de Chamberí desde OpenStreetMap...")
polygon_chamberi = ox.geocoder.geocode_to_gdf("Chamberí, Madrid, Spain")
G = ox.graph_from_place("Chamberí, Madrid, Spain", network_type="walk")

print(f"  -> Pidiendo elevaciones de {len(G.nodes)} cruces a Open Topo Data...")
try:
    G = anadir_elevaciones(G)
    G = ox.elevation.add_edge_grades(G)
    print("  -> Elevaciones y pendientes calculadas.")
except Exception as e:
    print(f"  -> Error en elevaciones (las calles quedan sin pendiente): {e}")

nodes, edges = ox.graph_to_gdfs(G)

poly_25830 = polygon_chamberi.to_crs(epsg=25830)
edges_25830 = edges.to_crs(epsg=25830)

# 2. Bancos
print("[2/5] Cargando bancos...")
df_bancos = leer_tabla("bancos")
df_bancos = df_bancos.dropna(subset=["COORD_GIS_X", "COORD_GIS_Y"])
geometry_bancos = gpd.points_from_xy(
    df_bancos["COORD_GIS_X"].str.replace(",", ".").astype(float),
    df_bancos["COORD_GIS_Y"].str.replace(",", ".").astype(float),
)
gdf_bancos = gpd.GeoDataFrame(df_bancos, geometry=geometry_bancos, crs="EPSG:25830")
bancos_chamberi = gpd.clip(gdf_bancos, poly_25830)

# 3. Farmacias
print("[3/5] Cargando farmacias...")
df_farmacias = leer_tabla("farmacias")
df_farmacias = df_farmacias.dropna(
    subset=["LOCALIZACION_COORDENADA_X", "LOCALIZACION_COORDENADA_Y"]
)
x_farm = df_farmacias["LOCALIZACION_COORDENADA_X"].str.replace(",", ".").astype(float)
y_farm = df_farmacias["LOCALIZACION_COORDENADA_Y"].str.replace(",", ".").astype(float)
gdf_farmacias = gpd.GeoDataFrame(
    df_farmacias, geometry=gpd.points_from_xy(x_farm, y_farm), crs="EPSG:25830"
)
farmacias_chamberi = gpd.clip(gdf_farmacias, poly_25830)

# 4. Paradas de Metro y Bus (GTFS)
print("[4/5] Cargando paradas de transporte público...")
df_metro = pd.read_csv(RAW / "stops_metro.txt")
gdf_metro = gpd.GeoDataFrame(
    df_metro,
    geometry=gpd.points_from_xy(df_metro["stop_lon"], df_metro["stop_lat"]),
    crs="EPSG:4326",
).to_crs(epsg=25830)
metro_chamberi = gpd.clip(gdf_metro, poly_25830)

df_bus = pd.read_csv(RAW / "stops_bus.txt")
gdf_bus = gpd.GeoDataFrame(
    df_bus,
    geometry=gpd.points_from_xy(df_bus["stop_lon"], df_bus["stop_lat"]),
    crs="EPSG:4326",
).to_crs(epsg=25830)
bus_chamberi = gpd.clip(gdf_bus, poly_25830)

# 5. Exportar a GeoJSON (en WGS84 para Leaflet/Mapbox)
print("[5/5] Exportando GeoJSONs finalizados...")

# Las calles de OSM tienen columnas con listas que GeoJSON no acepta: las pasamos a texto
edges_out = edges_25830.reset_index()
for col in edges_out.columns:
    if col != "geometry":
        edges_out[col] = edges_out[col].apply(lambda v: str(v) if isinstance(v, list) else v)

edges_out.to_crs(epsg=4326).to_file(OUT / "chamberi_calles.geojson", driver="GeoJSON")
bancos_chamberi.to_crs(epsg=4326).to_file(OUT / "chamberi_bancos.geojson", driver="GeoJSON")
farmacias_chamberi.to_crs(epsg=4326).to_file(OUT / "chamberi_farmacias.geojson", driver="GeoJSON")
metro_chamberi.to_crs(epsg=4326).to_file(OUT / "chamberi_metro.geojson", driver="GeoJSON")
bus_chamberi.to_crs(epsg=4326).to_file(OUT / "chamberi_bus.geojson", driver="GeoJSON")

print("\n--- RESUMEN EN CHAMBERÍ ---")
print(f"Tramos de calle: {len(edges_out)}")
con_pendiente = "grade" in edges_out.columns
print(f"Tramos con pendiente: {con_pendiente}")
if con_pendiente:
    g = edges_out["grade"].abs() * 100
    print(f"  Pendiente media: {g.mean():.1f} %  |  máxima: {g.max():.1f} %")
print(f"Bancos encontrados: {len(bancos_chamberi)}")
print(f"Farmacias encontradas: {len(farmacias_chamberi)}")
print(f"Accesos de Metro: {len(metro_chamberi)}")
print(f"Paradas de Autobús: {len(bus_chamberi)}")
print("----------------------------")
print("Archivos generados correctamente.")