from pathlib import Path
import geopandas as gpd
import folium

DATOS = Path(__file__).resolve().parent.parent / "data" / "processed"

# Calles coloreadas por pendiente (verde = llano, rojo = cuesta)
calles = gpd.read_file(DATOS / "chamberi_calles.geojson")
calles["pendiente_%"] = calles["pendiente_pct"].round(1)
calles["length"] = calles["length"].round(0)

m = calles.explore(
    column="pendiente_%", cmap="RdYlGn_r", vmin=0, vmax=10,
    tooltip=["name", "length", "pendiente_%"],
    name="Calles (pendiente %)", style_kwds={"weight": 3},
)

# Capas de puntos
capas = [
    ("chamberi_bancos.geojson", "saddlebrown", "Bancos"),
    ("chamberi_farmacias.geojson", "green", "Farmacias"),
    ("chamberi_metro.geojson", "red", "Metro"),
    ("chamberi_bus.geojson", "blue", "Bus"),
]
for archivo, color, nombre in capas:
    gdf = gpd.read_file(DATOS / archivo)
    gdf.explore(m=m, color=color, name=nombre, marker_kwds={"radius": 4})

folium.LayerControl().add_to(m)
m.save(Path(__file__).resolve().parent / "mapa_verificacion.html")
print("Mapa guardado: abre mapa_verificacion.html en el navegador")

# Los 10 tramos más empinados, para revisar el 29 %
print("\nTramos más empinados:")
top = calles.sort_values("pendiente_%", ascending=False).head(10)
print(top[["name", "length", "pendiente_%", "highway"]].to_string(index=False))