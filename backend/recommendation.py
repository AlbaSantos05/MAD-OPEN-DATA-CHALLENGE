"""Motor de recomendación del acompañante urbano (MVP Chamberí).

Carga la capa urbana generada por pipelines/limpieza_datos.py, calcula indicadores
por tramo de calle y recomienda la ruta más adecuada para cada perfil, explicando el porqué.
"""
import ast
import json
import os
import urllib.request
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import geopandas as gpd
import networkx as nx
from shapely.geometry import Point
from shapely.ops import unary_union

warnings.filterwarnings("ignore", message="Could not parse column")

RAIZ = Path(__file__).resolve().parent.parent
DATOS = RAIZ / "data" / "processed"      # capas antiguas (modo "processed")
DATASET = RAIZ / "data" / "dataset"      # copia local del dataset que está en Oracle (modo "local")
# API de lectura del dataset en Oracle (pública y de solo lectura; ver oracle/05_api_rest.sql)
ORDS_URL_POR_DEFECTO = "https://g65cd3d29f3f6ee-madcompanadb.adb.eu-madrid-1.oraclecloudapps.com/ords/maddata"
CRS_M = 25830          # sistema métrico de Madrid
RADIO_BANCOS = 20      # metros alrededor de un tramo para contar bancos (igual que en Oracle)
RADIO_GRUPO_BANCOS = 15  # bancos a menos de 2x15 m entre sí forman una misma zona de descanso
UMBRAL_CUESTA = 4      # % a partir del cual un tramo cuenta como cuesta
VELOCIDAD = 50         # metros por minuto (~3 km/h, paso tranquilo)
DIST_MAX_RED = 200     # m: si un punto está más lejos de cualquier calle, está fuera de Chamberí


# ---------------------------------------------------------------- utilidades
def _limpiar_nombre(v):
    """Los nombres de OSM pueden venir como "['Calle X', 'Calle Y']": nos quedamos con el primero."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    s = str(v)
    if s.startswith("["):
        try:
            lista = ast.literal_eval(s)
            return lista[0] if lista else None
        except (ValueError, SyntaxError):
            return s
    return s


def _a_metros(lat, lon):
    return gpd.GeoSeries([Point(lon, lat)], crs=4326).to_crs(CRS_M).iloc[0]


def _enumerar(items):
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " y " + items[-1]


def _texto(fila, col):
    v = fila.get(col)
    return str(v).strip() if isinstance(v, str) and v.strip() else ""


def _titulo(s):
    """'CALLE DE FUENCARRAL' -> 'Calle de Fuencarral'."""
    s = s.title()
    for p in ("De", "Del", "La", "Las", "Los", "El", "Y"):
        s = s.replace(f" {p} ", f" {p.lower()} ")
    return s


def _nombre_lugar(fila, tipo):
    """Nombre legible de un recurso según su capa de origen."""
    if _texto(fila, "stop_name"):                       # paradas de metro y bus (GTFS)
        return _texto(fila, "stop_name")
    if _texto(fila, "nombre_poi"):                      # dataset del proyecto (Oracle)
        return _texto(fila, "nombre_poi").replace("Banco en ", "Banco en ").strip()
    if tipo == "farmacia":                              # Comunidad de Madrid
        via = " ".join(x for x in (_texto(fila, "DIRECCION_VIAL_TIPO"),
                                   _texto(fila, "DIRECCION_VIAL_NOMBRE")) if x)
        via = _titulo(via)
        num = _texto(fila, "DIRECCION_VIAL_NRO")
        return f"Farmacia en {via}, {num}" if num else (f"Farmacia en {via}" if via else None)
    if tipo == "banco":                                 # Ayuntamiento de Madrid
        via = " ".join(x for x in (_texto(fila, "TIPO_VIA"), _texto(fila, "NOM_VIA")) if x)
        via = _titulo(via)
        num = _texto(fila, "NUM_VIA")
        return f"Banco en {via} {num}".strip() if via else None
    return None


def _leer_env():
    """Lee ORDS_URL y DATOS_FUENTE del .env de la raíz si no están ya en el entorno."""
    env = RAIZ / ".env"
    if env.exists():
        for linea in env.read_text(encoding="utf-8").splitlines():
            if "=" in linea and not linea.strip().startswith("#"):
                k, v = linea.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def _geojson_api(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        datos = json.loads(r.read().decode("utf-8"))
    return gpd.GeoDataFrame.from_features(datos["features"], crs=4326)


def _cargar_dataset(fuente):
    """Devuelve (tramos, poi) del dataset del proyecto.
    fuente = "api"   -> API de lectura de Oracle (ORDS_URL/dataset/...)
             "local" -> data/dataset/*.geojson (los mismos datos, sin conexión)"""
    if fuente == "api":
        base = os.environ.get("ORDS_URL", ORDS_URL_POR_DEFECTO).rstrip("/") + "/dataset/"
        print(f"  -> Leyendo el dataset desde la API de Oracle: {base}")
        return _geojson_api(base + "tramos.geojson"), _geojson_api(base + "poi.geojson")
    print(f"  -> Leyendo el dataset local: {DATASET}")
    return gpd.read_file(DATASET / "tramos.geojson"), gpd.read_file(DATASET / "poi.geojson")


def _cargar_centros_salud(fuente):
    """Centros de salud de la Comunidad de Madrid (tabla CENTROS_SALUD de Oracle).
    Con fuente "api" se leen de la API; si falla, o en local, de data/processed/centros_salud.csv."""
    g, origen = None, None
    if fuente == "api":
        base = os.environ.get("ORDS_URL", ORDS_URL_POR_DEFECTO).rstrip("/") + "/dataset/"
        try:
            g = _geojson_api(base + "centros_salud.geojson")
            origen = "API de Oracle"
        except Exception as e:                          # noqa: BLE001 - sin conexión, API caída...
            print(f"  -> No se pudieron leer los centros de salud de la API ({e}). Uso la copia local.")
    if g is None or g.empty:
        ruta = DATOS / "centros_salud.csv"
        if not ruta.exists():
            print("  -> Sin datos de centros de salud.")
            return gpd.GeoDataFrame({"tipo": [], "nombre_poi": []}, geometry=[], crs=CRS_M)
        df = pd.read_csv(ruta)
        g = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df["lon"], df["lat"]), crs=4326)
        origen = "copia local"
    g = g.rename(columns={"nombre": "nombre_poi"})
    g["tipo"] = "centro_salud"
    print(f"  -> {len(g)} centros de salud ({origen}).")
    return g.to_crs(CRS_M)


# ---------------------------------------------------------------- motor
class Motor:
    def __init__(self, fuente=None, carpeta=DATOS):
        """fuente: "api" (Oracle, por defecto), "local" (data/dataset, sin conexión)
        o "processed" (capas antiguas de limpieza_datos.py). También vale DATOS_FUENTE en .env."""
        _leer_env()
        fuente = fuente or os.environ.get("DATOS_FUENTE") or "api"
        print(f"Cargando capa urbana de Chamberí (fuente: {fuente})...")

        if fuente in ("api", "local"):
            try:
                tramos, poi = _cargar_dataset(fuente)
            except Exception as e:                      # sin conexión -> copia local
                if fuente != "api":
                    raise
                print(f"  -> No se pudo leer la API ({e}). Uso la copia local data/dataset/.")
                fuente = "local"
                tramos, poi = _cargar_dataset("local")
            self._desde_dataset(tramos.to_crs(CRS_M), poi.to_crs(CRS_M))
        else:
            def leer(nombre):
                return gpd.read_file(carpeta / f"chamberi_{nombre}.geojson").to_crs(CRS_M)

            self.calles = leer("calles")
            self.bancos = leer("bancos")
            self.farmacias = leer("farmacias")
            self.fuentes = leer("fuentes")    
            self.arbolado = leer("arbolado")
            metro = leer("metro")
            metro["tipo"] = "metro"
            bus = leer("bus")
            bus["tipo"] = "bus"
            self.transporte = gpd.GeoDataFrame(pd.concat([metro, bus], ignore_index=True), crs=CRS_M)
            self._calcular_indicadores()

        self.fuente = fuente
        self.centros_salud = _cargar_centros_salud(fuente)
        self._agrupar_bancos()
        self._construir_grafo()
        print(f"  -> {len(self.calles)} tramos y {self.G.number_of_nodes()} cruces listos.")

    # ---------- dataset del proyecto (Oracle) -> columnas que usa el motor
    def _desde_dataset(self, tramos, poi):
        """Los indicadores ya vienen calculados en el dataset: aquí solo se renombran.
        Cada tramo es un segmento físico; el grafo lo recorre en los dos sentidos."""
        c = tramos.copy()
        c["u"] = c["nodo_u"].astype("int64")
        c["v"] = c["nodo_v"].astype("int64")
        c["key"] = c["tramo_id"].astype("int64")
        c["length"] = c["length_m"].astype(float)
        c["pendiente_pct"] = c["slope_pct"].fillna(0).astype(float)
        c["escalera"] = c["steps"] == "si"
        # Algunos tramos del dataset traen dos calles pegadas ("Calle A' 'Calle B"): nos quedamos con la primera
        c["nombre"] = c["nombre_via"].where(c["nombre_via"].isna(), c["nombre_via"].astype(str).str.split("' '").str[0])
        c["bancos"] = c["benches_20m"].fillna(0).astype(int)
        c["dist_transporte"] = c["dist_transport_m"].astype(float)
        self.calles = c.set_index("tramo_id", drop=False)
        self.calles.index.name = None
        self.bidireccional = True

        poi = poi.rename(columns={"nombre": "nombre_poi"})
        self.bancos = poi[poi["tipo"] == "banco"].copy()
        self.farmacias = poi[poi["tipo"] == "farmacia"].copy()
        self.fuentes = poi[poi["tipo"] == "fuente"].copy()      # <-- AÑADIR ESTA LÍNEA
        self.arbolado = poi[poi["tipo"] == "arbol"].copy()
        self.transporte = poi[poi["tipo"].isin(["metro", "bus"])].copy()
        self.transporte["stop_name"] = self.transporte["nombre_poi"].str.replace(r"^(Metro|Bus) ", "", regex=True)

    # ---------- zonas de descanso
    def _agrupar_bancos(self):
        """Agrupa bancos a menos de ~30 m entre sí en una única 'zona de descanso'
        (una plaza con 20 bancos es un solo sitio donde sentarse, no 20)."""
        union = unary_union(list(self.bancos.geometry.buffer(RADIO_GRUPO_BANCOS)))
        geoms = list(union.geoms) if hasattr(union, "geoms") else [union]
        zonas = gpd.GeoDataFrame(geometry=geoms, crs=CRS_M)
        cruce = gpd.sjoin(self.bancos[["geometry"]], zonas, predicate="within")
        zonas["n_bancos"] = cruce["index_right"].value_counts().reindex(zonas.index, fill_value=0)
        zonas["geometry"] = zonas.centroid
        self.zonas_descanso = zonas

    # ---------- indicadores por tramo
    def _calcular_indicadores(self):
        c = self.calles
        c["nombre"] = c["name"].apply(_limpiar_nombre)
        c["pendiente_pct"] = c["pendiente_pct"].fillna(0)
        c["escalera"] = c["highway"].astype(str).str.contains("steps")

        # Bancos a menos de RADIO_BANCOS metros de cada tramo
        zonas = gpd.GeoDataFrame(geometry=c.geometry.buffer(RADIO_BANCOS), crs=CRS_M)
        cruce = gpd.sjoin(self.bancos[["geometry"]], zonas, predicate="within")
        c["bancos"] = cruce["index_right"].value_counts().reindex(c.index, fill_value=0)

        # Distancia desde el punto medio del tramo a la parada de metro/bus más cercana
        medios = gpd.GeoDataFrame(geometry=c.geometry.interpolate(0.5, normalized=True), crs=CRS_M)
        cerca = gpd.sjoin_nearest(medios, self.transporte[["geometry"]], distance_col="d")
        c["dist_transporte"] = cerca.groupby(level=0)["d"].min().reindex(c.index)

    # ---------- red peatonal
    def _construir_grafo(self):
        c = self.calles
        self.G = nx.MultiDiGraph()
        coords = {}
        for idx, u, v, k, geom, largo in zip(c.index, c["u"], c["v"], c["key"], c.geometry, c["length"]):
            u, v = int(u), int(v)
            puntos = list(geom.coords)
            coords.setdefault(u, puntos[0])
            coords.setdefault(v, puntos[-1])
            self.G.add_edge(u, v, key=int(k), idx=idx, largo=float(largo))
            if getattr(self, "bidireccional", False):
                self.G.add_edge(v, u, key=int(k), idx=idx, largo=float(largo))
        self.nodos = gpd.GeoDataFrame(
            {"id": list(coords)}, geometry=[Point(xy) for xy in coords.values()], crs=CRS_M
        )

    def _nodo_cercano(self, lat, lon):
        p = _a_metros(lat, lon)
        dist = self.nodos.distance(p)
        i = dist.idxmin()
        if dist[i] > DIST_MAX_RED:
            raise ValueError(f"El punto ({lat}, {lon}) está fuera de la zona del prototipo (Chamberí).")
        return int(self.nodos.loc[i, "id"])

    # ---------- rutas: siempre la más corta ANDANDO
    # El motor no elige por la persona un camino «más adecuado» y más largo: da el más corto, y el
    # acompañante avisa por el camino (cuestas, escaleras...) y ofrece alternativas diciendo cuánto
    # alargan. La persona decide. Si se desvía, el acompañante recalcula la ruta en cada paso.
    def distancias_a(self, nodo):
        """Metros andando desde cualquier cruce hasta `nodo` (la red es de doble sentido).
        Se guarda por destino: mientras la persona va hacia él, cada paso solo consulta la tabla."""
        cache = self.__dict__.setdefault("_cache_destinos", {})
        if nodo not in cache:
            if len(cache) > 32:
                cache.clear()
            cache[nodo] = nx.single_source_dijkstra_path_length(self.G, nodo, weight="largo")
        return cache[nodo]

    def camino_corto(self, nodo_origen, nodo_destino):
        """Tramos (en orden) del camino más corto andando entre dos cruces."""
        nodos = nx.shortest_path(self.G, nodo_origen, nodo_destino, weight="largo")
        return [min(self.G[a][b].values(), key=lambda e: e["largo"])["idx"] for a, b in zip(nodos, nodos[1:])]

    # ---------- descripción de una ruta
    def _resumen(self, idxs):
        if not idxs:
            return {"longitud_m": 0, "minutos": 0, "metros_en_cuesta": 0, "pendiente_max": 0.0, "escaleras": 0,
                    "bancos": 0, "zonas_descanso": 0, "metros_por_zona": None, "max_sin_banco_m": 0,
                    "dist_max_transporte_m": 0, "calles": [], "coordenadas": []}
        t = self.calles.loc[idxs]
        longitud = float(t["length"].sum())
        trazado = unary_union(list(t.geometry))
        zona_ruta = trazado.buffer(RADIO_BANCOS)
        bancos = int(self.bancos.within(zona_ruta).sum())
        zonas = int(self.zonas_descanso.within(zona_ruta).sum())

        # Distancia máxima que hay que andar seguida sin ningún banco cerca
        hueco = max_sin_banco = 0.0
        for largo, n in zip(t["length"], t["bancos"]):
            if n == 0:
                hueco += largo
                max_sin_banco = max(max_sin_banco, hueco)
            else:
                hueco = 0.0

        # Las 3 calles principales, en el orden en que se recorren
        largos = t.dropna(subset=["nombre"]).groupby("nombre")["length"].sum()
        top = set(largos.sort_values(ascending=False).head(3).index)
        calles = []
        for n in t["nombre"]:
            if n in top and n not in calles:
                calles.append(n)

        lineas = [[(y, x) for x, y in g.coords] for g in t.to_crs(4326).geometry]
        return {
            "longitud_m": round(longitud),
            "minutos": max(1, round(longitud / VELOCIDAD)),
            "metros_en_cuesta": round(float(t.loc[t["pendiente_pct"] >= UMBRAL_CUESTA, "length"].sum())),
            "pendiente_max": round(float(t["pendiente_pct"].max()), 1) if len(t) else 0.0,
            "escaleras": int(t["escalera"].sum()),
            "bancos": bancos,
            "zonas_descanso": zonas,
            "metros_por_zona": round(longitud / zonas) if zonas else None,
            "max_sin_banco_m": round(max_sin_banco),
            "dist_max_transporte_m": round(float(t["dist_transporte"].max())) if len(t) else 0,
            "calles": calles,
            "coordenadas": lineas,  # para pintar la ruta en el mapa del frontend
        }

    def _describir(self, r, perfil=None):
        """La ruta en palabras, con lo que le importa a esta persona (sin elegir por ella)."""
        texto = f"Son unos {r['longitud_m']} metros, unos {r['minutos']} minutos a paso tranquilo"
        if r["calles"]:
            texto += ", por " + _enumerar(r["calles"])
        texto += "."
        avisos = []
        if perfil is not None and (perfil.cuestas != "nada" or perfil.cansado) and r["metros_en_cuesta"] >= 20:
            avisos.append(f"hay unos {r['metros_en_cuesta']} m de cuesta (hasta un {r['pendiente_max']:.0f} %)")
        if perfil is not None and perfil.escaleras and r["escaleras"]:
            avisos.append("hay escaleras" if r["escaleras"] > 1 else "hay un tramo de escaleras")
        if avisos:
            texto += f" Por el camino {_enumerar(avisos)}: te avisaré antes de llegar y te diré cómo evitarlo."
        if r["zonas_descanso"]:
            texto += (f" Pasarás por {r['zonas_descanso']} sitios donde sentarte"
                      f" (uno cada {r['metros_por_zona']} metros, más o menos).")
        if perfil is not None and perfil.nombre and perfil.nombre != "Usuario":
            texto = f"{perfil.nombre}, {texto[0].lower()}{texto[1:]}"
        return texto

    # ---------------------------------------------------------------- API pública
    def ruta(self, origen, destino, perfil=None):
        """origen y destino como (lat, lon). Ruta MÁS CORTA andando, descrita en palabras, y la parada
        de transporte más cercana al destino."""
        o = self._nodo_cercano(*origen)
        d = self._nodo_cercano(*destino)
        r = self._resumen(self.camino_corto(o, d)) if o != d else self._resumen([])
        # Más el trozo de la persona al primer cruce y del último cruce al destino
        xy = dict(zip(self.nodos["id"], self.nodos.geometry))
        acceso = _a_metros(*origen).distance(xy[o]) + _a_metros(*destino).distance(xy[d])
        r["longitud_m"] = round(r["longitud_m"] + acceso)
        r["minutos"] = max(1, round(r["longitud_m"] / VELOCIDAD))
        explicacion = self._describir(r, perfil)
        parada = self.mas_cercano(*destino, "transporte")
        if parada["nombre"]:
            explicacion += (f" Al llegar tienes la parada de {parada['tipo']} {parada['nombre']} "
                            f"a {parada['distancia_m']} metros por si prefieres volver en transporte.")
        return {
            "ruta": r,
            "ruta_recomendada": r,          # mismo contenido: nombre que ya lee la interfaz
            "explicacion": explicacion,
            "parada_cercana_destino": parada,
        }

    def _distancias_red(self, origen):
        """Metros andando desde un cruce a todos los demás (hasta 3 km). En un mismo paso del paseo
        se busca banco, transporte, fuente... desde el mismo cruce: se guardan los últimos."""
        cache = self.__dict__.setdefault("_cache_red", {})
        if origen not in cache:
            if len(cache) > 64:
                cache.clear()
            cache[origen] = nx.single_source_dijkstra_path_length(self.G, origen, cutoff=3000, weight="largo")
        return cache[origen]

    def _mas_cercano_andando(self, p, capa, dist, candidatos=8):
        """De los `candidatos` más cercanos en línea recta, el más cercano ANDANDO por la red peatonal
        (una vía de tren o una manzana cerrada pueden convertir 50 m en línea recta en 400 andando).
        Devuelve (índice, metros andando). Sin red cerca (fuera de la zona con datos): línea recta."""
        cerca = dist.nsmallest(candidatos)
        d_nodos = self.nodos.distance(p)
        j = d_nodos.idxmin()
        if d_nodos[j] > DIST_MAX_RED:
            return cerca.index[0], float(cerca.iloc[0])
        origen = int(self.nodos.loc[j, "id"])
        red = self._distancias_red(origen)
        mejor = (cerca.index[0], float(cerca.iloc[0]))
        mejor_m = None
        for i, recto in cerca.items():
            g = capa.at[i, "geometry"]
            dn = self.nodos.distance(g)
            k = dn.idxmin()
            nodo = int(self.nodos.loc[k, "id"])
            if nodo not in red:
                continue
            # metros: de la persona a su cruce + red + del cruce al recurso (nunca menos que en línea recta)
            m = max(float(recto), float(d_nodos[j]) + red[nodo] + float(dn[k]))
            if mejor_m is None or m < mejor_m:
                mejor, mejor_m = (i, m), m
        return mejor

    def mas_cercano(self, lat, lon, tipo):
        """Atajos: tipo = 'banco', 'farmacia', 'fuente', 'metro', 'bus', 'transporte' o 'centro_salud'."""
        capas = {
            "banco": self.bancos,
            "farmacia": self.farmacias,
            "fuente": self.fuentes,
            "metro": self.transporte[self.transporte["tipo"] == "metro"],
            "bus": self.transporte[self.transporte["tipo"] == "bus"],
            "transporte": self.transporte,
            "centro_salud": self.centros_salud,
        }
        if tipo not in capas:
            raise ValueError(f"Tipo desconocido: {tipo}")
        capa = capas[tipo]
        if len(capa) == 0:
            raise ValueError(f"No hay datos de {tipo.replace('_', ' ')}.")
        p = _a_metros(lat, lon)
        dist = capa.distance(p)
        i, metros = self._mas_cercano_andando(p, capa, dist)
        fila = capa.loc[i]
        pt = gpd.GeoSeries([fila.geometry], crs=CRS_M).to_crs(4326).iloc[0]
        nombre = _nombre_lugar(fila, tipo)
        r = {
            "tipo": fila["tipo"] if "tipo" in fila.index else tipo,
            "nombre": nombre,
            "distancia_m": round(metros),
            "minutos": max(1, round(metros / VELOCIDAD)),
            "lat": pt.y,
            "lon": pt.x,
        }
        for extra in ("direccion", "url"):                # centros de salud: dirección y ficha oficial
            if _texto(fila, extra):
                r[extra] = _texto(fila, extra)
        return r


# ---------------------------------------------------------------- prueba en terminal
if __name__ == "__main__":
    from profiles import Perfil

    motor = Motor()

    origen = (40.4290, -3.7020)    # Glorieta de Bilbao
    destino = (40.4385, -3.7125)   # zona de Metro Islas Filipinas (aprox.)

    perfiles = [
        Perfil("Antonio"),
        Perfil("Carmen", cuestas="mucho", descanso="mucho", escaleras=True),
    ]

    for perfil in perfiles:
        r = motor.ruta(origen, destino, perfil)
        s_ = r["ruta"]
        print("\n" + "=" * 70)
        print(f"PERFIL: {perfil.nombre}: {s_['longitud_m']} m | en cuesta {s_['metros_en_cuesta']} m | "
              f"escaleras {s_['escaleras']} | zonas de descanso {s_['zonas_descanso']}")
        print("Explicación:\n" + r["explicacion"])

    print("\n" + "=" * 70)
    print("ATAJOS desde la Glorieta de Bilbao:")
    for tipo in ("banco", "farmacia", "metro", "bus"):
        a = motor.mas_cercano(*origen, tipo)
        print(f"  {tipo:9s}: {a['nombre'] or '-'} a {a['distancia_m']} m (~{a['minutos']} min)")