"""API del acompañante urbano (MVP Chamberí).

Pone el motor de recommendation.py a disposición de la web:
la web envía el perfil y los puntos, y recibe la ruta, la explicación y las coordenadas.

Arrancar (desde la carpeta raíz del proyecto):
    uvicorn backend.main:app --reload
Probar en el navegador:
    http://127.0.0.1:8000/docs
"""
import sys
from pathlib import Path
from typing import Literal

import networkx as nx
from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from typing import Optional

from backend.llm_service import analizar_mensaje_llm
from backend.ambiental import aviso_ambiental
from backend.profiles import Perfil

# Para poder importar recommendation.py y profiles.py estando en backend/
sys.path.insert(0, str(Path(__file__).resolve().parent))
from recommendation import Motor, DATOS  # noqa: E402
from profiles import Perfil  # noqa: E402
from acompanamiento import Acompanante  # noqa: E402
from simular_paseo import paseo_demo  # noqa: E402

app = FastAPI(
    title="Acompañante urbano · Chamberí",
    description="Recomendaciones de rutas y recursos urbanos adaptadas a personas mayores, "
                "a partir de datos abiertos de Madrid.",
    version="0.1",
)

# Permite que la web (frontend) llame a la API aunque se sirva desde otro puerto
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# El motor se carga una sola vez al arrancar (tarda unos segundos)
motor = Motor()
acompanante = Acompanante(motor)

Nivel = Literal["nada", "algo", "mucho"]
TipoAtajo = Literal["banco", "farmacia", "fuente", "metro", "bus", "transporte", "centro_salud"]
CAPAS = {"calles", "bancos", "farmacias", "fuentes", "metro", "bus"}


# ---------------------------------------------------------------- modelos de datos
class Punto(BaseModel):
    lat: float = Field(..., examples=[40.4290])
    lon: float = Field(..., examples=[-3.7020])


class PerfilIn(BaseModel):
    nombre: str = "Usuario"
    cuestas: Nivel = "nada"
    descanso: Nivel = "nada"
    escaleras: bool = False
    transporte: Nivel = "nada"
    arbolado: Nivel = "nada"
    sensible_polen: bool = False
    cansado: bool = False


class PeticionRuta(BaseModel):
    perfil: PerfilIn
    origen: Punto
    destino: Punto

    model_config = {
        "json_schema_extra": {
            "examples": [{
                "perfil": {"nombre": "Carmen", "cuestas": "mucho", "descanso": "mucho",
                           "escaleras": True, "transporte": "nada", "cansado": False},
                "origen": {"lat": 40.4290, "lon": -3.7020},
                "destino": {"lat": 40.4385, "lon": -3.7125},
            }]
        }
    }

class PeticionAcompanar(BaseModel):
    """Un paso del paseo libre. El frontend reenvía el `contexto` que devolvió el paso anterior."""
    perfil: PerfilIn
    lat: float = Field(..., examples=[40.4328])
    lon: float = Field(..., examples=[-3.7040])
    lat_antes: Optional[float] = Field(None, description="Posición del paso anterior (sentido de la marcha)")
    lon_antes: Optional[float] = None
    contexto: Optional[dict] = Field(None, description="Contexto devuelto por el paso anterior. "
                                     "En el primer paso, solo {'mes': 10, 'hora': 17} (opcional).")
    dice: Optional[str] = Field(None, max_length=500, examples=["Tengo calor"],
                                description="Lo que la persona acaba de decir en este paso (opcional): "
                                            "«estoy cansada», «tengo calor», «me estoy mareando», «ya estoy mejor»...")


class PeticionMapa(BaseModel):
    perfil: PerfilIn
    contexto: Optional[dict] = Field(None, description="Contexto del paseo (estados dichos, mes, hora)")


class PeticionVolver(BaseModel):
    perfil: PerfilIn
    lat: float
    lon: float
    contexto: Optional[dict] = Field(None, description="Contexto del paseo (incluye 'casa')")


class Destino(BaseModel):
    lat: float
    lon: float
    nombre: Optional[str] = Field(None, examples=["Farmacia en Calle de Feijoo, 12"])
    tipo: Optional[str] = Field(None, examples=["farmacia"])


class PeticionIrA(BaseModel):
    """Empezar a guiar hacia un sitio (modo destino). Los pasos siguientes de /acompanar recalculan la ruta."""
    perfil: PerfilIn
    lat: float
    lon: float
    destino: Destino
    contexto: Optional[dict] = Field(None, description="Contexto del paseo en curso (de /acompanar)")


class PeticionChat(BaseModel):
    texto: str = Field(..., min_length=1, max_length=500, examples=["Estoy cansada y necesito un banco"])
    lat: float = Field(..., description="Latitud exacta actual del usuario (OBLIGATORIA)")
    lon: float = Field(..., description="Longitud exacta actual del usuario (OBLIGATORIA)")
    perfil_actual: Optional[PerfilIn] = Field(None, description="Perfil actualmente guardado en la interfaz")
    contexto: Optional[dict] = Field(None, description="Contexto del paseo en curso (de /acompanar)")


# ---------------------------------------------------------------- endpoints
@app.get("/salud")
def salud():
    """Comprueba que la API está funcionando."""
    return {"estado": "ok", "tramos": len(motor.calles), "cruces": motor.G.number_of_nodes()}


@app.post("/ruta")
def ruta(peticion: PeticionRuta):
    """Ruta MÁS CORTA andando entre dos puntos, descrita en palabras para este perfil.
    No se elige por la persona un camino más largo: el acompañante avisa por el camino y ella decide."""
    perfil = Perfil(**peticion.perfil.model_dump())
    try:
        return motor.ruta(
            (peticion.origen.lat, peticion.origen.lon),
            (peticion.destino.lat, peticion.destino.lon),
            perfil,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except nx.NetworkXNoPath:
        raise HTTPException(status_code=404, detail="No hay un camino peatonal entre esos dos puntos.")


@app.get("/atajo/{tipo}")
def atajo(tipo: TipoAtajo, lat: float, lon: float):
    """Atajos de un solo toque: banco, farmacia, fuente, metro, bus, transporte o centro de salud más cercano."""
    try:
        return motor.mas_cercano(lat, lon, tipo)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/capas/{nombre}")
def capa(nombre: str):
    """Devuelve una capa GeoJSON (calles, bancos, farmacias, metro, bus) para pintarla en el mapa."""
    if nombre not in CAPAS:
        raise HTTPException(status_code=404, detail=f"Capa desconocida. Opciones: {sorted(CAPAS)}")
    return FileResponse(DATOS / f"chamberi_{nombre}.geojson", media_type="application/geo+json")


@app.get("/ambiente")
def ambiente(mes: Optional[int] = None, hora: Optional[int] = None):
    """Polen del mes y calidad del aire de esa hora en Chamberí (datos reales de Oracle).
    Sin parámetros usa el mes y la hora actuales. Ej.: /ambiente?mes=3&hora=9"""
    if mes is not None and not 1 <= mes <= 12:
        raise HTTPException(status_code=400, detail="mes debe estar entre 1 y 12")
    if hora is not None and not 0 <= hora <= 23:
        raise HTTPException(status_code=400, detail="hora debe estar entre 0 y 23")
    try:
        return aviso_ambiental(mes, hora)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/acompanar")
def acompanar(peticion: PeticionAcompanar):
    """Acompañamiento libre (sin destino): devuelve como mucho UN aviso útil para este paso,
    con su motivo y la fuente de datos, más el contexto actualizado para el paso siguiente."""
    perfil = Perfil(**peticion.perfil.model_dump())
    estado, urgencia, analisis = None, False, None
    if peticion.dice:
        # El LLM solo INTERPRETA lo dicho; qué recomendar lo deciden los datos
        analisis = analizar_mensaje_llm(peticion.dice)
        urgencia = analisis.intencion == "alerta_salud"
        estado = analisis.estado or ("cansado" if analisis.cansado else None)
    r = acompanante.paso(peticion.lat, peticion.lon, perfil, peticion.contexto,
                         lat_antes=peticion.lat_antes, lon_antes=peticion.lon_antes,
                         estado=estado, urgencia=urgencia)
    if analisis is not None:
        r["entendido"] = {"intencion": analisis.intencion, "estado": estado,
                          "tipo_recurso": analisis.tipo_recurso}
        if analisis.intencion == "volver":
            try:
                r["vuelta"] = acompanante.volver(peticion.lat, peticion.lon, perfil, r["contexto"])
                r["contexto"] = r["vuelta"]["contexto"]            # a partir de aquí: modo destino (casa)
            except (ValueError, nx.NetworkXNoPath):
                r["vuelta"] = None
        elif analisis.intencion == "buscar_recurso" and analisis.tipo_recurso:
            try:
                r["recurso"] = motor.mas_cercano(peticion.lat, peticion.lon, analisis.tipo_recurso)
                # Y le guía hasta allí (ruta más corta, recalculada en cada paso)
                ida = acompanante.ir_a(peticion.lat, peticion.lon, perfil, r["contexto"],
                                       {**r["recurso"], "tipo": r["recurso"].get("tipo") or analisis.tipo_recurso})
                r["ruta_destino"] = ida
                r["contexto"] = ida["contexto"]
            except (ValueError, nx.NetworkXNoPath):
                r["recurso"] = r.get("recurso")
    return r


@app.post("/ir_a")
def ir_a(peticion: PeticionIrA):
    """Modo destino: ruta MÁS CORTA andando hasta el sitio elegido (p. ej. el banco de un atajo).
    Devuelve el contexto con el destino; en cada paso, /acompanar recalcula la ruta desde donde esté
    la persona (si se desvía, se adapta), sigue dando los mismos avisos que en el paseo libre y avisa al llegar."""
    perfil = Perfil(**peticion.perfil.model_dump())
    try:
        return acompanante.ir_a(peticion.lat, peticion.lon, perfil, peticion.contexto,
                                peticion.destino.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except nx.NetworkXNoPath:
        raise HTTPException(status_code=404, detail="No hay un camino peatonal hasta ese sitio.")


@app.post("/mapa_personal")
def mapa_personal(peticion: PeticionMapa):
    """GeoJSON de las calles con su idoneidad 0-100 y categoría (apta / regular / no apta)
    PARA ESTE PERFIL y lo que ha dicho en el paseo. El mismo barrio cambia según la persona."""
    perfil = Perfil(**peticion.perfil.model_dump())
    return acompanante.mapa_personal(perfil, peticion.contexto)


@app.post("/volver")
def volver(peticion: PeticionVolver):
    """«Quiero volver»: ruta más corta andando hasta el inicio del paseo (modo destino) y transporte cercano."""
    perfil = Perfil(**peticion.perfil.model_dump())
    try:
        return acompanante.volver(peticion.lat, peticion.lon, perfil, peticion.contexto)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except nx.NetworkXNoPath:
        raise HTTPException(status_code=404, detail="No hay un camino peatonal de vuelta.")


@app.get("/paseo_demo")
def paseo_demo_endpoint(paso_m: int = 15):
    """Puntos [lat, lon] de un paseo real por Chamberí para simular el acompañamiento en pantalla."""
    return {"puntos": paseo_demo(motor, paso_m), "paso_m": paso_m}


@app.post("/chat")
def chat_interactivo(peticion: PeticionChat):
    """Endpoint de lenguaje natural: interpreta intención y delega en el motor determinista."""

    # 1. Analizar el mensaje con el LLM (Structured Outputs)
    analisis = analizar_mensaje_llm(peticion.texto)

    # 2. RAMA 1: Alerta de Salud (Respuesta de seguridad inmediata)
    if analisis.intencion == "alerta_salud":
        return {
            "intencion": "alerta_salud",
            "respuesta": "Si te encuentras mal o se trata de una urgencia médica, por favor llama inmediatamente al 112 o pide ayuda a una persona cercana.",
            "accion_ui": "mostrar_alerta_urgencia"
        }

    # RAMA: «Quiero volver» -> ruta de vuelta adaptada (acompañamiento libre)
    if analisis.intencion == "volver":
        datos = peticion.perfil_actual.model_dump() if peticion.perfil_actual else {}
        if analisis.cansado:
            datos["cansado"] = True
        try:
            vuelta = acompanante.volver(peticion.lat, peticion.lon, Perfil(**datos), peticion.contexto)
        except (ValueError, nx.NetworkXNoPath) as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {
            "intencion": "volver",
            "respuesta": vuelta["explicacion"],
            "ruta": vuelta["ruta"],
            "alternativa_transporte": vuelta["alternativa_transporte"],
            "contexto": vuelta["contexto"],
            "accion_ui": "mostrar_ruta_vuelta",
        }

    # 3. RAMA 2: Búsqueda de Recursos Específicos (/atajo)
    elif analisis.intencion == "buscar_recurso" and analisis.tipo_recurso:
        try:
            recurso = motor.mas_cercano(peticion.lat, peticion.lon, analisis.tipo_recurso)
            nombre = recurso.get("nombre") or f"un punto de {analisis.tipo_recurso}"

            donde = f" ({recurso['direccion']})" if recurso.get("direccion") else ""
            respuesta_texto = (
                f"He localizado {nombre}{donde} a unos {recurso['distancia_m']} metros de tu ubicación "
                f"(aproximadamente {recurso['minutos']} minutos a pie)."
            )
            return {
                "intencion": "buscar_recurso",
                "tipo_recurso": analisis.tipo_recurso,
                "respuesta": respuesta_texto,
                "detalle_recurso": recurso,
                "accion_ui": "marcar_en_mapa"
            }
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    # 4. RAMA 3: ir a un sitio concreto. La interfaz no tiene selector de destino: se dice con claridad
    elif analisis.intencion == "calcular_ruta":
        return {
            "intencion": "calcular_ruta",
            "respuesta": ("Todavía no puedo llevarte a un sitio concreto. Sí puedo buscarte lo más cercano "
                          "(un banco, una farmacia, un centro de salud, una fuente o el transporte), "
                          "acompañarte en el paseo o llevarte de vuelta al punto de inicio."),
            "accion_ui": "mostrar_opciones",
        }

    # 5. RAMA 4: Estado de Fatiga / Conversación General
    else:
        # Dice cómo se encuentra (cansancio, calor, mareo, piernas): responde el acompañante con datos,
        # igual que durante el paseo
        estado = analisis.estado or ("cansado" if analisis.cansado else None)
        if estado == "mejor":
            return {"intencion": "estado", "estado": "mejor",
                    "respuesta": "Me alegro mucho. Aquí sigo por si necesitas algo.",
                    "accion_ui": "mostrar_aviso_acompanante"}
        if estado:
            analisis.estado = estado
            datos = peticion.perfil_actual.model_dump() if peticion.perfil_actual else {}
            r = acompanante.paso(peticion.lat, peticion.lon, Perfil(**datos), peticion.contexto,
                                 estado=analisis.estado)
            return {
                "intencion": "estado",
                "estado": analisis.estado,
                "respuesta": r["aviso"]["mensaje"] if r["aviso"] else "De acuerdo, sigo pendiente.",
                "detalle": r["aviso"],
                "contexto": r["contexto"],
                "accion_ui": "mostrar_aviso_acompanante",
            }
        return {
            "intencion": "conversacion_general",
            "respuesta": "Hola, soy tu acompañante urbano por Chamberí. Puedo buscarte el banco, la farmacia o el centro de salud más cercano, o sugerirte un camino adaptado.",
            "sugerencias_botones": ["Buscar banco", "Buscar farmacia", "Buscar centro de salud", "Calcular ruta"],
            "accion_ui": "mostrar_opciones"
        }