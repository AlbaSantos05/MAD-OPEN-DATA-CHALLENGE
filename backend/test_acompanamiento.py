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


carmen = lambda: Perfil("Carmen", cuestas="mucho", descanso="mucho", escaleras=True, sensible_polen=True)  # noqa: E731
antonio = lambda: Perfil("Antonio", transporte="algo")  # noqa: E731

av_c, ctx_c = simular(acomp, carmen(), puntos, mes=4, hora=17, verbose=False)
av_a, _ = simular(acomp, antonio(), puntos, mes=4, hora=17, verbose=False)
av_c_oct, _ = simular(acomp, carmen(), puntos, mes=10, hora=17, verbose=False)

print("Personalización")
comprobar(len(av_c) > len(av_a), f"Carmen recibe más avisos que Antonio en el mismo paseo ({len(av_c)} vs {len(av_a)})")
comprobar("cuesta" in tipos(av_c) and "cuesta" not in tipos(av_a), "Solo a Carmen se le avisa de cuestas")
comprobar("polen" in tipos(av_c), "A Carmen (sensible al polen) se le avisa de polen en abril")
comprobar("polen" not in tipos(av_a), "A Antonio (no sensible) no se le avisa de polen")
comprobar("polen" not in tipos(av_c_oct), "En octubre no hay aviso de polen (el calendario no marca nivel medio/alto ese mes)")

print("No es pesado")
metros = [m for m, _, _ in av_c]
comprobar(all(b - a >= 40 for a, b in zip(metros, metros[1:])), "Nunca dos avisos en menos de 40 m")
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

print("Volver")
v = acomp.volver(*puntos[-1], Perfil("Carmen", cuestas="mucho", cansado=True), ctx_c)
comprobar(v["ruta_recomendada"]["longitud_m"] > 0 and "alternativa_transporte" in v, "«Quiero volver» devuelve ruta adaptada y transporte")

print(f"\n{ok} comprobaciones superadas.")
