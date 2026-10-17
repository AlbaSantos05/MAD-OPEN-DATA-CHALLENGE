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
        c["nombre"] = c["nombre_via"]
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
        for idx, u, v, k, geom in zip(c.index, c["u"], c["v"], c["key"], c.geometry):
            u, v = int(u), int(v)
            puntos = list(geom.coords)
            coords.setdefault(u, puntos[0])
            coords.setdefault(v, puntos[-1])
            self.G.add_edge(u, v, key=int(k), idx=idx)
            if getattr(self, "bidireccional", False):
                self.G.add_edge(v, u, key=int(k), idx=idx)
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

    # ---------- coste multicriterio
    def _costes(self, pesos):
        """Coste de cada tramo = longitud x (1 + penalizaciones según el perfil)."""
        c = self.calles
        f_cuesta = c["pendiente_pct"] / 5                         # 5 % de pendiente duplica el coste con peso 1
        f_descanso = 1 / (1 + c["bancos"])                        # 1 si no hay bancos, baja cuantos más haya
        f_transporte = (c["dist_transporte"] / 300).clip(upper=1)  # lejos (>300 m) = penalización máxima
        f_escalera = c["escalera"].astype(float)
        num_arboles = c.get("trees_20m", pd.Series(0, index=c.index)).fillna(0)
        f_arbolado = 1 / (1 + num_arboles)          # menos árboles a 20 m = más coste (no implica sombra)
        p_arbolado = pesos.get("arbolado", 0.0)
        factor = (
            1
            + pesos["cuesta"] * f_cuesta
            + pesos["descanso"] * f_descanso
            + pesos["transporte"] * f_transporte
            + pesos["escaleras"] * f_escalera
            + p_arbolado * f_arbolado
        )
        return c["length"] * factor

    def _ruta(self, origen, destino, costes):
        coste = costes.to_dict()

        def peso(u, v, aristas):
            return min(coste[a["idx"]] for a in aristas.values())

        nodos = nx.shortest_path(self.G, origen, destino, weight=peso)
        return [
            min(self.G[a][b].values(), key=lambda e: coste[e["idx"]])["idx"]
            for a, b in zip(nodos, nodos[1:])
        ]

    # ---------- descripción de una ruta
    def _resumen(self, idxs):
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

    def _explicar(self, perfil, corta, reco, misma):
        desc = f"Son unos {reco['longitud_m']} metros, unos {reco['minutos']} minutos a paso tranquilo"
        if reco["calles"]:
            desc += ", por " + _enumerar(reco["calles"])
        desc += "."

        pesos = perfil.pesos()
        if misma:
            texto = f"{perfil.nombre}, el camino más directo ya te viene bien. {desc}"
        else:
            # Solo mencionamos motivos relacionados con lo que le importa a esta persona
            # y que supongan una diferencia apreciable
            motivos = []
            if pesos["cuesta"] > 0 and corta["metros_en_cuesta"] - reco["metros_en_cuesta"] >= 20:
                motivos.append(f"tiene mucha menos cuesta ({reco['metros_en_cuesta']} m frente a {corta['metros_en_cuesta']} m)")
            if pesos["descanso"] > 0 and corta["max_sin_banco_m"] - reco["max_sin_banco_m"] >= 30:
                motivos.append(f"como mucho andarás {reco['max_sin_banco_m']} m seguidos sin un banco al lado "
                               f"(en el camino directo, hasta {corta['max_sin_banco_m']} m)")
            if pesos["escaleras"] > 0 and reco["escaleras"] < corta["escaleras"]:
                motivos.append("evita escaleras")
            if pesos["transporte"] > 0 and corta["dist_max_transporte_m"] - reco["dist_max_transporte_m"] >= 50:
                motivos.append("pasa más cerca del metro y el autobús")
            if not motivos:
                motivos.append("se adapta mejor a lo que necesitas")

            extra = reco["longitud_m"] - corta["longitud_m"]
            if extra <= 10:
                inicio = "te sugiero un camino casi igual de largo que el más directo, pero que"
            else:
                inicio = f"te sugiero un camino {extra} metros más largo que el más directo, pero que"
            texto = f"{perfil.nombre}, {inicio} {_enumerar(motivos)}. {desc}"

        if reco["zonas_descanso"]:
            texto += (f" Por el camino pasarás por {reco['zonas_descanso']} sitios donde sentarte"
                      f" (uno cada {reco['metros_por_zona']} metros, más o menos).")
        return texto

    # ---------------------------------------------------------------- API pública
    def recomendar_ruta(self, origen, destino, perfil):
        """origen y destino como (lat, lon). Devuelve la ruta recomendada, la más corta y la explicación."""
        o = self._nodo_cercano(*origen)
        d = self._nodo_cercano(*destino)

        idx_corta = self._ruta(o, d, self.calles["length"])
        idx_reco = self._ruta(o, d, self._costes(perfil.pesos()))
        misma = idx_corta == idx_reco
        corta, reco = self._resumen(idx_corta), self._resumen(idx_reco)

        explicacion = self._explicar(perfil, corta, reco, misma)
        parada = self.mas_cercano(*destino, "transporte")
        if parada["nombre"]:
            explicacion += (
                f" Al llegar tienes la parada de {parada['tipo']} {parada['nombre']} "
                f"a {parada['distancia_m']} metros por si prefieres volver en transporte."
            )

        return {
            "perfil": perfil.nombre,
            "misma_ruta": misma,
            "ruta_recomendada": reco,
            "ruta_mas_corta": corta,
            "explicacion": explicacion,
            "parada_cercana_destino": parada,
        }

    def mas_cercano(self, lat, lon, tipo):
        """Atajos: tipo = 'banco', 'farmacia', 'metro', 'bus' o 'transporte'."""
        capas = {
            "banco": self.bancos,
            "farmacia": self.farmacias,
            "fuente": self.fuentes,
            "metro": self.transporte[self.transporte["tipo"] == "metro"],
            "bus": self.transporte[self.transporte["tipo"] == "bus"],
            "transporte": self.transporte,
        }
        if tipo not in capas:
            raise ValueError(f"Tipo desconocido: {tipo}")
        capa = capas[tipo]
        p = _a_metros(lat, lon)
        dist = capa.distance(p)
        i = dist.idxmin()
        fila = capa.loc[i]
        pt = gpd.GeoSeries([fila.geometry], crs=CRS_M).to_crs(4326).iloc[0]
        nombre = _nombre_lugar(fila, tipo)
        return {
            "tipo": fila["tipo"] if "tipo" in fila.index else tipo,
            "nombre": nombre,
            "distancia_m": round(float(dist[i])),
            "minutos": max(1, round(float(dist[i]) / VELOCIDAD)),
            "lat": pt.y,
            "lon": pt.x,
        }


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
        r = motor.recomendar_ruta(origen, destino, perfil)
        print("\n" + "=" * 70)
        print(f"PERFIL: {perfil.nombre}   pesos: {perfil.pesos()}")
        for etiqueta, clave in (("Ruta más corta  ", "ruta_mas_corta"), ("Ruta recomendada", "ruta_recomendada")):
            s = r[clave]
            print(f"{etiqueta}: {s['longitud_m']} m | en cuesta {s['metros_en_cuesta']} m | "
                  f"pendiente máx {s['pendiente_max']} % | zonas descanso {s['zonas_descanso']} | "
                  f"máx sin banco {s['max_sin_banco_m']} m | escaleras {s['escaleras']}")
        print("\nExplicación:\n" + r["explicacion"])

    print("\n" + "=" * 70)
    print("ATAJOS desde la Glorieta de Bilbao:")
    for tipo in ("banco", "farmacia", "metro", "bus"):
        a = motor.mas_cercano(*origen, tipo)
        print(f"  {tipo:9s}: {a['nombre'] or '-'} a {a['distancia_m']} m (~{a['minutos']} min)")