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
from dataclasses import asdict
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, Point

RAIZ = Path(__file__).resolve().parent.parent
PROC = RAIZ / "data" / "processed"
DATASET = RAIZ / "data" / "dataset"
CRS_M = 25830

HORIZONTE_M = 150          # cuánto mira por delante
RADIO_ARBOLES = 15         # m: árbol "en" el tramo (igual que trees_20m usa 15 m en el dataset)
SEPARACION_MIN_M = 40      # nunca dos avisos en menos de estos metros andados
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]

# Prioridad (mayor = más importante) y metros mínimos antes de repetir el mismo tipo de aviso
REGLAS = {
    "escaleras": (90, 120),
    "cuesta":    (80, 200),
    "cansancio": (70, 600),
    "descanso":  (60, 250),
    "polen":     (50, 500),
    "alejarse":  (40, 400),
    "agua":      (30, 500),
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
}


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
        print(f"  -> Acompañante listo: {len(self.vecinos)} cruces, "
              f"{len(self.polen_mes)} filas de polen, {len(self.aire)} de aire.")

    # ------------------------------------------------------------ datos ambientales
    def _cargar_ambiente(self):
        self.polen_mes = {}
        ruta = PROC / "polen_mes.csv"
        if ruta.exists():
            for f in pd.read_csv(ruta).itertuples():
                self.polen_mes[(f.tipo_polen, int(f.mes))] = (f.nivel, float(f.nivel_medio))
        self.especie_polen = {}
        ruta = PROC / "especie_polen.csv"
        if ruta.exists():
            self.especie_polen = dict(pd.read_csv(ruta)[["especie", "tipo_polen"]].values)
        self.aire = {}
        ruta = PROC / "aire_mes_hora.csv"
        if ruta.exists():
            for f in pd.read_csv(ruta).itertuples():
                self.aire[(int(f.mes), int(f.hora))] = (f.categoria, float(f.pct_regular_o_peor))

    def _polen_por_tramo(self):
        """tramo -> {tipo_polen: nº de árboles a menos de 15 m}. Cruza arbolado (Ayto.) con
        la tabla especie -> tipo de polen (calibrada con la red palinológica de la CM)."""
        self.polen_tramo = {}
        arboles = getattr(self.m, "arbolado", None)
        if arboles is None or len(arboles) == 0:                 # la API no publica los árboles
            ruta = DATASET / "poi.geojson"
            if not ruta.exists():
                return
            poi = gpd.read_file(ruta)
            arboles = poi[poi["tipo"] == "arbol"].to_crs(CRS_M)
        arboles = arboles.copy()
        arboles["tipo_polen"] = arboles["especie"].map(self.especie_polen)
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
    def paso(self, lat, lon, perfil, contexto=None, lat_antes=None, lon_antes=None):
        ctx = dict(contexto or {})
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

        candidatos = []
        for regla in (self._r_escaleras, self._r_cuesta, self._r_cansancio, self._r_descanso,
                      self._r_polen, self._r_alejarse, self._r_agua, self._r_inicio):
            a = regla(perfil, ctx, idx, hacia, falta, ramas, previsto, presupuesto)
            if a:
                candidatos.append(a)

        # Filtro anti-pesadez: separación global y tiempo de espera por tipo
        validos = []
        for a in candidatos:
            prioridad, espera = REGLAS[a["tipo"]]
            ultimo = ctx["avisos_dados"].get(a["tipo"], -10 ** 9)
            if ctx["metros_andados"] - ultimo < espera:
                continue
            if a["tipo"] != "escaleras" and ctx["metros_andados"] - ctx["ultimo_aviso_m"] < SEPARACION_MIN_M:
                continue
            a["prioridad"] = prioridad
            validos.append(a)
        validos.sort(key=lambda a: -a["prioridad"])
        aviso = validos[0] if validos else None
        if aviso:
            ctx["avisos_dados"][aviso["tipo"]] = ctx["metros_andados"]
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
            "contexto": ctx,
        }

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
        if not (6 <= int(ctx["mes"]) <= 9 and 12 <= int(ctx["hora"]) <= 20):
            return None
        if ctx["metros_sin_fuente"] < 600:
            return None
        lat, lon = self._a_latlon(self._punto_actual(idx, hacia, falta))
        f = self.m.mas_cercano(lat, lon, "fuente")
        if f["distancia_m"] > 200:
            return None
        texto = f"Hace calor a estas horas: tienes una fuente de agua a unos {f['distancia_m']} m."
        return {"tipo": "agua", "mensaje": self._saludo(perfil, texto), "sugerencia": {"recurso": f},
                "motivo": {"mes": ctx["mes"], "hora": ctx["hora"], "metros_sin_fuente": round(ctx["metros_sin_fuente"])},
                "fuentes": [FUENTES_DATOS["fuentes"]]}

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
            if altos:
                partes.append(f"En {MESES[mes - 1]} suele haber polen alto de: {', '.join(a.lower() for a in altos)}.")
                fuentes.append(FUENTES_DATOS["polen"])
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
