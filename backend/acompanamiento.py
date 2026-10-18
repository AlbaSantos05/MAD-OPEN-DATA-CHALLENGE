"""Acompañamiento libre: la persona pasea SIN destino y el sistema avisa solo cuando hay algo útil.

No dibuja una ruta. En cada paso del paseo:
  1. sitúa la posición sobre el tramo de calle más cercano y deduce el sentido de la marcha;
  2. mira unos 150 m por delante en el grafo peatonal (todas las ramas del próximo cruce);
  3. evalúa reglas con los datos del dataset + el perfil + el contexto (cansancio, metros
     andados, mes y hora);
  4. decide si habla: como mucho un aviso por paso, el más prioritario, sin repetirse.

Todas las decisiones salen de datos (pendiente, escaleras, bancos, transporte, fuentes,
arbolado x polen, calidad del aire). El modelo de lenguaje no decide nada aquí.

El servicio NO guarda estado: recibe el `contexto` y devuelve el contexto actualizado,
que el frontend reenvía en el paso siguiente. Funciona igual en local y en Hugging Face.
"""
import json
import math
import os
import threading
import urllib.request
from dataclasses import asdict, replace
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import LineString, Point
from shapely.ops import substring

try:
    from backend.profiles import NIVELES
except ImportError:                                   # ejecutado desde backend/
    from profiles import NIVELES

RAIZ = Path(__file__).resolve().parent.parent
PROC = RAIZ / "data" / "processed"
DATASET = RAIZ / "data" / "dataset"
CRS_M = 25830
ORDS_URL_POR_DEFECTO = "https://g65cd3d29f3f6ee-madcompanadb.adb.eu-madrid-1.oraclecloudapps.com/ords/maddata"


def _ords_url(recurso):
    return os.environ.get("ORDS_URL", ORDS_URL_POR_DEFECTO).rstrip("/") + "/dataset/" + recurso


def _ords_filas(recurso):
    """Todas las filas de un endpoint paginado de ORDS (sigue el enlace «next»)."""
    url, filas = _ords_url(recurso), []
    while url:
        with urllib.request.urlopen(url, timeout=30) as r:
            datos = json.loads(r.read().decode("utf-8"))
        filas += datos.get("items", [])
        url = next((l["href"] for l in datos.get("links", []) if l.get("rel") == "next"), None) \
            if datos.get("hasMore") else None
    return pd.DataFrame(filas)

HORIZONTE_M = 150          # cuánto mira por delante
RADIO_ARBOLES = 15         # m: árbol "en" el tramo (igual que trees_20m usa 15 m en el dataset)
SEPARACION_MIN_M = 40      # nunca dos avisos en menos de estos metros andados
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]

# Prioridad (mayor = más importante) y metros mínimos antes de repetir el mismo tipo de aviso
REGLAS = {
    "alerta_salud": (100, 0),
    "llegada":   (96, 0),       # modo destino: ha llegado
    "reaccion":  (95, 0),       # respuesta inmediata a lo que la persona acaba de decir
    "barrera":   (90, 0),       # por delante hay algo que para ESTA persona es una barrera (rojo)
    "cansancio": (70, 600),
    "incomodidad": (62, 0),     # por delante hay algo incómodo para ella (amarillo); espera por motivo
    "descanso":  (60, 250),
    "agua":      (30, 500),
    "centro_salud": (25, 800),  # pasa junto a un centro de salud (informativo, poco frecuente)
    "inicio":    (20, 10 ** 9),
}
# Metros antes de volver a avisar del mismo motivo (además, de un mismo tramo solo se avisa una vez)
ESPERA_MOTIVO = {"escaleras": 100, "cuesta_fuerte": 150, "cuesta": 400, "polen": 500, "descanso": 400,
                 "transporte": 500, "sombra": 400}

FUENTES_DATOS = {
    "pendiente": "Pendiente por tramo (EU-DEM sobre red OSM)",
    "escaleras": "OpenStreetMap (highway=steps)",
    "bancos": "Ayuntamiento de Madrid · Mobiliario urbano: bancos",
    "transporte": "Consorcio Regional de Transportes (CRTM) · GTFS Metro y EMT",
    "fuentes": "Ayuntamiento de Madrid · Fuentes de agua para beber",
    "arbolado": "Ayuntamiento de Madrid · Arbolado municipal",
    "polen": "Comunidad de Madrid · Red Palinológica (captadores AYTM y FACF)",
    "aire": "Ayuntamiento de Madrid · Calidad del aire, estación 38 (Cuatro Caminos)",
    "farmacias": "Comunidad de Madrid · Farmacias",
    "centros_salud": "Comunidad de Madrid · Centros de Salud (IDEM)",
}

# ---------------------------------------------------------------- clasificación personal de tramos
# Cada tramo tiene, PARA ESTA PERSONA y en este momento, una lista de motivos con su nivel:
#   barrera (rojo) · incomodidad (amarillo). El color del tramo es su peor motivo; sin motivos, verde.
# El perfil y lo que dice en el paseo deciden QUÉ motivos cuentan y CON QUÉ UMBRAL:
CUESTA = {"nada": (None, 9.0), "algo": (7.0, 5.0), "mucho": (5.0, 3.0)}   # % (rojo desde, amarillo desde)
BANCO_M = {"nada": None, "algo": 300, "mucho": 150}          # amarillo si el banco más cercano está más lejos
TRANSPORTE_M = {"nada": None, "algo": 500, "mucho": 300}     # amarillo si la parada más cercana está más lejos
POLEN_MIN_ARBOLES = 5        # alérgica: amarillo con 5+ árboles cuyo polen está medio/alto ese mes
SOMBRA_FUENTE_M = 300        # con calor: sin árboles y fuente a más de 300 m
LARGO_MIN_CUESTA = 15        # en tramos más cortos la pendiente del modelo digital no es fiable
CATEGORIA = {"barrera": "no apta", "incomodidad": "regular", None: "apta"}
MOTIVOS = [("escaleras", "barrera"), ("cuesta_fuerte", "barrera"), ("cuesta", "incomodidad"),
           ("polen", "incomodidad"), ("descanso", "incomodidad"), ("transporte", "incomodidad"),
           ("sombra", "incomodidad")]
RADAR_M = 120                # radio que vigila alrededor de la posición

# Estados que la persona puede decir durante el paseo (su efecto: perfil_efectivo y umbrales)
ESTADOS = ("cansado", "calor", "mareo", "dolor_piernas")
_SUBIR = {"nada": "algo", "algo": "mucho", "mucho": "mucho"}
REACCION_CUBRE = {"cansado": ("descanso", "cansancio"), "calor": ("agua", "descanso"),
                  "mareo": ("descanso", "cansancio"), "dolor_piernas": ("descanso", "cansancio")}


def _lista(partes):
    return partes[0] if len(partes) == 1 else ", ".join(partes[:-1]) + " y " + partes[-1]


# ---------------------------------------------------------------- utilidades geométricas
def _rumbo(p, q):
    """Rumbo en grados (0 = norte, 90 = este) de p a q, en coordenadas métricas."""
    return math.degrees(math.atan2(q[0] - p[0], q[1] - p[1])) % 360


def _giro(rumbo_llegada, rumbo_salida):
    """Diferencia firmada en grados (-180..180): positivo = derecha."""
    return (rumbo_salida - rumbo_llegada + 180) % 360 - 180


def _palabra_giro(dif):
    if abs(dif) < 35:
        return "recto"
    if abs(dif) > 145:
        return "atrás"
    return "a la derecha" if dif > 0 else "a la izquierda"


def _calle(v):
    """Nombre legible de la calle o None (OSM deja sin nombre aceras y pasos)."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    s = str(v).strip()
    return s or None


def _por(calle):
    return f" por {calle}" if calle else ""


def _coords_desde(geom, nodo_xy):
    """Coordenadas de la línea empezando por el extremo más cercano a nodo_xy."""
    c = list(geom.coords)
    if Point(c[0]).distance(Point(nodo_xy)) <= Point(c[-1]).distance(Point(nodo_xy)):
        return c
    return c[::-1]


# ---------------------------------------------------------------- acompañante
class Acompanante:
    def __init__(self, motor):
        self.m = motor
        self.calles = motor.calles                       # índice = idx del tramo, CRS 25830
        self.xy = {int(i): (g.x, g.y) for i, g in zip(motor.nodos["id"], motor.nodos.geometry)}

        # Vecinos de cada cruce: nodo -> [(idx_tramo, otro_nodo)]
        self.vecinos = {}
        for idx, u, v in zip(self.calles.index, self.calles["u"], self.calles["v"]):
            self.vecinos.setdefault(int(u), []).append((idx, int(v)))
            self.vecinos.setdefault(int(v), []).append((idx, int(u)))

        self._cargar_ambiente()
        self._polen_por_tramo()
        c = self.calles
        sin = pd.Series(float("nan"), index=c.index)
        # Distancias del dataset (si el motor viene de datos antiguos, se aproximan)
        self._dist_banco = c.get("dist_bench_m", sin).astype(float).fillna(
            c["bancos"].map(lambda n: 0.0 if n > 0 else 300.0))
        self._dist_agua = c.get("dist_water_m", sin).astype(float).fillna(500.0)
        self._arboles = c.get("trees_20m", sin).astype(float).fillna(0.0)
        self._cache_idon = {}
        self._paso = threading.local()
        self._calles_4326 = c.geometry.to_crs(4326)
        print(f"  -> Acompañante listo: {len(self.vecinos)} cruces, "
              f"{len(self.polen_mes)} filas de polen, {len(self.aire)} de aire, "
              f"{sum(sum(d.values()) for d in self.polen_tramo.values())} árboles con polen junto a tramos.")
        print("     Origen de los datos ambientales: " + ", ".join(f"{k.strip('/')}={v}" for k, v in self.origen.items()))

    # ------------------------------------------------------------ datos ambientales
    def _usa_api(self):
        """Mismo origen que el motor: si las calles vienen de Oracle, el ambiente también."""
        return getattr(self.m, "fuente", None) == "api"

    def _tabla(self, recurso, csv):
        """Filas de Oracle (API ORDS) si el motor usa la API; si falla o se trabaja en local,
        la copia de data/processed/. Guarda de dónde salió cada tabla en self.origen."""
        if self._usa_api():
            try:
                df = _ords_filas(recurso)
                if not df.empty:
                    self.origen[recurso] = "API de Oracle"
                    return df
                print(f"  -> La API devolvió 0 filas en {recurso}. Uso {csv}.")
            except Exception as e:                       # noqa: BLE001 - sin conexión, API caída...
                print(f"  -> No se pudo leer {recurso} de la API ({e}). Uso {csv}.")
        ruta = PROC / csv
        if ruta.exists():
            self.origen[recurso] = "copia local"
            return pd.read_csv(ruta)
        self.origen[recurso] = "sin datos"
        return pd.DataFrame()

    def _cargar_ambiente(self):
        self.origen = {}
        self.polen_mes = {}
        for f in self._tabla("polen/", "polen_mes.csv").itertuples():
            self.polen_mes[(f.tipo_polen, int(f.mes))] = (f.nivel, float(f.nivel_medio))
        esp = self._tabla("especie_polen/", "especie_polen.csv")
        self.especie_polen = dict(esp[["especie", "tipo_polen"]].values) if not esp.empty else {}
        self.aire = {}
        for f in self._tabla("aire/", "aire_mes_hora.csv").itertuples():
            self.aire[(int(f.mes), int(f.hora))] = (f.categoria, float(f.pct_regular_o_peor))

    def _arboles_con_polen(self):
        """Árboles con su tipo de polen, en CRS métrico: de la API (arboles.geojson) o en local."""
        arboles = getattr(self.m, "arbolado", None)
        if arboles is not None and len(arboles) > 0:     # el motor ya los trae (modo local)
            self.origen["arboles"] = "motor"
            a = arboles.copy()
            a["tipo_polen"] = a["especie"].map(self.especie_polen)
            return a
        if self._usa_api():
            try:
                with urllib.request.urlopen(_ords_url("arboles.geojson"), timeout=60) as r:
                    datos = json.loads(r.read().decode("utf-8"))
                a = gpd.GeoDataFrame.from_features(datos["features"], crs=4326).to_crs(CRS_M)
                if not a.empty:
                    self.origen["arboles"] = "API de Oracle"
                    return a
            except Exception as e:                       # noqa: BLE001
                print(f"  -> No se pudo leer arboles.geojson de la API ({e}). Uso data/dataset/poi.geojson.")
        ruta = DATASET / "poi.geojson"
        if not ruta.exists():
            self.origen["arboles"] = "sin datos"
            return None
        poi = gpd.read_file(ruta)
        a = poi[poi["tipo"] == "arbol"].to_crs(CRS_M)
        a["tipo_polen"] = a["especie"].map(self.especie_polen)
        self.origen["arboles"] = "copia local"
        return a

    def _polen_por_tramo(self):
        """tramo -> {tipo_polen: nº de árboles a menos de 15 m}. Cruza arbolado (Ayto.) con
        la tabla especie -> tipo de polen (calibrada con la red palinológica de la CM)."""
        self.polen_tramo = {}
        arboles = self._arboles_con_polen()
        if arboles is None:
            return
        arboles = arboles.dropna(subset=["tipo_polen"])
        if arboles.empty:
            return
        zonas = gpd.GeoDataFrame({"idx": self.calles.index},
                                 geometry=self.calles.geometry.buffer(RADIO_ARBOLES).values, crs=CRS_M)
        cruce = gpd.sjoin(arboles[["tipo_polen", "geometry"]], zonas, predicate="within")
        for (idx, tipo), n in cruce.groupby(["idx", "tipo_polen"]).size().items():
            self.polen_tramo.setdefault(idx, {})[tipo] = int(n)

    def _polen_activo(self, idxs, mes):
        """Árboles con polen en nivel medio/alto este mes a lo largo de unos tramos."""
        total, detalle = 0, {}
        for idx in idxs:
            for tipo, n in self.polen_tramo.get(idx, {}).items():
                nivel = self.polen_mes.get((tipo, mes), (None, 0))[0]
                if nivel in ("medio", "alto"):
                    total += n
                    detalle[tipo] = (detalle.get(tipo, (0, nivel))[0] + n, nivel)
        return total, detalle

    # ------------------------------------------------------------ clasificación personal de tramos
    @staticmethod
    def estados(perfil, ctx):
        """Estados activos: los que la persona ha dicho en el paseo (+ «cansado» del perfil)."""
        e = set((ctx or {}).get("estados", []))
        if getattr(perfil, "cansado", False):
            e.add("cansado")
        return e

    @staticmethod
    def perfil_efectivo(perfil, estados):
        """El perfil tal y como está AHORA según lo que ha dicho:
        cansada / le duelen las piernas -> descanso sube un nivel · mareo -> descanso «mucho» y
        transporte al menos «algo» · calor -> arbolado «mucho». (Las cuestas bajan su umbral en `umbrales`.)"""
        cambios, descanso = {}, perfil.descanso
        if estados & {"cansado", "dolor_piernas"}:
            descanso = _SUBIR[descanso]
            cambios["cansado"] = True
        if "mareo" in estados:
            descanso = "mucho"
            cambios["cansado"] = True
            if perfil.transporte == "nada":
                cambios["transporte"] = "algo"
        if descanso != perfil.descanso:
            cambios["descanso"] = descanso
        if "calor" in estados:
            cambios["arbolado"] = "mucho"
        return replace(perfil, **cambios) if cambios else perfil

    @staticmethod
    def umbrales(perfil, estados, mes, hora):
        """Qué motivos cuentan para esta persona AHORA y con qué umbral (`perfil` = perfil efectivo)."""
        rojo, amarillo = CUESTA[perfil.cuestas]
        baja = 2.0 if "dolor_piernas" in estados else (1.0 if ("cansado" in estados or perfil.cansado) else 0.0)
        verano = 6 <= mes <= 9 and 12 <= hora <= 20
        return {"escaleras": bool(perfil.escaleras),
                "cuesta_rojo_pct": rojo - baja if rojo is not None else None,
                "cuesta_amarillo_pct": amarillo - baja,
                "banco_m": BANCO_M[perfil.descanso],
                "transporte_m": TRANSPORTE_M[perfil.transporte],
                "sombra": perfil.arbolado == "mucho",
                "calor": "calor" in estados or verano,
                "polen": bool(getattr(perfil, "sensible_polen", False))}

    def clasificar(self, perfil, contexto=None):
        """Tabla (índice = tramo): cada motivo (True/False), el nivel del tramo (barrera / incomodidad /
        None) y su categoría (no apta / regular / apta) para ESTA persona, lo que ha dicho, el mes y la hora."""
        ctx = contexto or {}
        estados = self.estados(perfil, ctx)
        mes, hora = int(ctx.get("mes", 10)), int(ctx.get("hora", 17))
        clave = (tuple(sorted(asdict(perfil).items())), tuple(sorted(estados)), mes, hora)
        if clave in self._cache_idon:
            return self._cache_idon[clave]

        u = self.umbrales(self.perfil_efectivo(perfil, estados), estados, mes, hora)
        c = self.calles
        pend, largo_ok = c["pendiente_pct"], c["length"] >= LARGO_MIN_CUESTA
        sin_arboles = self._arboles <= 0
        t = pd.DataFrame(index=c.index)
        t["escaleras"] = c["escalera"].astype(bool) & u["escaleras"]
        t["cuesta_fuerte"] = (largo_ok & (pend >= u["cuesta_rojo_pct"])) if u["cuesta_rojo_pct"] is not None else False
        t["cuesta"] = largo_ok & (pend >= u["cuesta_amarillo_pct"]) & ~t["cuesta_fuerte"]
        t["polen"] = (self._polen_serie(mes) >= POLEN_MIN_ARBOLES) if u["polen"] else False
        t["descanso"] = (self._dist_banco > u["banco_m"]) if u["banco_m"] else False
        t["transporte"] = (c["dist_transporte"] > u["transporte_m"]) if u["transporte_m"] else False
        if u["sombra"]:
            t["sombra"] = sin_arboles
        elif u["calor"]:                               # verano a mediodía: sin sombra Y sin fuente cerca
            t["sombra"] = sin_arboles & (self._dist_agua > SOMBRA_FUENTE_M)
        else:
            t["sombra"] = False
        rojo = t["escaleras"] | t["cuesta_fuerte"]
        amarillo = t[["cuesta", "polen", "descanso", "transporte", "sombra"]].any(axis=1)
        t["nivel"] = pd.Series(np.where(rojo, "barrera", np.where(amarillo, "incomodidad", None)), index=c.index)
        t["categoria"] = t["nivel"].map(lambda n: CATEGORIA[n if isinstance(n, str) else None])
        t.attrs.update({"umbrales": u, "mes": mes, "estados": sorted(estados)})
        if len(self._cache_idon) > 32:
            self._cache_idon.clear()
        self._cache_idon[clave] = t
        return t

    def _polen_serie(self, mes):
        n = {idx: self._polen_activo([idx], mes)[0] for idx in self.polen_tramo}
        return pd.Series(n, dtype=float).reindex(self.calles.index, fill_value=0.0)

    def motivos(self, idx, cl, mes=None):
        """Motivos de un tramo para esta persona, en palabras y con el dato: [{factor, nivel, texto}]."""
        mes = int(mes or cl.attrs.get("mes", 10))
        fila, f, u = cl.loc[idx], self.calles.loc[idx], cl.attrs["umbrales"]
        out = []
        for col, nivel in MOTIVOS:
            if not bool(fila[col]):
                continue
            if col in ("cuesta", "cuesta_fuerte"):
                texto = f"hay una cuesta del {float(f['pendiente_pct']):.0f} %"
            elif col == "escaleras":
                texto = "hay escaleras"
            elif col == "polen":
                n, detalle = self._polen_activo([idx], mes)
                tipo, (_, niv) = max(detalle.items(), key=lambda kv: kv[1][0])
                texto = (f"hay {n} árboles cuyo polen suele estar {niv} en {MESES[mes - 1]} "
                         f"(sobre todo {tipo.lower()})")
            elif col == "descanso":
                texto = f"el banco más cercano queda a unos {float(self._dist_banco[idx]):.0f} m"
            elif col == "transporte":
                texto = f"el transporte público queda a unos {float(f['dist_transporte']):.0f} m"
            else:
                texto = "no hay árboles que den sombra"
                if u["calor"] and float(self._dist_agua[idx]) > SOMBRA_FUENTE_M:
                    texto += f" y la fuente más cercana queda a unos {float(self._dist_agua[idx]):.0f} m"
            out.append({"factor": "cuesta" if col.startswith("cuesta") else col, "clave": col,
                        "nivel": nivel, "texto": texto})
        return out

    @staticmethod
    def _peor(cl, idxs):
        niv = cl.loc[list(idxs), "nivel"]
        return "barrera" if (niv == "barrera").any() else ("incomodidad" if (niv == "incomodidad").any() else None)

    def radar(self, p, cl, radio=RADAR_M):
        """Tramos a menos de `radio` m de la posición, con su nivel y distancia."""
        pos = self.calles.sindex.query(p.buffer(radio), predicate="intersects")
        idxs = self.calles.index[pos]
        d = self.calles.loc[idxs].geometry.distance(p)
        return pd.DataFrame({"nivel": cl.loc[idxs, "nivel"], "distancia": d}).sort_values("distancia")

    # ------------------------------------------------------------ dónde está y hacia dónde va
    def _a_metros(self, lat, lon):
        return gpd.GeoSeries([Point(lon, lat)], crs=4326).to_crs(CRS_M).iloc[0]

    def _a_latlon(self, xy):
        p = gpd.GeoSeries([Point(xy)], crs=CRS_M).to_crs(4326).iloc[0]
        return round(p.y, 6), round(p.x, 6)

    def _tramo_cercano(self, p):
        _, pos = self.calles.sindex.nearest(p, return_all=False)
        return self.calles.index[int(pos[0])]

    def _situar(self, p_ahora, p_antes):
        """Devuelve (idx_tramo, nodo_hacia, nodo_desde, metros_hasta_cruce, rumbo_llegada)."""
        idx = self._tramo_cercano(p_ahora)
        fila = self.calles.loc[idx]
        geom = fila.geometry
        u, v = int(fila["u"]), int(fila["v"])
        c = list(geom.coords)
        nodo_ini = u if Point(c[0]).distance(Point(self.xy[u])) <= Point(c[0]).distance(Point(self.xy[v])) else v
        nodo_fin = v if nodo_ini == u else u

        s_ahora = geom.project(p_ahora)
        s_antes = geom.project(p_antes) if p_antes is not None else None
        if s_antes is not None and abs(s_ahora - s_antes) >= 1:
            hacia_fin = s_ahora > s_antes
        elif p_antes is not None:                        # venía de otro tramo: va al extremo más lejano
            hacia_fin = Point(c[-1]).distance(p_antes) > Point(c[0]).distance(p_antes)
        else:
            hacia_fin = True
        if hacia_fin:
            hacia, desde, falta = nodo_fin, nodo_ini, geom.length - s_ahora
            a = geom.interpolate(max(geom.length - 5, 0)).coords[0]
            b = c[-1]
        else:
            hacia, desde, falta = nodo_ini, nodo_fin, s_ahora
            a = geom.interpolate(min(5, geom.length)).coords[0]
            b = c[0]
        return idx, hacia, desde, falta, _rumbo(a, b)

    def _ramas(self, idx_actual, nodo, rumbo_llegada):
        """Salidas del próximo cruce: [(idx, otro_nodo, giro_grados)] sin dar media vuelta."""
        out = []
        for idx, otro in self.vecinos.get(nodo, []):
            if idx == idx_actual:
                continue
            c = _coords_desde(self.calles.at[idx, "geometry"], self.xy[nodo])
            p2 = LineString(c).interpolate(min(10, LineString(c).length)).coords[0]
            dif = _giro(rumbo_llegada, _rumbo(c[0], p2))
            if abs(dif) <= 150:
                out.append((idx, otro, dif))
        return sorted(out, key=lambda r: abs(r[2]))

    def _seguir(self, idx, desde, presupuesto):
        """Camino más recto a partir de un tramo, hasta gastar `presupuesto` metros."""
        camino, nodo_previo, acumulado = [idx], desde, 0.0
        fila = self.calles.loc[idx]
        nodo = int(fila["v"]) if int(fila["u"]) == desde else int(fila["u"])
        acumulado += float(fila["length"])
        while acumulado < presupuesto:
            c = _coords_desde(self.calles.at[camino[-1], "geometry"], self.xy[nodo_previo])
            rumbo = _rumbo(LineString(c).interpolate(max(LineString(c).length - 10, 0)).coords[0], c[-1])
            ramas = self._ramas(camino[-1], nodo, rumbo)
            if not ramas or abs(ramas[0][2]) >= 35:            # se acaba lo "recto"
                break
            sig, otro, _ = ramas[0]
            camino.append(sig)
            acumulado += float(self.calles.at[sig, "length"])
            nodo_previo, nodo = nodo, otro
        return camino

    # ------------------------------------------------------------ paso del acompañamiento
    def paso(self, lat, lon, perfil, contexto=None, lat_antes=None, lon_antes=None,
             estado=None, urgencia=False):
        """Un paso del paseo. `estado` es lo que la persona acaba de decir ya interpretado
        («cansado», «calor», «mareo», «dolor_piernas» o «mejor»); `urgencia` = síntomas graves."""
        ctx = dict(contexto or {})
        ctx["estados"] = list(ctx.get("estados", []))
        nuevo = None
        if estado == "mejor":
            nuevo = "mejor" if ctx["estados"] else None
            ctx["estados"] = []
        elif estado in ESTADOS:
            if estado not in ctx["estados"]:
                ctx["estados"].append(estado)
            nuevo = estado                       # se responde aunque lo repita: lo ha dicho ahora
        estados = self.estados(perfil, ctx)
        perfil_base, perfil = perfil, self.perfil_efectivo(perfil, estados)
        ctx.setdefault("metros_andados", 0.0)
        ctx.setdefault("metros_sin_banco", 0.0)
        ctx.setdefault("metros_sin_fuente", 0.0)
        ctx.setdefault("avisos_dados", {})              # tipo -> metros_andados cuando se dio
        ctx.setdefault("ultimo_aviso_m", -10 ** 9)
        ctx.setdefault("tramos_avisados", [])           # para no repetir la misma cuesta
        ctx.setdefault("mes", 10)
        ctx.setdefault("hora", 17)
        if "casa" not in ctx:
            ctx["casa"] = {"lat": lat, "lon": lon}

        p = self._a_metros(lat, lon)
        p_antes = self._a_metros(lat_antes, lon_antes) if lat_antes is not None else None
        paso_m = min(p.distance(p_antes), 200) if p_antes is not None else 0.0

        idx, hacia, desde, falta, rumbo = self._situar(p, p_antes)
        fila = self.calles.loc[idx]
        ctx["metros_andados"] += paso_m
        ctx["metros_sin_banco"] = 0.0 if fila["bancos"] > 0 else ctx["metros_sin_banco"] + paso_m
        sin_fuente = fila.get("dist_water_m")
        ctx["metros_sin_fuente"] = 0.0 if (sin_fuente is not None and sin_fuente < 30) else ctx["metros_sin_fuente"] + paso_m

        ramas = self._ramas(idx, hacia, rumbo)
        presupuesto = max(HORIZONTE_M - falta, 0)
        previsto = [idx] + (self._seguir(ramas[0][0], hacia, presupuesto) if ramas and abs(ramas[0][2]) < 35 and presupuesto > 0 else [])

        # Modo destino (volver, ir al banco más cercano...): la ruta más corta se RECALCULA en cada paso
        # desde donde está ahora; lo que se mira «por delante» es la ruta, no la calle más recta.
        nav, self._paso.nav, dar_vuelta = None, None, False
        if ctx.get("destino"):
            nav = self._navegar(p, idx, hacia, desde, falta, rumbo if p_antes is not None else None, ctx["destino"])
            if nav is None:                              # sin camino posible: se sigue como paseo libre
                ctx.pop("destino", None)
            else:
                interno = nav.pop("_interno", None)      # datos internos: no salen en la respuesta
            if nav is not None and not nav["llegado"] and interno:
                self._paso.nav = interno
                dar_vuelta = interno["dar_vuelta"]
                previsto = [idx] + (self._recortar(interno["camino"], presupuesto) if not dar_vuelta else [])

        cl = self.clasificar(perfil_base, ctx)
        # Estado de ESTE paso para las reglas (por hilo: FastAPI atiende peticiones en paralelo)
        self._paso.cl, self._paso.p = cl, p
        self._paso.rumbo = rumbo if p_antes is not None else None   # sin paso anterior no hay sentido
        zona = self._zona(p, idx, previsto, cl, ctx)

        # Sin paso anterior no se sabe hacia dónde va (y si tiene que dar la vuelta, lo de «delante»
        # queda detrás): no se avisa de lo que hay «por delante»
        sin_sentido = p_antes is None or dar_vuelta
        direccionales = {self._r_por_delante}

        candidatos = []
        if nav and nav["llegado"]:
            candidatos.append(self._r_llegada(perfil, ctx["destino"]))
            ctx.pop("destino", None)
        if urgencia:
            candidatos.append(self._r_urgencia(perfil, ctx, p, self._paso.rumbo))
        elif nuevo:
            candidatos.append(self._reaccion(nuevo, perfil, ctx, p, self._paso.rumbo, zona))
        for regla in (self._r_por_delante, self._r_cansancio, self._r_descanso,
                      self._r_agua, self._r_centro_salud, self._r_inicio):
            if sin_sentido and regla in direccionales:
                continue
            a = regla(perfil, ctx, idx, hacia, falta, ramas, previsto, presupuesto)
            if a:
                candidatos.append(a)

        # Filtro anti-pesadez: separación global y tiempo de espera por tipo
        validos = []
        for a in candidatos:
            prioridad, espera = REGLAS[a["tipo"]]
            inmediato = a["tipo"] in ("reaccion", "alerta_salud", "llegada")
            ultimo = ctx["avisos_dados"].get(a["tipo"], -10 ** 9)
            if not inmediato and ctx["metros_andados"] - ultimo < espera:
                continue
            if (not inmediato and not (a["tipo"] == "barrera" and a.get("factor") == "escaleras")
                    and ctx["metros_andados"] - ctx["ultimo_aviso_m"] < SEPARACION_MIN_M):
                continue
            a["prioridad"] = prioridad
            validos.append(a)
        validos.sort(key=lambda a: -a["prioridad"])
        aviso = validos[0] if validos else None
        if aviso:
            ctx["avisos_dados"][aviso["tipo"]] = ctx["metros_andados"]
            if aviso["tipo"] == "reaccion":                # lo que ya cubre la respuesta no se repite
                for t in REACCION_CUBRE.get(aviso.get("estado"), ()):
                    ctx["avisos_dados"][t] = ctx["metros_andados"]
            for fac in (aviso.get("motivo") or {}).get("factores", []):
                ctx["avisos_dados"]["motivo:" + fac["clave"]] = ctx["metros_andados"]
            ctx["ultimo_aviso_m"] = ctx["metros_andados"]
            if aviso.get("tramos"):
                ctx["tramos_avisados"] = (ctx["tramos_avisados"] + aviso["tramos"])[-40:]

        ctx["metros_andados"] = round(ctx["metros_andados"], 1)
        ctx["metros_sin_banco"] = round(ctx["metros_sin_banco"], 1)
        ctx["metros_sin_fuente"] = round(ctx["metros_sin_fuente"], 1)
        return {
            "posicion": {"lat": lat, "lon": lon, "calle": _calle(fila.get("nombre")), "tramo_id": int(idx),
                         "metros_hasta_cruce": round(falta)},
            "aviso": aviso,
            "tambien_se_cumplian": [a["tipo"] for a in validos[1:]],
            "zona": zona,
            "destino": nav,                     # None en paseo libre
            "contexto": ctx,
        }

    # ------------------------------------------------------------ lo que hay alrededor
    def _zona(self, p, idx, previsto, cl, ctx):
        """Cómo es para esta persona su calle, lo que viene por delante y los tramos rojos cerca."""
        mes = int(ctx.get("mes", 10))
        r = self.radar(p, cl)
        rojos = []
        for i, fila in r[r["nivel"] == "barrera"].head(5).iterrows():
            rojos.append({"tramo_id": int(i), "calle": _calle(self.calles.at[i, "nombre"]),
                          "distancia_m": round(float(fila["distancia"])),
                          "motivos": [m["texto"] for m in self.motivos(i, cl, mes)]})
        return {"categoria_aqui": cl.at[idx, "categoria"],
                "motivos_aqui": [m["texto"] for m in self.motivos(idx, cl, mes)],
                "categoria_delante": CATEGORIA[self._peor(cl, previsto[1:])] if len(previsto) > 1 else None,
                "radio_m": RADAR_M, "no_aptas_cerca": rojos,
                "estados": sorted(self.estados(None, ctx))}

    def _orientacion(self, p, xy, rumbo):
        """«delante», «a tu derecha»... de un punto respecto al sentido de la marcha."""
        if p.distance(Point(xy)) < 8:
            return "aquí mismo"
        if rumbo is None:
            return None
        dif = _giro(rumbo, _rumbo((p.x, p.y), xy))
        if abs(dif) < 40:
            return "delante"
        if abs(dif) > 140:
            return "detrás"
        return "a tu derecha" if dif > 0 else "a tu izquierda"

    @staticmethod
    def _fuentes_de(motivos):
        mapa = {"escaleras": ["escaleras"], "cuesta": ["pendiente"], "descanso": ["bancos"],
                "transporte": ["transporte"], "sombra": ["arbolado", "fuentes"], "polen": ["arbolado", "polen"]}
        out = []
        for m in motivos:
            for k in mapa[m["factor"]]:
                if FUENTES_DATOS[k] not in out:
                    out.append(FUENTES_DATOS[k])
        return out

    # ------------------------------------------------------------ lo que la persona dice
    def _recurso_orientado(self, p, rumbo, tipo):
        lat, lon = self._a_latlon((p.x, p.y))
        try:
            r = self.m.mas_cercano(lat, lon, tipo)
        except Exception:                                  # noqa: BLE001 - sin datos de ese tipo
            return None
        if r.get("lat") is not None:
            xy = self._a_metros(r["lat"], r["lon"])
            r["orientacion"] = self._orientacion(p, (xy.x, xy.y), rumbo)
        return r

    @staticmethod
    def _donde(r):
        nombre = f" ({r['nombre']})" if r.get("nombre") and r.get("tipo") == "banco" else ""
        ori = f", {r['orientacion']}" if r.get("orientacion") and r["orientacion"] != "aquí mismo" else ""
        return f"a unos {r['distancia_m']} m{ori}{nombre}"

    def _reaccion(self, estado, perfil, ctx, p, rumbo, zona):
        banco = self._recurso_orientado(p, rumbo, "banco")
        trans = self._recurso_orientado(p, rumbo, "transporte")
        sug, fuentes, partes = {}, [], []
        if estado == "mejor":
            partes = ["Me alegro. Seguimos; sigo pendiente del camino."]
        elif estado == "cansado":
            if banco:
                partes.append(f"Para un momento si lo necesitas: hay un banco {self._donde(banco)}.")
            if trans:
                partes.append(f"Si prefieres no seguir andando, tienes {trans['tipo']} {trans['nombre']} a unos {trans['distancia_m']} m.")
            partes.append("A partir de ahora te aviso antes de las cuestas y te indico bancos más a menudo.")
            sug = {"banco": banco, "transporte": trans}
            fuentes = [FUENTES_DATOS["bancos"], FUENTES_DATOS["transporte"]]
        elif estado == "calor":
            fuente = self._recurso_orientado(p, rumbo, "fuente")
            if fuente:
                partes.append(f"Tienes una fuente de agua {self._donde(fuente)}.")
            if banco:
                partes.append(f"Si quieres parar, hay un banco {self._donde(banco)}.")
            partes.append("Desde ahora te propondré calles con más árboles y te avisaré si nos alejamos de las fuentes.")
            sug = {"fuente": fuente, "banco": banco}
            fuentes = [FUENTES_DATOS["fuentes"], FUENTES_DATOS["bancos"], FUENTES_DATOS["arbolado"]]
        elif estado == "mareo":
            if banco:
                partes.append(f"Siéntate en cuanto puedas: el banco más cercano está {self._donde(banco)}.")
            else:
                partes.append("Para y apóyate o siéntate en cuanto puedas.")
            partes.append("Si el mareo no se pasa en unos minutos o va a más, llama al 112.")
            if trans:
                partes.append(f"Cuando te encuentres mejor, si prefieres volver, {trans['tipo']} {trans['nombre']} está a unos {trans['distancia_m']} m.")
            far = self._recurso_orientado(p, rumbo, "farmacia")
            cs = self._recurso_orientado(p, rumbo, "centro_salud")
            if cs:
                partes.append(f"El centro de salud más cercano es el {cs['nombre']}, {self._donde(cs)}.")
            sug = {"banco": banco, "transporte": trans, "farmacia": far, "centro_salud": cs,
                   "telefono_emergencias": "112"}
            fuentes = [FUENTES_DATOS["bancos"], FUENTES_DATOS["transporte"], FUENTES_DATOS["farmacias"],
                       FUENTES_DATOS["centros_salud"]]
        elif estado == "dolor_piernas":
            if banco:
                partes.append(f"Hay un banco {self._donde(banco)} para descansar las piernas.")
            partes.append("Desde ahora evitaré cuestas y te avisaré antes de cualquier subida.")
            if trans:
                partes.append(f"Y si prefieres volver sin andar mucho, {trans['tipo']} {trans['nombre']} está a unos {trans['distancia_m']} m.")
            sug = {"banco": banco, "transporte": trans}
            fuentes = [FUENTES_DATOS["bancos"], FUENTES_DATOS["transporte"]]
        return {"tipo": "reaccion", "estado": estado, "mensaje": self._saludo(perfil, " ".join(partes)),
                "sugerencia": sug or None,
                "motivo": {"estado_dicho": estado, "estados_activos": ctx["estados"],
                           "categoria_aqui_ahora": zona["categoria_aqui"]},
                "fuentes": fuentes}

    def _r_urgencia(self, perfil, ctx, p, rumbo):
        far = self._recurso_orientado(p, rumbo, "farmacia")
        banco = self._recurso_orientado(p, rumbo, "banco")
        cs = self._recurso_orientado(p, rumbo, "centro_salud")
        texto = "Si te encuentras muy mal, llama ahora al 112. Si puedes, siéntate o apóyate"
        texto += f" (hay un banco {self._donde(banco)})." if banco else "."
        if cs:
            texto += f" El centro de salud más cercano es el {cs['nombre']}, {self._donde(cs)}."
        lat, lon = self._a_latlon((p.x, p.y))
        return {"tipo": "alerta_salud", "mensaje": self._saludo(perfil, texto),
                "sugerencia": {"telefono_emergencias": "112", "banco": banco, "farmacia": far,
                               "centro_salud": cs, "tu_posicion": {"lat": lat, "lon": lon}},
                "motivo": {"sintomas_graves": True},
                "fuentes": [FUENTES_DATOS["bancos"], FUENTES_DATOS["farmacias"], FUENTES_DATOS["centros_salud"]]}

    # ------------------------------------------------------------ mapa personal
    def mapa_personal(self, perfil, contexto=None):
        """GeoJSON de los tramos con su categoría PARA ESTA PERSONA (apta / regular / no apta) y sus
        motivos. Sale de los mismos motivos y umbrales que los avisos: el mapa y el acompañante coinciden."""
        ctx = contexto or {}
        cl = self.clasificar(perfil, ctx)
        mes = int(ctx.get("mes", 10))
        feats = []
        cuenta = {"apta": 0, "regular": 0, "no apta": 0}
        for idx, geom in self._calles_4326.items():
            cat = cl.at[idx, "categoria"]
            cuenta[cat] += 1
            props = {"tramo_id": int(idx), "calle": _calle(self.calles.at[idx, "nombre"]), "categoria": cat}
            if cat != "apta":
                props["motivos"] = [m["texto"] for m in self.motivos(idx, cl, mes)]
            feats.append({"type": "Feature", "properties": props,
                          "geometry": {"type": "LineString",
                                       "coordinates": [[round(x, 6), round(y, 6)] for x, y in geom.coords]}})
        return {"type": "FeatureCollection", "features": feats,
                "resumen": {**cuenta, "estados": cl.attrs["estados"], "umbrales": cl.attrs["umbrales"]}}

    # ------------------------------------------------------------ reglas
    @staticmethod
    def _nombre(perfil):
        return perfil.nombre if perfil.nombre and perfil.nombre != "Usuario" else ""

    def _saludo(self, perfil, texto):
        n = self._nombre(perfil)
        return f"{n}, {texto[0].lower()}{texto[1:]}" if n else texto

    def _mejor_salida(self, ramas, hacia, presupuesto, cl, nivel_actual):
        """Salida del próximo cruce con lo que viene después MEJOR que lo de ahora: verde antes que amarillo,
        nunca roja. Paseo libre: el camino más recto por esa salida. Modo destino: la ruta por esa salida
        hasta el destino, solo si no alarga demasiado, y diciendo cuánto alarga."""
        orden = {None: 0, "incomodidad": 1, "barrera": 2}
        nav = getattr(self._paso, "nav", None)
        if nav:
            principal = nav["camino"][0] if nav["camino"] else None
        else:
            principal = ramas[0][0] if ramas and abs(ramas[0][2]) < 35 else None
        mejor = None
        for idx, otro, dif in ramas:
            if idx == principal:
                continue
            extra = None
            if nav:
                extra = self._extra(idx, otro, hacia)
                # Para evitar algo rojo puede merecer la pena un rodeo; para algo amarillo, solo uno pequeño
                if extra is None or (nivel_actual == "incomodidad" and extra > 60):
                    continue
                camino = [idx] + self._recortar(self._camino_hasta(otro, nav["dnodo"], nav["dist"]), max(presupuesto, 60))
            else:
                camino = self._seguir(idx, hacia, max(presupuesto, 60))
            nivel = self._peor(cl, camino)
            if nivel == "barrera" or orden[nivel] >= orden[nivel_actual]:
                continue
            clave = (orden[nivel], extra or 0, abs(dif))
            if mejor is None or clave < mejor[0]:
                mejor = (clave, {"direccion": _palabra_giro(dif), "calle": _calle(self.calles.at[idx, "nombre"]),
                                 "tramo_id": int(idx), "coordenadas": [self._a_latlon(self.xy[otro])],
                                 "categoria": CATEGORIA[nivel], "extra_m": extra})
        return mejor[1] if mejor else None

    def _motivos_nuevos(self, i, cl, ctx):
        """Motivos de un tramo de los que aún no se ha avisado: nunca dos veces del mismo tramo, y de cada
        motivo no antes de ESPERA_MOTIVO metros desde el último aviso (una cuesta larga es un solo aviso)."""
        if int(i) in ctx["tramos_avisados"]:
            return []
        out = []
        for m in self.motivos(i, cl, int(ctx["mes"])):
            ultimo = ctx["avisos_dados"].get("motivo:" + m["clave"], -10 ** 9)
            if m["clave"] == "cuesta":                   # tras avisar de una cuesta fuerte, una suave no es noticia
                ultimo = max(ultimo, ctx["avisos_dados"].get("motivo:cuesta_fuerte", -10 ** 9))
            if ctx["metros_andados"] - ultimo >= ESPERA_MOTIVO[m["clave"]]:
                out.append(m)
        return out

    def _r_por_delante(self, perfil, ctx, idx, hacia, falta, ramas, previsto, presupuesto):
        """Lo que viene por delante (por la calle, o por la ruta si va a un destino) que para ESTA persona
        es una barrera (rojo: tono de alerta) o una incomodidad (amarillo: tono tranquilo), con una
        alternativa mejor si la hay. Sustituye a los antiguos avisos de escaleras, cuesta, polen y zonas."""
        cl = self._paso.cl
        # Ya dentro de una barrera (p. ej. una cuesta fuerte): ánimo y cuánto queda
        aqui = [m for m in self._motivos_nuevos(idx, cl, ctx) if m["nivel"] == "barrera"]
        if aqui and falta >= 40:
            texto = (f"Estás en una calle donde {_lista([m['texto'] for m in aqui])}. "
                     f"Quedan unos {falta:.0f} m hasta el cruce; ve con calma.")
            return self._aviso_tramo("barrera", perfil, texto, aqui, [idx], 0, None)

        objetivo, dist = None, falta
        for j, i in enumerate(previsto[1:], start=1):
            if j > 1:
                dist += float(self.calles.at[previsto[j - 1], "length"])
            mot = self._motivos_nuevos(i, cl, ctx)
            if not mot:
                continue
            nivel = "barrera" if any(m["nivel"] == "barrera" for m in mot) else "incomodidad"
            if objetivo is None or (nivel == "barrera" and objetivo[1] != "barrera"):
                objetivo = (j, nivel, i, dist, mot)
            if nivel == "barrera":
                break
        if objetivo is None:
            return None
        j, nivel, i, d, mot = objetivo
        if nivel == "barrera":
            mot = [m for m in mot if m["nivel"] == "barrera"]
        factores = {m["factor"] for m in mot}
        tramos = [i]                                     # los tramos seguidos con lo mismo, de una vez
        for k in previsto[j + 1:]:
            if factores & {m["factor"] for m in self.motivos(k, cl, int(ctx["mes"]))}:
                tramos.append(k)
            else:
                break
        alt = self._mejor_salida(ramas, hacia, presupuesto, cl, nivel)
        calle = _calle(self.calles.at[i, "nombre"])
        en = f", en {calle}," if calle else ""
        que = _lista([m["texto"] for m in mot[:2]])
        cuando = "justo aquí delante" if d < 25 else f"en unos {d:.0f} m"
        if nivel == "barrera":
            texto = f"Atención: {cuando}{en} {que}."
            if alt:
                texto += f" Mejor sigue {alt['direccion']}{_por(alt['calle'])}{self._cuanto_mas(alt)}."
            else:
                texto += " Ve con calma." if "cuesta" in factores else " Si puedes, busca otro camino."
        else:
            texto = f"Más adelante{en} {que}."
            if alt:
                texto += f" Si prefieres, {alt['direccion']}{_por(alt['calle'])} está mejor{self._cuanto_mas(alt)}."
        return self._aviso_tramo(nivel, perfil, texto, mot, tramos, d, alt)

    def _aviso_tramo(self, nivel, perfil, texto, mot, tramos, metros, alt):
        return {"tipo": nivel, "nivel": nivel, "factor": mot[0]["factor"], "tramo_id": int(tramos[0]),
                "tramos": [int(t) for t in tramos], "mensaje": self._saludo(perfil, texto), "sugerencia": alt,
                "motivo": {"factores": mot, "metros_hasta": round(metros),
                           "umbrales": self._paso.cl.attrs["umbrales"]},
                "fuentes": self._fuentes_de(mot)}

    def _distancia_comoda(self, perfil):
        if "mucho" in (perfil.cuestas, perfil.descanso):
            return 1200
        if "algo" in (perfil.cuestas, perfil.descanso):
            return 1800
        return 2500

    def _r_cansancio(self, perfil, ctx, idx, hacia, falta, ramas, previsto, presupuesto):
        limite = self._distancia_comoda(perfil)
        if not (perfil.cansado or ctx["metros_andados"] >= limite):
            return None
        lat, lon = self._a_latlon(self._punto_actual(idx, hacia, falta))
        t = self.m.mas_cercano(lat, lon, "transporte")
        if perfil.cansado:
            texto = "Vamos con calma."
        else:
            texto = f"Llevas ya unos {ctx['metros_andados'] / 1000:.1f} km andados."
        texto += (f" Tienes la parada de {t['tipo']} {t['nombre']} a unos {t['distancia_m']} m"
                  " por si prefieres volver en transporte.")
        if (ctx.get("destino") or {}).get("tipo") != "casa":
            texto += " Si quieres que te lleve de vuelta, dime «quiero volver»."
        return {"tipo": "cansancio", "mensaje": self._saludo(perfil, texto),
                "sugerencia": {"recurso": t},
                "motivo": {"metros_andados": round(ctx["metros_andados"]), "distancia_comoda_perfil": limite,
                           "cansado": perfil.cansado},
                "fuentes": [FUENTES_DATOS["transporte"]]}

    def _r_descanso(self, perfil, ctx, idx, hacia, falta, ramas, previsto, presupuesto):
        umbral = {"mucho": 150, "algo": 300}.get(perfil.descanso, 400 if perfil.cansado else None)
        if umbral is None:
            return None
        if perfil.cansado:
            umbral *= 0.6
        if ctx["metros_sin_banco"] < umbral:
            return None
        lat, lon = self._a_latlon(self._punto_actual(idx, hacia, falta))
        b = self.m.mas_cercano(lat, lon, "banco")
        if b["distancia_m"] > 150:
            return None
        texto = f"Hay un banco a unos {b['distancia_m']} m" + (f" ({b['nombre']})" if b.get("nombre") else "") + " por si quieres descansar."
        return {"tipo": "descanso", "mensaje": self._saludo(perfil, texto), "sugerencia": {"recurso": b},
                "motivo": {"metros_sin_banco": round(ctx["metros_sin_banco"]), "umbral_perfil_m": round(umbral)},
                "fuentes": [FUENTES_DATOS["bancos"]]}

    def _r_agua(self, perfil, ctx, idx, hacia, falta, ramas, previsto, presupuesto):
        calor_dicho = "calor" in ctx.get("estados", [])
        if not calor_dicho and not (6 <= int(ctx["mes"]) <= 9 and 12 <= int(ctx["hora"]) <= 20):
            return None
        if ctx["metros_sin_fuente"] < (300 if calor_dicho else 600):
            return None
        lat, lon = self._a_latlon(self._punto_actual(idx, hacia, falta))
        f = self.m.mas_cercano(lat, lon, "fuente")
        if f["distancia_m"] > 200:
            return None
        texto = ("Como tenías calor: " if calor_dicho else "En esta época, a estas horas suele hacer calor: ") + \
            f"tienes una fuente de agua a unos {f['distancia_m']} m."
        return {"tipo": "agua", "mensaje": self._saludo(perfil, texto), "sugerencia": {"recurso": f},
                "motivo": {"mes": ctx["mes"], "hora": ctx["hora"], "metros_sin_fuente": round(ctx["metros_sin_fuente"])},
                "fuentes": [FUENTES_DATOS["fuentes"]]}

    def _r_centro_salud(self, perfil, ctx, idx, hacia, falta, ramas, previsto, presupuesto):
        """Aviso informativo al pasar a menos de 80 m de un centro de salud (Comunidad de Madrid)."""
        if ctx["metros_andados"] < 30:
            return None
        lat, lon = self._a_latlon(self._punto_actual(idx, hacia, falta))
        try:
            c = self.m.mas_cercano(lat, lon, "centro_salud")
        except (ValueError, AttributeError):
            return None
        if c["distancia_m"] > 80:
            return None
        donde = f" ({c['direccion']})" if c.get("direccion") else ""
        texto = f"Pasas junto al {c['nombre']}{donde}, por si lo necesitas."
        return {"tipo": "centro_salud", "mensaje": self._saludo(perfil, texto), "sugerencia": {"recurso": c},
                "motivo": {"distancia_m": c["distancia_m"]}, "fuentes": [FUENTES_DATOS["centros_salud"]]}

    def _r_inicio(self, perfil, ctx, idx, hacia, falta, ramas, previsto, presupuesto):
        if ctx["metros_andados"] > 30:
            return None
        mes, hora = int(ctx["mes"]), int(ctx["hora"])
        partes = ["Empezamos el paseo. Estaré pendiente del camino y te avisaré solo si hace falta."]
        motivo = {"mes": mes, "hora": hora}
        fuentes = []
        if (mes, hora) in self.aire:
            cat, pct = self.aire[(mes, hora)]
            motivo.update({"aire_categoria_habitual": cat, "pct_horas_regular_o_peor": pct})
            fuentes.append(FUENTES_DATOS["aire"])
            if pct >= 20:
                partes.append(f"A esta hora, en {MESES[mes - 1]}, el aire en Chamberí está regular o peor "
                              f"en un {pct:.0f} % de los días registrados.")
            else:
                partes.append(f"A esta hora, en {MESES[mes - 1]}, la calidad del aire en Chamberí suele estar "
                              f"en la categoría «{cat}» del índice oficial (ICA).")
        if getattr(perfil, "sensible_polen", False):
            altos = sorted(t for (t, m), (niv, _) in self.polen_mes.items() if m == mes and niv == "alto")
            medios = sorted(t for (t, m), (niv, _) in self.polen_mes.items() if m == mes and niv == "medio")
            if altos:
                partes.append(f"En {MESES[mes - 1]} suele haber polen alto de: {', '.join(a.lower() for a in altos)}.")
            elif medios:
                partes.append(f"En {MESES[mes - 1]} el polen suele estar en nivel medio: {', '.join(a.lower() for a in medios)}.")
            elif any(m == mes for (_, m) in self.polen_mes):
                partes.append(f"En {MESES[mes - 1]} el polen suele estar bajo: no espero avisos de polen en este paseo.")
            if altos or medios or any(m == mes for (_, m) in self.polen_mes):
                fuentes.append(FUENTES_DATOS["polen"])
                motivo["polen_mes"] = "alto" if altos else ("medio" if medios else "bajo")
        return {"tipo": "inicio", "mensaje": self._saludo(perfil, " ".join(partes)), "sugerencia": None,
                "motivo": motivo, "fuentes": fuentes}

    def _punto_actual(self, idx, hacia, falta):
        """Punto métrico aproximado de la persona (sobre el tramo)."""
        geom = self.calles.at[idx, "geometry"]
        c = _coords_desde(geom, self.xy[hacia])
        return LineString(c).interpolate(falta).coords[0]

    # ------------------------------------------------------------ ir a un sitio (modo destino)
    def ir_a(self, lat, lon, perfil, contexto, destino):
        """Empieza a guiar hacia `destino` ({lat, lon, nombre, tipo}): ruta MÁS CORTA andando, descrita
        para esta persona. Devuelve el contexto con el destino: los pasos siguientes la recalculan."""
        ctx = dict(contexto or {})
        perfil = self.perfil_efectivo(perfil, self.estados(perfil, ctx))
        resultado = self.m.ruta((lat, lon), (destino["lat"], destino["lon"]), perfil)
        ctx["destino"] = {"lat": float(destino["lat"]), "lon": float(destino["lon"]),
                          "nombre": destino.get("nombre"), "tipo": destino.get("tipo")}
        ctx.setdefault("casa", {"lat": lat, "lon": lon})
        resultado["contexto"] = ctx
        return resultado

    def volver(self, lat, lon, perfil, contexto=None):
        """«Quiero volver»: modo destino hacia el punto de inicio del paseo. Si queda lejos (>1,5 km)
        o la persona está cansada, ofrece también el transporte más cercano."""
        ctx = contexto or {}
        casa = ctx.get("casa") or {"lat": lat, "lon": lon}
        resultado = self.ir_a(lat, lon, perfil, ctx, {"lat": casa["lat"], "lon": casa["lon"],
                                                      "nombre": "el punto de inicio", "tipo": "casa"})
        perfil = self.perfil_efectivo(perfil, self.estados(perfil, ctx))
        t = self.m.mas_cercano(lat, lon, "transporte")
        resultado["alternativa_transporte"] = t
        if resultado["ruta"]["longitud_m"] > 1500 or perfil.cansado:
            resultado["explicacion"] += (f" Si prefieres no andar tanto, la parada de {t['tipo']} "
                                         f"{t['nombre']} está a {t['distancia_m']} m.")
        return resultado

    # ------------------------------------------------------------ navegación hacia el destino
    def _otro(self, idx, nodo):
        u, v = int(self.calles.at[idx, "u"]), int(self.calles.at[idx, "v"])
        return v if u == int(nodo) else u

    def _camino_hasta(self, nodo, dnodo, dist):
        """Tramos del camino más corto de `nodo` a `dnodo` usando la tabla de distancias al destino."""
        camino, n = [], nodo
        for _ in range(3000):
            if n == dnodo:
                break
            opciones = self.vecinos.get(n, [])
            if not opciones:
                break
            idx, otro = min(opciones, key=lambda t: float(self.calles.at[t[0], "length"]) + dist.get(t[1], math.inf))
            if math.isinf(dist.get(otro, math.inf)):
                break
            camino.append(idx)
            n = otro
        return camino

    def _recortar(self, camino, metros):
        """Primeros tramos de un camino hasta cubrir `metros`."""
        out, acum = [], 0.0
        for i in camino:
            if acum >= metros:
                break
            out.append(i)
            acum += float(self.calles.at[i, "length"])
        return out

    def _extra(self, idx, otro, hacia):
        """Metros que añade salir del cruce `hacia` por `idx` en vez de por la ruta. None si no compensa."""
        nav = getattr(self._paso, "nav", None)
        if not nav:
            return None
        base = nav["dist"].get(int(hacia), math.inf)
        extra = float(self.calles.at[idx, "length"]) + nav["dist"].get(int(otro), math.inf) - base
        if math.isinf(extra) or extra > max(150.0, 0.5 * nav["restante"]):
            return None
        return int(round(max(extra, 0.0)))

    @staticmethod
    def _cuanto_mas(alt):
        if not alt or alt.get("extra_m") is None:
            return ""
        return " (misma distancia)" if alt["extra_m"] < 15 else f" (son unos {alt['extra_m']} m más)"

    def _navegar(self, p, idx, hacia, desde, falta, rumbo, dest):
        """Ruta más corta desde la posición ACTUAL al destino: metros que faltan, línea para el mapa,
        próxima indicación y si ha llegado. Recalcular en cada paso hace que, si se desvía, la ruta
        se adapte sola."""
        try:
            dnodo = self.m._nodo_cercano(dest["lat"], dest["lon"])
        except ValueError:
            return None
        dist = self.m.distancias_a(dnodo)
        geom = self.calles.at[idx, "geometry"]
        largo = float(geom.length)
        p_dest = self._a_metros(dest["lat"], dest["lon"])
        acceso = Point(self.xy[dnodo]).distance(p_dest)
        via_h = falta + dist.get(int(hacia), math.inf)
        via_d = (largo - falta) + dist.get(int(desde), math.inf)
        if math.isinf(min(via_h, via_d)):
            return None
        directo = p.distance(p_dest)
        nombre = dest.get("nombre") or "el destino"
        if directo <= 25:
            return {"nombre": nombre, "tipo": dest.get("tipo"), "llegado": True, "metros_restantes": 0,
                    "minutos": 0, "coordenadas": [], "indicacion": None}
        dar_vuelta = via_d < via_h
        nodo = desde if dar_vuelta else hacia
        camino = self._camino_hasta(nodo, dnodo, dist)
        restante = min(via_h, via_d) + acceso

        # Línea que queda: de la persona al cruce, la ruta y el último trozo hasta el destino
        s_p = geom.project(p)
        c = list(geom.coords)
        nodo_al_final = Point(c[-1]).distance(Point(self.xy[nodo])) <= Point(c[0]).distance(Point(self.xy[nodo]))
        tramo = substring(geom, s_p, largo) if nodo_al_final else substring(geom, 0, s_p)
        pts = list(tramo.coords) if nodo_al_final else list(tramo.coords)[::-1]
        if len(pts) < 2:
            pts = [(p.x, p.y), self.xy[nodo]]
        n = nodo
        for t in camino:
            pts += _coords_desde(self.calles.at[t, "geometry"], self.xy[n])[1:]
            n = self._otro(t, n)
        pts.append((p_dest.x, p_dest.y))
        linea = gpd.GeoSeries([LineString(pts)], crs=CRS_M).to_crs(4326).iloc[0]
        coords = [[round(y, 6), round(x, 6)] for x, y in linea.coords]

        return {"nombre": nombre, "tipo": dest.get("tipo"), "llegado": directo <= 25 or restante <= 25,
                "metros_restantes": int(round(restante)), "minutos": max(1, round(restante / 50)),
                "coordenadas": [coords],
                "indicacion": self._indicacion(camino, nodo, falta if not dar_vuelta else largo - falta,
                                               rumbo, dar_vuelta, restante, nombre),
                "_interno": {"camino": camino, "dist": dist, "dnodo": dnodo, "restante": restante,
                             "dar_vuelta": dar_vuelta}}

    def _indicacion(self, camino, nodo, hasta_cruce, rumbo, dar_vuelta, restante, nombre):
        """Próxima indicación, como un navegador: el primer giro de verdad de la ruta."""
        if dar_vuelta:
            return "Da la vuelta: el camino sigue en la otra dirección."
        if rumbo is None or not camino:
            calle = _calle(self.calles.at[camino[0], "nombre"]) if camino else None
            return f"Ve{_por(calle) or ' hacia ' + nombre}." if calle else f"Sigue hasta {nombre}, a unos {restante:.0f} m."
        acum, n, r_in = hasta_cruce, nodo, rumbo
        for t in camino:
            c = _coords_desde(self.calles.at[t, "geometry"], self.xy[n])
            linea = LineString(c)
            dif = _giro(r_in, _rumbo(c[0], linea.interpolate(min(10, linea.length)).coords[0]))
            if abs(dif) >= 35:
                calle = _calle(self.calles.at[t, "nombre"])
                if acum < 15:
                    return f"Ahora, gira {_palabra_giro(dif)}{_por(calle)}."
                return f"En unos {acum:.0f} m, gira {_palabra_giro(dif)}{_por(calle)}."
            acum += float(linea.length)
            r_in = _rumbo(linea.interpolate(max(linea.length - 10, 0)).coords[0], c[-1])
            n = self._otro(t, n)
        return f"Sigue recto unos {restante:.0f} m hasta {nombre}."

    def _r_llegada(self, perfil, dest):
        if dest.get("tipo") == "casa":
            texto = "Ya estás de vuelta en el punto donde empezaste el paseo."
        else:
            texto = f"Has llegado: {dest.get('nombre') or 'tu destino'} está aquí mismo."
        return {"tipo": "llegada", "mensaje": self._saludo(perfil, texto), "sugerencia": None,
                "motivo": {"destino": dest.get("nombre")}, "fuentes": []}
