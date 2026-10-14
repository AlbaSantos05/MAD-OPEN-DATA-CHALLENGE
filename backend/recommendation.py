"""Motor de recomendación del acompañante urbano (MVP Chamberí).

Carga la capa urbana generada por pipelines/limpieza_datos.py, calcula indicadores
por tramo de calle y recomienda la ruta más adecuada para cada perfil, explicando el porqué.
"""
import ast
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import geopandas as gpd
import networkx as nx
from shapely.geometry import Point
from shapely.ops import unary_union

warnings.filterwarnings("ignore", message="Could not parse column")

DATOS = Path(__file__).resolve().parent.parent / "data" / "processed"
CRS_M = 25830          # sistema métrico de Madrid
RADIO_BANCOS = 30      # metros alrededor de un tramo para contar bancos
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


# ---------------------------------------------------------------- motor
class Motor:
    def __init__(self, carpeta=DATOS):
        print("Cargando capa urbana de Chamberí...")

        def leer(nombre):
            return gpd.read_file(carpeta / f"chamberi_{nombre}.geojson").to_crs(CRS_M)

        self.calles = leer("calles")
        self.bancos = leer("bancos")
        self.farmacias = leer("farmacias")
        metro = leer("metro")
        metro["tipo"] = "metro"
        bus = leer("bus")
        bus["tipo"] = "bus"
        self.transporte = gpd.GeoDataFrame(pd.concat([metro, bus], ignore_index=True), crs=CRS_M)

        self._calcular_indicadores()
        self._construir_grafo()
        print(f"  -> {len(self.calles)} tramos y {self.G.number_of_nodes()} cruces listos.")

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
        factor = (
            1
            + pesos["cuesta"] * f_cuesta
            + pesos["descanso"] * f_descanso
            + pesos["transporte"] * f_transporte
            + pesos["escaleras"] * f_escalera
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
        bancos = int(self.bancos.within(trazado.buffer(RADIO_BANCOS)).sum())
        calles = (
            t.dropna(subset=["nombre"]).groupby("nombre")["length"].sum()
            .sort_values(ascending=False).head(3).index.tolist()
        )
        lineas = [[(y, x) for x, y in g.coords] for g in t.to_crs(4326).geometry]
        return {
            "longitud_m": round(longitud),
            "minutos": max(1, round(longitud / VELOCIDAD)),
            "metros_en_cuesta": round(float(t.loc[t["pendiente_pct"] >= UMBRAL_CUESTA, "length"].sum())),
            "pendiente_max": round(float(t["pendiente_pct"].max()), 1) if len(t) else 0.0,
            "escaleras": int(t["escalera"].sum()),
            "bancos": bancos,
            "metros_por_banco": round(longitud / bancos) if bancos else None,
            "dist_max_transporte_m": round(float(t["dist_transporte"].max())) if len(t) else 0,
            "calles": calles,
            "coordenadas": lineas,  # para pintar la ruta en el mapa del frontend
        }

    def _explicar(self, perfil, corta, reco, misma):
        desc = f"Son unos {reco['longitud_m']} metros, unos {reco['minutos']} minutos a paso tranquilo"
        if reco["calles"]:
            desc += ", por " + _enumerar(reco["calles"])
        desc += "."

        if misma:
            texto = f"{perfil.nombre}, el camino más directo ya te viene bien. {desc}"
        else:
            motivos = []
            if reco["metros_en_cuesta"] < corta["metros_en_cuesta"]:
                motivos.append(f"tiene menos cuesta ({reco['metros_en_cuesta']} m frente a {corta['metros_en_cuesta']} m)")
            if reco["bancos"] > corta["bancos"]:
                motivos.append(f"tiene más bancos para descansar ({reco['bancos']} frente a {corta['bancos']})")
            if reco["escaleras"] < corta["escaleras"]:
                motivos.append("evita escaleras")
            if reco["dist_max_transporte_m"] < corta["dist_max_transporte_m"]:
                motivos.append("pasa más cerca del metro y el autobús")
            if not motivos:
                motivos.append("se adapta mejor a lo que necesitas")

            extra = reco["longitud_m"] - corta["longitud_m"]
            if extra <= 10:
                inicio = "te sugiero un camino casi igual de largo que el más directo, pero que"
            else:
                inicio = f"te sugiero un camino {extra} metros más largo que el más directo, pero que"
            texto = f"{perfil.nombre}, {inicio} {_enumerar(motivos)}. {desc}"

        if reco["metros_por_banco"]:
            texto += f" Encontrarás un banco aproximadamente cada {reco['metros_por_banco']} metros."
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
        nombre = next(
            (fila[col] for col in ("stop_name", "NOMBRE", "DENOMINACION", "TITULAR", "DIRECCION")
             if col in fila.index and isinstance(fila[col], str)),
            None,
        )
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
                  f"pendiente máx {s['pendiente_max']} % | bancos {s['bancos']} | escaleras {s['escaleras']}")
        print("\nExplicación:\n" + r["explicacion"])

    print("\n" + "=" * 70)
    print("ATAJOS desde la Glorieta de Bilbao:")
    for tipo in ("banco", "farmacia", "metro", "bus"):
        a = motor.mas_cercano(*origen, tipo)
        print(f"  {tipo:9s}: {a['nombre'] or '-'} a {a['distancia_m']} m (~{a['minutos']} min)")