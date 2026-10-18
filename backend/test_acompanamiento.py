"""Pruebas del acompañamiento libre (sin pytest: python backend/test_acompanamiento.py).

Comprueban el comportamiento, no frases exactas: que cada regla salta cuando debe,
que no salta cuando no debe y que el acompañante no se vuelve pesado.
"""
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from acompanamiento import Acompanante  # noqa: E402
from profiles import Perfil  # noqa: E402
from recommendation import Motor  # noqa: E402
from simular_paseo import paseo_demo, simular  # noqa: E402

motor = Motor()
acomp = Acompanante(motor)
puntos = paseo_demo(motor)
ok = 0


def comprobar(condicion, texto):
    global ok
    assert condicion, "FALLA: " + texto
    ok += 1
    print("  ✓", texto)


def tipos(avisos):
    return [t for _, t, _ in avisos]


def hay(avisos, palabra):
    """¿Algún aviso de ese tipo o motivo? (los avisos de tramo son «barrera:cuesta», «incomodidad:polen»...)"""
    return any(palabra in t for t in tipos(avisos))


carmen = lambda: Perfil("Carmen", cuestas="mucho", descanso="mucho", escaleras=True, sensible_polen=True)  # noqa: E731
antonio = lambda: Perfil("Antonio", transporte="algo")  # noqa: E731

av_c, ctx_c = simular(acomp, carmen(), puntos, mes=4, hora=17, verbose=False)
av_a, _ = simular(acomp, antonio(), puntos, mes=4, hora=17, verbose=False)
av_c_oct, _ = simular(acomp, carmen(), puntos, mes=10, hora=17, verbose=False)

print("Personalización")
comprobar(len(av_c) > len(av_a), f"Carmen recibe más avisos que Antonio en el mismo paseo ({len(av_c)} vs {len(av_a)})")
comprobar(hay(av_c, "barrera:cuesta") and not hay(av_a, "barrera:cuesta"), "Las cuestas son barrera (alerta) para Carmen y nunca para Antonio")
comprobar(all(t == "incomodidad:cuesta" for t in tipos(av_a) if "cuesta" in t), "A Antonio solo se le mencionan las cuestas muy fuertes, con tono tranquilo")
comprobar(hay(av_c, "polen"), "A Carmen (sensible al polen) se le avisa de polen en abril")
comprobar(not hay(av_a, "polen"), "A Antonio (no sensible) no se le avisa de polen")
comprobar(not hay(av_c_oct, "polen"), "En octubre no hay aviso de polen (el calendario no marca nivel medio/alto ese mes)")
comprobar(hay(av_c, "barrera:") and all(t.split(":")[0] in ("barrera", "incomodidad") for t in tipos(av_c) if ":" in t),
          "Los avisos de la calle llevan su nivel: barrera (alerta) o incomodidad (tono tranquilo)")

print("No es pesado")
seguidos = [(a, b) for a, b in zip(av_c, av_c[1:]) if b[1] != "barrera:escaleras"]
comprobar(all(b[0] - a[0] >= 40 for a, b in seguidos), "Nunca dos avisos en menos de 40 m (salvo unas escaleras)")
comprobar(tipos(av_c).count("inicio") == 1, "El mensaje de inicio se da una sola vez")
comprobar(len(av_c) <= ctx_c["metros_andados"] / 150 + 1, "Como media, no más de un aviso cada 150 m")

print("Contexto")
av_cans, _ = simular(acomp, carmen(), puntos, mes=4, hora=17, cansada_en=len(puntos) // 2, verbose=False)
mitad = [t for m, t, _ in av_cans if m >= ctx_c["metros_andados"] / 2 - 20]
comprobar("cansancio" in mitad, "Al decir «estoy cansada» aparece la ayuda para volver (transporte cercano)")

print("Sin estado en el servidor")
r1 = acomp.paso(*puntos[0], carmen(), {"mes": 4, "hora": 17})
r2 = acomp.paso(*puntos[0], carmen(), {"mes": 4, "hora": 17})
comprobar(r1["aviso"]["tipo"] == r2["aviso"]["tipo"] == "inicio", "Mismo paso y mismo contexto -> misma respuesta")
comprobar("casa" in r1["contexto"], "El contexto guarda dónde empezó el paseo (casa)")

print("Volver e ir a un sitio (modo destino)")
import json  # noqa: E402
import networkx as nx  # noqa: E402
v = acomp.volver(*puntos[-1], Perfil("Carmen", cuestas="mucho", cansado=True), ctx_c)
comprobar(v["ruta"]["longitud_m"] > 0 and "alternativa_transporte" in v, "«Quiero volver» devuelve la ruta y el transporte cercano")
o, d = motor._nodo_cercano(*puntos[-1]), motor._nodo_cercano(ctx_c["casa"]["lat"], ctx_c["casa"]["lon"])
corta = nx.shortest_path_length(motor.G, o, d, weight="largo")
xy = dict(zip(motor.nodos["id"], motor.nodos.geometry))
acceso = acomp._a_metros(*puntos[-1]).distance(xy[o]) + acomp._a_metros(ctx_c["casa"]["lat"], ctx_c["casa"]["lon"]).distance(xy[d])
comprobar(abs(v["ruta"]["longitud_m"] - (corta + acceso)) <= 1, "La ruta es la MÁS CORTA andando (no se alarga por la persona)")
comprobar(v["contexto"]["destino"]["tipo"] == "casa", "Volver deja el destino en el contexto: los pasos siguientes guían")
ctx_v = v["contexto"]
r = acomp.paso(*puntos[-1], carmen(), ctx_v)
json.dumps(r)
comprobar(r["destino"] and r["destino"]["metros_restantes"] > 0 and r["destino"]["coordenadas"], "Cada paso devuelve lo que falta y la línea hasta el destino")
r_lejos = acomp.paso(*puntos[len(puntos) // 2], carmen(), r["contexto"], *puntos[-1])
comprobar(abs(r_lejos["destino"]["metros_restantes"] - r["destino"]["metros_restantes"]) > 100,
          "Si se mueve a otro sitio, la ruta se recalcula desde donde está")
casa = ctx_c["casa"]
r_fin = acomp.paso(casa["lat"], casa["lon"], carmen(), r["contexto"], *puntos[1])
comprobar(r_fin["aviso"]["tipo"] == "llegada" and "destino" not in r_fin["contexto"],
          "Al llegar avisa y vuelve al paseo libre")

print("Clasificación personal de los tramos")
ctx_abril = {"mes": 4, "hora": 17}
idc = acomp.clasificar(carmen(), ctx_abril)
ida = acomp.clasificar(antonio(), ctx_abril)
no_c, no_a = (idc["categoria"] == "no apta").sum(), (ida["categoria"] == "no apta").sum()
comprobar(no_c > 100 and no_a < no_c / 5, f"El mismo barrio tiene más zonas no aptas para Carmen que para Antonio ({no_c} vs {no_a})")
esc = motor.calles.index[motor.calles["escalera"]]
comprobar(len(esc) == 0 or (idc.loc[esc, "categoria"] == "no apta").all(), "Un tramo con escaleras es rojo para quien las evita")
comprobar((ida.loc[esc, "categoria"] != "no apta").all(), "…y no para quien no las evita")
llano = motor.calles.index[(motor.calles["pendiente_pct"] == 0) & ~motor.calles["escalera"]]
comprobar((idc.loc[llano, "categoria"] != "no apta").all(), "Una calle llana y sin escaleras nunca es roja (el polen o los bancos son amarillo)")
empinado = motor.calles["pendiente_pct"].idxmax()
mot = [m["factor"] for m in acomp.motivos(empinado, idc, 4)]
comprobar("cuesta" in mot, "La zona no apta explica su motivo con el dato (cuesta)")
z = acomp.paso(*puntos[40], carmen(), ctx_abril)["zona"]
comprobar({"categoria_aqui", "motivos_aqui", "no_aptas_cerca", "radio_m"} <= set(z), "Cada paso devuelve cómo es su calle y lo rojo alrededor")

print("Lo que dice durante el paseo")
orden = {"apta": 0, "regular": 1, "no apta": 2}
for estado in ("calor", "mareo", "dolor_piernas"):
    id_e = acomp.clasificar(carmen(), {**ctx_abril, "estados": [estado]})
    peor = (id_e["categoria"].map(orden) > idc["categoria"].map(orden)).sum()
    comprobar(peor > 100, f"Decir «{estado}» recalcula los tramos (más exigentes: {peor} empeoran)")
ctx_m = acomp.paso(*puntos[30], carmen(), ctx_abril)["contexto"]
r = acomp.paso(*puntos[31], carmen(), ctx_m, *puntos[30], estado="mareo")
comprobar(r["aviso"]["tipo"] == "reaccion" and "112" in r["aviso"]["mensaje"], "«Me estoy mareando» -> respuesta inmediata: sentarse y, si va a más, 112")
comprobar(r["aviso"]["sugerencia"]["banco"]["distancia_m"] <= 300, "…con el banco más cercano")
comprobar("mareo" in r["contexto"]["estados"], "El estado queda en el contexto para los pasos siguientes")
r2 = acomp.paso(*puntos[32], carmen(), r["contexto"], *puntos[31], estado="mejor")
comprobar(r2["contexto"]["estados"] == [] and r2["aviso"]["tipo"] == "reaccion", "«Ya estoy mejor» borra los estados")
av_calor, _ = simular(acomp, antonio(), puntos, mes=4, hora=17, dice={20: "calor"}, verbose=False)
comprobar("reaccion" in tipos(av_calor), "Incluso a Antonio (sin dificultades) se le responde si dice «tengo calor»")
u = acomp.paso(*puntos[33], carmen(), r2["contexto"], *puntos[32], urgencia=True)
comprobar(u["aviso"]["tipo"] == "alerta_salud" and "112" in u["aviso"]["mensaje"], "Síntomas graves -> alerta con el 112 por encima de todo")

print("Mapa personal")
g = acomp.mapa_personal(carmen(), ctx_abril)
comprobar(len(g["features"]) == len(motor.calles) and g["resumen"]["no apta"] == no_c, "El mapa personal pinta cada tramo con su categoría")

print(f"\n{ok} comprobaciones superadas.")
