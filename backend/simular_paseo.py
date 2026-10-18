"""Paseo de demostración por Chamberí para el acompañamiento libre.

Genera un recorrido real (calles de verdad, sin destino para la persona) y lo recorre
paso a paso llamando al acompañante, como haría el frontend con el GPS del móvil.

    python backend/simular_paseo.py                 # Carmen y Antonio, mes y hora de la demo
    python backend/simular_paseo.py --mes 4 --hora 17

El frontend puede pedir los mismos puntos a GET /paseo_demo y llamar a POST /acompanar
en cada uno (contexto incluido) para la simulación en pantalla.
"""
import argparse
import sys
from pathlib import Path

import geopandas as gpd
import networkx as nx
from shapely.geometry import LineString, Point

sys.path.insert(0, str(Path(__file__).resolve().parent))
from acompanamiento import Acompanante, _coords_desde  # noqa: E402
from profiles import Perfil  # noqa: E402

# Recorrido de la demo: de la glorieta de Quevedo hacia el oeste (zona de Arapiles/Gaztambide,
# la parte con más desnivel de Chamberí) y vuelta hacia el norte.
PUNTOS_DE_PASO = [
    (40.43280, -3.70400),   # Glorieta de Quevedo
    (40.43330, -3.70930),   # Calle de Fernando el Católico (tramo central)
    (40.43460, -3.71500),   # Calle de Fernando el Católico (oeste, cuesta abajo hacia Moncloa)
    (40.43790, -3.71470),   # Calle de Hilarión Eslava
    (40.43950, -3.71040),   # Calle de Cea Bermúdez
]
PASO_M = 15


def _recorrido(motor, puntos):
    """Une los puntos de paso por el camino más corto y devuelve la línea (EPSG:25830)."""
    nodos = [motor._nodo_cercano(lat, lon) for lat, lon in puntos]
    largo = motor.calles["length"].to_dict()
    coords = []
    for o, d in zip(nodos, nodos[1:]):
        camino = nx.shortest_path(motor.G, o, d,
                                  weight=lambda u, v, e: min(largo[a["idx"]] for a in e.values()))
        for a, b in zip(camino, camino[1:]):
            idx = min(motor.G[a][b].values(), key=lambda e: largo[e["idx"]])["idx"]
            xy_a = motor.nodos.loc[motor.nodos["id"] == a].geometry.iloc[0]
            tramo = _coords_desde(motor.calles.at[idx, "geometry"], (xy_a.x, xy_a.y))
            coords.extend(tramo if not coords else tramo[1:])
    return LineString(coords)


def paseo_demo(motor, paso_m=PASO_M):
    """Lista de [lat, lon] cada `paso_m` metros a lo largo del recorrido de demostración."""
    linea = _recorrido(motor, PUNTOS_DE_PASO)
    n = int(linea.length // paso_m)
    pts = gpd.GeoSeries([linea.interpolate(i * paso_m) for i in range(n + 1)], crs=25830).to_crs(4326)
    return [[round(p.y, 6), round(p.x, 6)] for p in pts]


FRASES = {"cansado": "Estoy cansada", "calor": "Tengo calor", "mareo": "Me estoy mareando",
          "dolor_piernas": "Me duelen las piernas", "mejor": "Ya estoy mejor"}


def simular(acomp, perfil, puntos, mes, hora, cansada_en=None, dice=None, verbose=True):
    """Recorre los puntos como si fuera el móvil. `dice` = {índice_del_punto: estado}
    simula lo que la persona dice en ese momento («calor», «mareo», «mejor»...)."""
    ctx = {"mes": mes, "hora": hora}
    dice = dict(dice or {})
    if cansada_en is not None:
        dice.setdefault(cansada_en, "cansado")
    avisos, antes = [], None
    for i, (lat, lon) in enumerate(puntos):
        estado = dice.get(i)
        if estado and verbose:
            print(f"\n   [{ctx.get('metros_andados', 0):>5.0f} m] {perfil.nombre} dice: «{FRASES.get(estado, estado)}»")
        r = acomp.paso(lat, lon, perfil, ctx, estado=estado,
                       lat_antes=antes[0] if antes else None, lon_antes=antes[1] if antes else None)
        ctx, antes = r["contexto"], (lat, lon)
        if r["aviso"]:
            tipo = r["aviso"]["tipo"] + (f":{r['aviso']['factor']}" if r["aviso"].get("factor") else "")
            avisos.append((ctx["metros_andados"], tipo, r["aviso"]["mensaje"]))
            if verbose:
                z = r["zona"]
                print(f"   [{ctx['metros_andados']:>5.0f} m] {r['posicion']['calle'] or '':<30.30} "
                      f"{z['categoria_aqui']:<8} {tipo.upper():<22} {r['aviso']['mensaje']}")
    return avisos, ctx


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    from recommendation import Motor

    ap = argparse.ArgumentParser()
    ap.add_argument("--mes", type=int, default=4)
    ap.add_argument("--hora", type=int, default=17)
    args = ap.parse_args()

    motor = Motor()
    acomp = Acompanante(motor)
    puntos = paseo_demo(motor)
    print(f"\nPaseo de demostración: {len(puntos)} puntos, ~{(len(puntos) - 1) * PASO_M} m · mes {args.mes}, {args.hora}:00 h")

    carmen = Perfil("Carmen", cuestas="mucho", descanso="mucho", escaleras=True, sensible_polen=True)
    antonio = Perfil("Antonio", transporte="algo")
    print("\n=== CARMEN (cuestas y descanso «mucho», evita escaleras, sensible al polen) ===")
    n = len(puntos)
    simular(acomp, carmen, puntos, args.mes, args.hora,
            dice={n // 5: "calor", n // 2: "mareo", (2 * n) // 3: "mejor"})
    print("\n=== ANTONIO (sin dificultades, le gusta tener transporte cerca) ===")
    simular(acomp, antonio, puntos, args.mes, args.hora)

    print("\n=== CARMEN dice «quiero volver» al final ===")
    carmen.cansado = True
    _, ctx = simular(acomp, Perfil("Carmen", cuestas="mucho", descanso="mucho", escaleras=True),
                     puntos, args.mes, args.hora, verbose=False)
    vuelta = acomp.volver(*puntos[-1], carmen, ctx)
    print("   " + vuelta["explicacion"])
