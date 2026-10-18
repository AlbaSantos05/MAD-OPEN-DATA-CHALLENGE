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
import pandas as pd
from shapely.geometry import LineString, Point

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
    "reaccion":  (95, 0),       # respuesta inmediata a lo que la persona acaba de decir
    "escaleras": (90, 120),
    "cuesta":    (80, 200),
    "zona_actual": (75, 300),   # está en una zona poco adecuada PARA ELLA
    "cansancio": (70, 600),
    "zona_delante": (65, 200),  # va a entrar en una zona poco adecuada
    "descanso":  (60, 250),
    "polen":     (50, 500),
    "alejarse":  (40, 400),
    "agua":      (30, 500),
    "centro_salud": (25, 800),  # pasa junto a un centro de salud (informativo, poco frecuente)
    "inicio":    (20, 10 ** 9),
}

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

# ---------------------------------------------------------------- idoneidad personal
# Cada tramo recibe una idoneidad 0-100 PARA ESTA PERSONA. Para cada factor:
#   penalización = tope × min(1, peso/2 × p)     (p en 0..1 sale del dato del tramo)
#   idoneidad    = 100 × Π (1 − penalización)
# El peso sale del perfil (nada 0 / algo 1 / mucho 2) más lo que la persona dice en el paseo.
# El tope limita factores que son preferencias y no barreras (árboles cerca ≠ sombra).
TOPES = {"escaleras": 1.0, "cuesta": 1.0, "descanso": 0.7, "transporte": 0.5,
         "polen": 0.8, "arbolado": 0.5, "agua": 0.5}
APTA, NO_APTA = 70, 45            # ≥ 70 apta · 45-70 regular · < 45 no apta
RADAR_M = 120                     # radio que vigila alrededor de la posición

# Estados que la persona puede decir durante el paseo y cuánto sube cada peso
ESTADOS = {
    "cansado":       {"descanso": 1.0, "cuesta": 1.0, "transporte": 0.5},
    "calor":         {"arbolado": 1.0, "agua": 1.5, "descanso": 0.5},
    "mareo":         {"descanso": 3.0, "transporte": 1.0, "cuesta": 1.0},
    "dolor_piernas": {"cuesta": 2.0, "descanso": 1.0},
}
_SUBIR = {"nada": "algo", "algo": "mucho", "mucho": "mucho"}


# Regla clásica que cubre cada factor, y avisos que hacen innecesario otro inmediato
FACTOR_REGLA = {"cuesta": "cuesta", "polen": "polen", "descanso": "descanso", "agua": "agua",
                "transporte": "alejarse", "escaleras": "escaleras"}
RELACIONADAS = {"cuesta": ("zona_delante",), "polen": ("zona_delante",), "escaleras": ("zona_delante",),
                "zona_actual": ("zona_delante",), "zona_delante": ("zona_actual",)}
REACCION_CUBRE = {"cansado": ("descanso", "cansancio"), "calor": ("agua", "descanso"),
                  "mareo": ("descanso", "cansancio"), "dolor_piernas": ("descanso", "cansancio")}


def _categoria(score):
    return "apta" if score >= APTA else ("regular" if score >= NO_APTA else "no apta")


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

    # ------------------------------------------------------------ idoneidad personal por tramo
    @staticmethod
    def estados(perfil, ctx):
        """Estados activos: los que la persona ha dicho en el paseo (+ «cansado» del perfil)."""
        e = set((ctx or {}).get("estados", []))
        if getattr(perfil, "cansado", False):
            e.add("cansado")
        return e

    @staticmethod
    def perfil_efectivo(perfil, estados):
        """El perfil tal y como está AHORA: lo que dice durante el paseo endurece los umbrales."""
        cambios = {}
        if estados & {"cansado", "mareo", "dolor_piernas"}:
            cambios["cansado"] = True
        if "dolor_piernas" in estados:
            cambios["cuestas"] = _SUBIR[perfil.cuestas]
        if "mareo" in estados:
            cambios["descanso"] = "mucho"
        if "calor" in estados:
            cambios["arbolado"] = _SUBIR[perfil.arbolado]
            cambios["descanso"] = _SUBIR[cambios.get("descanso", perfil.descanso)]
        return replace(perfil, **cambios) if cambios else perfil

    def _umbral_cuesta_mapa(self, perfil):
        u = self._umbral_cuesta(perfil)
        return u if u is not None else 9.0          # sin dificultad declarada: solo cuestas fuertes

    def pesos_idoneidad(self, perfil, estados, mes, hora):
        p = {"cuesta": NIVELES[perfil.cuestas], "descanso": NIVELES[perfil.descanso],
             "transporte": NIVELES[perfil.transporte], "arbolado": NIVELES[perfil.arbolado],
             "polen": 2.0 if getattr(perfil, "sensible_polen", False) else 0.0,
             "agua": 1.0 if (6 <= mes <= 9 and 12 <= hora <= 20) else 0.0,
             "escaleras": 0.0}
        for e in estados:
            for k, w in ESTADOS.get(e, {}).items():
                p[k] += w
        return p

    def idoneidad(self, perfil, contexto=None):
        """DataFrame (índice = tramo) con la penalización de cada factor, la idoneidad 0-100
        y la categoría, para este perfil, lo que ha dicho en el paseo, el mes y la hora."""
        ctx = contexto or {}
        estados = self.estados(perfil, ctx)
        mes, hora = int(ctx.get("mes", 10)), int(ctx.get("hora", 17))
        clave = (tuple(sorted(asdict(perfil).items())), tuple(sorted(estados)), mes, hora)
        if clave in self._cache_idon:
            return self._cache_idon[clave]

        pe = self.perfil_efectivo(perfil, estados)
        w = self.pesos_idoneidad(pe, estados, mes, hora)
        c = self.calles
        umbral = self._umbral_cuesta_mapa(pe)
        p = pd.DataFrame(index=c.index)
        p["cuesta"] = ((c["pendiente_pct"] - (umbral - 1)) / 2).clip(0, 1)   # 0 un punto por debajo del umbral
        p["descanso"] = ((self._dist_banco - 50) / 250).clip(0, 1)
        p["transporte"] = ((c["dist_transporte"] - 150) / 350).clip(0, 1)
        p["arbolado"] = 1 / (1 + self._arboles)
        p["agua"] = ((self._dist_agua - 150) / 400).clip(0, 1)
        p["polen"] = (self._polen_serie(mes) / 10).clip(0, 1)

        pen = pd.DataFrame(index=c.index)
        for k in ("cuesta", "descanso", "transporte", "arbolado", "agua", "polen"):
            pen[k] = TOPES[k] * (w[k] / 2 * p[k]).clip(upper=1)
        pen["escaleras"] = c["escalera"].astype(float) if pe.escaleras else 0.0
        score = 100 * (1 - pen).prod(axis=1)
        pen["idoneidad"] = score.round(0)
        pen["categoria"] = score.map(_categoria)
        if len(self._cache_idon) > 32:
            self._cache_idon.clear()
        self._cache_idon[clave] = pen
        return pen

    def _polen_serie(self, mes):
        n = {idx: self._polen_activo([idx], mes)[0] for idx in self.polen_tramo}
        return pd.Series(n, dtype=float).reindex(self.calles.index, fill_value=0.0)

    def motivos(self, idx, pen, mes):
        """Por qué un tramo no es adecuado, en palabras y con el dato que lo explica."""
        f = self.calles.loc[idx]
        fila = pen.loc[idx]
        textos = {
            "escaleras": "tiene escaleras",
            "cuesta": f"tiene una cuesta del {float(f['pendiente_pct']):.0f} %",
            "descanso": f"el banco más cercano queda a unos {float(self._dist_banco[idx]):.0f} m",
            "transporte": f"el transporte público queda a unos {float(f['dist_transporte']):.0f} m",
            "arbolado": "apenas tiene árboles cerca",
            "agua": f"la fuente más cercana queda a unos {float(self._dist_agua[idx]):.0f} m",
            "polen": f"hay {self._polen_activo([idx], mes)[0]} árboles cuyo polen suele estar alto o medio en {MESES[mes - 1]}",
        }
        orden = sorted(TOPES, key=lambda k: -float(fila[k]))
        return [{"factor": k, "texto": textos[k], "penalizacion": round(float(fila[k]), 2)}
                for k in orden if float(fila[k]) >= 0.2]

    def _score_camino(self, idxs, pen):
        largos = self.calles.loc[idxs, "length"].clip(lower=1)
        return float((pen.loc[idxs, "idoneidad"] * largos).sum() / largos.sum())

    def radar(self, p, pen, radio=RADAR_M):
        """Tramos a menos de `radio` m de la posición, con su idoneidad y distancia."""
        pos = self.calles.sindex.query(p.buffer(radio), predicate="intersects")
        idxs = self.calles.index[pos]
        d = self.calles.loc[idxs].geometry.distance(p)
        return pd.DataFrame({"idoneidad": pen.loc[idxs, "idoneidad"], "distancia": d}).sort_values("distancia")

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

        pen = self.idoneidad(perfil_base, ctx)
        # Estado de ESTE paso para las reglas de zona (por hilo: FastAPI atiende peticiones en paralelo)
        self._paso.pen, self._paso.p = pen, p
        self._paso.rumbo = rumbo if p_antes is not None else None   # sin paso anterior no hay sentido
        zona = self._zona(p, idx, previsto, pen, ctx)

        candidatos = []
        if urgencia:
            candidatos.append(self._r_urgencia(perfil, ctx, p, self._paso.rumbo))
        elif nuevo:
            candidatos.append(self._reaccion(nuevo, perfil, ctx, p, self._paso.rumbo, zona))
        for regla in (self._r_escaleras, self._r_cuesta, self._r_zona_actual, self._r_cansancio,
                      self._r_zona_delante, self._r_descanso, self._r_polen, self._r_alejarse,
                      self._r_agua, self._r_centro_salud, self._r_inicio):
            a = regla(perfil, ctx, idx, hacia, falta, ramas, previsto, presupuesto)
            if a:
                candidatos.append(a)

        # Filtro anti-pesadez: separación global y tiempo de espera por tipo
        validos = []
        for a in candidatos:
            prioridad, espera = REGLAS[a["tipo"]]
            inmediato = a["tipo"] in ("reaccion", "alerta_salud")
            ultimo = ctx["avisos_dados"].get(a["tipo"], -10 ** 9)
            if not inmediato and ctx["metros_andados"] - ultimo < espera:
                continue
            if (not inmediato and a["tipo"] != "escaleras"
                    and ctx["metros_andados"] - ctx["ultimo_aviso_m"] < SEPARACION_MIN_M):
                continue
            a["prioridad"] = prioridad
            validos.append(a)
        validos.sort(key=lambda a: -a["prioridad"])
        aviso = validos[0] if validos else None
        if aviso:
            ctx["avisos_dados"][aviso["tipo"]] = ctx["metros_andados"]
            # Lo ya dicho por una regla no se repite con otras palabras por otra
            for t in RELACIONADAS.get(aviso["tipo"], ()):
                ctx["avisos_dados"][t] = ctx["metros_andados"]
            if aviso["tipo"] == "reaccion":
                for t in REACCION_CUBRE.get(aviso.get("estado"), ()):
                    ctx["avisos_dados"][t] = ctx["metros_andados"]
            for fac in (aviso.get("motivo") or {}).get("factores", [])[:2]:
                if FACTOR_REGLA.get(fac["factor"]):
                    ctx["avisos_dados"][FACTOR_REGLA[fac["factor"]]] = ctx["metros_andados"]
            ctx["ultimo_aviso_m"] = ctx["metros_andados"]
            if aviso.get("tramo_id") is not None:
                ctx["tramos_avisados"] = (ctx["tramos_avisados"] + [int(aviso["tramo_id"])])[-30:]

        ctx["metros_andados"] = round(ctx["metros_andados"], 1)
        ctx["metros_sin_banco"] = round(ctx["metros_sin_banco"], 1)
        ctx["metros_sin_fuente"] = round(ctx["metros_sin_fuente"], 1)
        return {
            "posicion": {"lat": lat, "lon": lon, "calle": _calle(fila.get("nombre")), "tramo_id": int(idx),
                         "metros_hasta_cruce": round(falta)},
            "aviso": aviso,
            "tambien_se_cumplian": [a["tipo"] for a in validos[1:]],
            "zona": zona,
            "contexto": ctx,
        }

    # ------------------------------------------------------------ zonas adecuadas / no adecuadas
    def _zona(self, p, idx, previsto, pen, ctx):
        """Lo que ve el radar: idoneidad aquí, por delante y tramos no aptos alrededor."""
        r = self.radar(p, pen)
        cerca = r[r["distancia"] <= 60].index.tolist() or [idx]
        aqui = round(0.5 * float(pen.at[idx, "idoneidad"]) + 0.5 * self._score_camino(cerca, pen))
        delante = round(self._score_camino(previsto[1:], pen)) if len(previsto) > 1 else None
        mes = int(ctx.get("mes", 10))
        no_aptas = []
        for i, fila in r[r["idoneidad"] < NO_APTA].head(5).iterrows():
            no_aptas.append({"tramo_id": int(i), "calle": _calle(self.calles.at[i, "nombre"]),
                             "idoneidad": int(fila["idoneidad"]), "distancia_m": round(float(fila["distancia"])),
                             "motivos": [m["texto"] for m in self.motivos(i, pen, mes)]})
        return {"idoneidad_aqui": aqui, "categoria_aqui": _categoria(aqui),
                "idoneidad_delante": delante,
                "categoria_delante": _categoria(delante) if delante is not None else None,
                "radio_m": RADAR_M, "no_aptas_cerca": no_aptas,
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

    def _mejor_cerca(self, p, pen, minimo=APTA, radio=150, excluir=()):
        """Tramo adecuado más cercano: {calle, distancia_m, orientacion, idoneidad, coordenadas}."""
        r = self.radar(p, pen, radio)
        r = r[(r["idoneidad"] >= minimo) & ~r.index.isin(list(excluir))]
        if r.empty:
            return None
        i = r.index[0]
        punto = self.calles.at[i, "geometry"].interpolate(self.calles.at[i, "geometry"].project(p))
        return {"tramo_id": int(i), "calle": _calle(self.calles.at[i, "nombre"]),
                "distancia_m": round(float(r.iloc[0]["distancia"])),
                "orientacion": self._orientacion(p, (punto.x, punto.y), self._paso.rumbo),
                "idoneidad": int(r.iloc[0]["idoneidad"]),
                "coordenadas": [self._a_latlon((punto.x, punto.y))]}

    def _r_zona_actual(self, perfil, ctx, idx, hacia, falta, ramas, previsto, presupuesto):
        pen, p = self._paso.pen, self._paso.p
        r = self.radar(p, pen, 60)
        aqui = 0.5 * float(pen.at[idx, "idoneidad"]) + 0.5 * self._score_camino(r.index.tolist() or [idx], pen)
        if aqui >= NO_APTA or int(idx) in ctx["tramos_avisados"] or ctx["metros_andados"] < 30:
            return None
        mes = int(ctx["mes"])
        mot = self.motivos(idx, pen, mes)
        if not mot:
            return None
        alt = self._mejor_cerca(p, pen, excluir=[idx])
        texto = f"Esta zona no es la más cómoda para ti: {_lista([m['texto'] for m in mot[:2]])}."
        if alt and alt["distancia_m"] >= 20:
            ori = f", {alt['orientacion']}" if alt["orientacion"] else ""
            texto += f" A unos {alt['distancia_m']} m{ori}{_por(alt['calle'])}, la zona es más adecuada."
        elif falta >= 30:
            texto += f" Quedan unos {falta:.0f} m hasta el cruce."
        return {"tipo": "zona_actual", "tramo_id": int(idx), "mensaje": self._saludo(perfil, texto),
                "sugerencia": alt,
                "motivo": {"idoneidad_zona": round(aqui), "umbral_no_apta": NO_APTA, "factores": mot},
                "fuentes": self._fuentes_de(mot)}

    def _r_zona_delante(self, perfil, ctx, idx, hacia, falta, ramas, previsto, presupuesto):
        pen = self._paso.pen
        if len(previsto) < 2:
            return None
        delante = previsto[1:]
        sc = self._score_camino(delante, pen)
        aqui = float(pen.at[idx, "idoneidad"])
        if sc >= NO_APTA or aqui < 60 or any(int(i) in ctx["tramos_avisados"] for i in delante):
            return None
        peor = min(delante, key=lambda i: pen.at[i, "idoneidad"])
        mes = int(ctx["mes"])
        mot = self.motivos(peor, pen, mes)
        if not mot:
            return None
        # Mejor salida del cruce según la idoneidad de lo que viene después
        alt, mejor = None, sc
        for i, otro, dif in ramas:
            camino = self._seguir(i, hacia, max(presupuesto, 80))
            s = self._score_camino(camino, pen)
            if s >= max(mejor + 20, APTA - 10) and abs(dif) <= 145:
                mejor = s
                alt = {"direccion": _palabra_giro(dif), "calle": _calle(self.calles.at[i, "nombre"]),
                       "tramo_id": int(i), "idoneidad": round(s),
                       "coordenadas": [self._a_latlon(self.xy[otro])]}
        texto = f"Si sigues recto entras en una zona menos cómoda para ti: {_lista([m['texto'] for m in mot[:2]])}."
        if alt and alt["direccion"] != "recto":
            texto += f" En el cruce, {alt['direccion']}{_por(alt['calle'])}, se está mejor."
        return {"tipo": "zona_delante", "tramo_id": int(peor), "mensaje": self._saludo(perfil, texto),
                "sugerencia": alt,
                "motivo": {"idoneidad_delante": round(sc), "idoneidad_aqui": round(aqui), "factores": mot},
                "fuentes": self._fuentes_de(mot)}

    @staticmethod
    def _fuentes_de(motivos):
        mapa = {"escaleras": ["escaleras"], "cuesta": ["pendiente"], "descanso": ["bancos"],
                "transporte": ["transporte"], "arbolado": ["arbolado"], "agua": ["fuentes"],
                "polen": ["arbolado", "polen"]}
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
                           "idoneidad_aqui_ahora": zona["idoneidad_aqui"]},
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
        """GeoJSON de los tramos con su idoneidad y categoría PARA ESTA PERSONA."""
        ctx = contexto or {}
        pen = self.idoneidad(perfil, ctx)
        mes = int(ctx.get("mes", 10))
        feats = []
        cuenta = {"apta": 0, "regular": 0, "no apta": 0}
        for idx, geom in self._calles_4326.items():
            cat = pen.at[idx, "categoria"]
            cuenta[cat] += 1
            props = {"tramo_id": int(idx), "calle": _calle(self.calles.at[idx, "nombre"]),
                     "idoneidad": int(pen.at[idx, "idoneidad"]), "categoria": cat}
            if cat != "apta":
                props["motivos"] = [m["texto"] for m in self.motivos(idx, pen, mes)]
            feats.append({"type": "Feature", "properties": props,
                          "geometry": {"type": "LineString",
                                       "coordinates": [[round(x, 6), round(y, 6)] for x, y in geom.coords]}})
        return {"type": "FeatureCollection", "features": feats,
                "resumen": {**cuenta, "estados": sorted(self.estados(perfil, ctx)),
                            "pesos": self.pesos_idoneidad(self.perfil_efectivo(perfil, self.estados(perfil, ctx)),
                                                          self.estados(perfil, ctx), mes, int(ctx.get("hora", 17)))}}

    # ------------------------------------------------------------ reglas
    @staticmethod
    def _nombre(perfil):
        return perfil.nombre if perfil.nombre and perfil.nombre != "Usuario" else ""

    def _saludo(self, perfil, texto):
        n = self._nombre(perfil)
        return f"{n}, {texto[0].lower()}{texto[1:]}" if n else texto

    def _alternativa(self, ramas, hacia, presupuesto, malo):
        """Mejor salida del cruce que evita lo `malo` (función tramo->bool) en el horizonte."""
        for idx, otro, dif in ramas[1:] if ramas and abs(ramas[0][2]) < 35 else ramas:
            camino = self._seguir(idx, hacia, max(presupuesto, 60))
            if not any(malo(i) for i in camino):
                calle = _calle(self.calles.at[idx, "nombre"])
                return {"direccion": _palabra_giro(dif), "calle": calle,
                        "tramo_id": int(idx), "coordenadas": [self._a_latlon(self.xy[otro])]}
        return None

    def _r_escaleras(self, perfil, ctx, idx, hacia, falta, ramas, previsto, presupuesto):
        if not perfil.escaleras:
            return None
        for i in previsto:
            if bool(self.calles.at[i, "escalera"]) and int(i) not in ctx["tramos_avisados"]:
                alt = self._alternativa(ramas, hacia, presupuesto, lambda t: bool(self.calles.at[t, "escalera"]))
                texto = "Por delante hay escaleras."
                if alt:
                    texto += f" En el próximo cruce sigue {alt['direccion']}{_por(alt['calle'])} para evitarlas."
                return {"tipo": "escaleras", "tramo_id": int(i), "mensaje": self._saludo(perfil, texto),
                        "sugerencia": alt, "motivo": {"tramo_con_escaleras": int(i)},
                        "fuentes": [FUENTES_DATOS["escaleras"]]}
        return None

    def _umbral_cuesta(self, perfil):
        base = {"mucho": 5.0, "algo": 7.0}.get(perfil.cuestas)
        if base is None:
            return 7.0 if perfil.cansado else None
        return base - 1.0 if perfil.cansado else base

    def _r_cuesta(self, perfil, ctx, idx, hacia, falta, ramas, previsto, presupuesto):
        umbral = self._umbral_cuesta(perfil)
        if umbral is None:
            return None
        distancia_hasta = 0.0                       # metros desde la persona hasta el inicio del tramo
        for j, i in enumerate(previsto):
            largo = float(self.calles.at[i, "length"])
            pend = float(self.calles.at[i, "pendiente_pct"])
            if j == 1:
                distancia_hasta = falta
            elif j > 1:
                distancia_hasta += float(self.calles.at[previsto[j - 1], "length"])
            if pend < umbral or largo < 15 or int(i) in ctx["tramos_avisados"]:
                continue
            if j == 0:
                if falta < 40:                      # casi acabado: no merece la pena avisar
                    continue
                texto = (f"Estás en un tramo con cuesta (un {pend:.0f} %). "
                         f"Quedan unos {falta:.0f} m hasta el cruce; ve con calma.")
                alt = None
            else:
                alt = self._alternativa(ramas, hacia, presupuesto,
                                        lambda t: float(self.calles.at[t, "pendiente_pct"]) >= umbral
                                        and float(self.calles.at[t, "length"]) >= 15)
                texto = f"En unos {distancia_hasta:.0f} m empieza una cuesta (un {pend:.0f} %)."
                if alt:
                    texto += f" Si en el cruce sigues {alt['direccion']}{_por(alt['calle'])}, el camino es más llano."
            return {"tipo": "cuesta", "tramo_id": int(i), "mensaje": self._saludo(perfil, texto),
                    "sugerencia": alt,
                    "motivo": {"pendiente_pct": round(pend, 1), "umbral_perfil_pct": umbral,
                               "metros_hasta_cuesta": round(distancia_hasta)},
                    "fuentes": [FUENTES_DATOS["pendiente"]]}
        return None

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
                  " por si prefieres volver en transporte. Si quieres que te lleve de vuelta, dime «quiero volver».")
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

    def _r_polen(self, perfil, ctx, idx, hacia, falta, ramas, previsto, presupuesto):
        if not getattr(perfil, "sensible_polen", False):
            return None
        mes = int(ctx["mes"])
        delante = previsto[1:] or previsto
        total, detalle = self._polen_activo(delante, mes)
        if total < 5 or any(int(i) in ctx["tramos_avisados"] for i in delante[:1]):
            return None
        tipo, (n, nivel) = max(detalle.items(), key=lambda kv: kv[1][0])
        alt = self._alternativa(ramas, hacia, presupuesto,
                                lambda t: self._polen_activo([t], mes)[0] >= 3)
        texto = (f"Por delante hay unos {total} árboles cuyo polen suele estar {nivel} en {MESES[mes - 1]} "
                 f"(sobre todo {tipo.lower()}).")
        if alt:
            texto += f" Si sigues {alt['direccion']}{_por(alt['calle'])} hay menos."
        return {"tipo": "polen", "tramo_id": int(delante[0]), "mensaje": self._saludo(perfil, texto),
                "sugerencia": alt,
                "motivo": {"arboles_con_polen_activo": total, "detalle": {k: v[0] for k, v in detalle.items()},
                           "mes": mes, "nota": "patrón estacional histórico, no el polen de hoy"},
                "fuentes": [FUENTES_DATOS["arbolado"], FUENTES_DATOS["polen"]]}

    def _r_alejarse(self, perfil, ctx, idx, hacia, falta, ramas, previsto, presupuesto):
        if perfil.transporte == "nada" and not perfil.cansado:
            return None
        ahora = float(self.calles.at[idx, "dist_transporte"])
        delante = max(float(self.calles.at[i, "dist_transporte"]) for i in previsto)
        if ahora <= 250 and delante >= 350:
            lat, lon = self._a_latlon(self._punto_actual(idx, hacia, falta))
            t = self.m.mas_cercano(lat, lon, "transporte")
            texto = (f"Si sigues recto, el transporte público queda más lejos (hasta {delante:.0f} m andando). "
                     f"Ahora tienes {t['tipo']} {t['nombre']} a {t['distancia_m']} m.")
            return {"tipo": "alejarse", "mensaje": self._saludo(perfil, texto), "sugerencia": {"recurso": t},
                    "motivo": {"dist_transporte_ahora_m": round(ahora), "dist_transporte_delante_m": round(delante)},
                    "fuentes": [FUENTES_DATOS["transporte"]]}
        return None

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

    # ------------------------------------------------------------ volver
    def volver(self, lat, lon, perfil, contexto=None):
        """Ruta adaptada de vuelta a casa (punto de inicio). Si casa queda lejos (>1,5 km)
        o la persona está cansada, ofrece también el transporte más cercano."""
        ctx = contexto or {}
        casa = ctx.get("casa") or {"lat": lat, "lon": lon}
        perfil_vuelta = perfil
        resultado = self.m.recomendar_ruta((lat, lon), (casa["lat"], casa["lon"]), perfil_vuelta)
        t = self.m.mas_cercano(lat, lon, "transporte")
        resultado["alternativa_transporte"] = t
        if resultado["ruta_recomendada"]["longitud_m"] > 1500 or perfil.cansado:
            resultado["explicacion"] += (f" Si prefieres no andar tanto, la parada de {t['tipo']} "
                                         f"{t['nombre']} está a {t['distancia_m']} m.")
        return resultado