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
TipoAtajo = Literal["banco", "farmacia", "fuente", "metro", "bus", "transporte"]
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


class PeticionVolver(BaseModel):
    perfil: PerfilIn
    lat: float
    lon: float
    contexto: Optional[dict] = Field(None, description="Contexto del paseo (incluye 'casa')")


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
    """Ruta recomendada entre dos puntos según el perfil, con su explicación."""
    perfil = Perfil(**peticion.perfil.model_dump())
    try:
        return motor.recomendar_ruta(
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
    """Atajos de un solo toque: banco, farmacia, metro, bus o transporte más cercano."""
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

@app.post("/acompanar")
def acompanar(peticion: PeticionAcompanar):
    """Acompañamiento libre (sin destino): devuelve como mucho UN aviso útil para este paso,
    con su motivo y la fuente de datos, más el contexto actualizado para el paso siguiente."""
    perfil = Perfil(**peticion.perfil.model_dump())
    return acompanante.paso(peticion.lat, peticion.lon, perfil, peticion.contexto,
                            lat_antes=peticion.lat_antes, lon_antes=peticion.lon_antes)


@app.post("/volver")
def volver(peticion: PeticionVolver):
    """«Quiero volver»: ruta adaptada al perfil hasta casa (inicio del paseo) y transporte más cercano."""
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
            "ruta": vuelta["ruta_recomendada"],
            "alternativa_transporte": vuelta["alternativa_transporte"],
            "accion_ui": "mostrar_ruta_vuelta",
        }

    # 3. RAMA 2: Búsqueda de Recursos Específicos (/atajo)
    elif analisis.intencion == "buscar_recurso" and analisis.tipo_recurso:
        try:
            recurso = motor.mas_cercano(peticion.lat, peticion.lon, analisis.tipo_recurso)
            nombre = recurso.get("nombre") or f"un punto de {analisis.tipo_recurso}"
            
            respuesta_texto = (
                f"He localizado {nombre} a unos {recurso['distancia_m']} metros de tu ubicación "
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

    # 4. RAMA 3: Petición de Calcular Ruta (Se solicita selección de destino en mapa)
    elif analisis.intencion == "calcular_ruta":
        # Preservar el perfil guardado o usar uno base sin sobrescribir valores existentes
        perfil_base = peticion.perfil_actual.model_dump() if peticion.perfil_actual else {}
        
        # Aplicar solo las modificaciones indicadas en la frase
        if analisis.cuestas: perfil_base["cuestas"] = analisis.cuestas
        if analisis.descanso: perfil_base["descanso"] = analisis.descanso
        if analisis.escaleras is not None: perfil_base["escaleras"] = analisis.escaleras
        if analisis.transporte: perfil_base["transporte"] = analisis.transporte
        if analisis.cansado: perfil_base["cansado"] = True

        return {
            "intencion": "calcular_ruta",
            "respuesta": "Entendido. Selecciona tu destino en el mapa o elige un punto predefinido para calcular el mejor camino.",
            "perfil_actualizado": perfil_base,
            "accion_ui": "solicitar_destino_mapa"
        }

    # 5. RAMA 4: Estado de Fatiga / Conversación General
    else:
        if analisis.cansado:
            perfil_base = peticion.perfil_actual.model_dump() if peticion.perfil_actual else {}
            perfil_base["cansado"] = True
            perfil_base["descanso"] = "mucho"
            
            return {
                "intencion": "conversacion_general",
                "respuesta": "He actualizado tus prioridades: priorizaré rutas más llanas, con descansos frecuentes y transporte cercano.",
                "perfil_actualizado": perfil_base,
                "sugerencias_botones": ["Buscar banco cercano", "Elegir destino en mapa"],
                "accion_ui": "actualizar_perfil_contexto"
            }
            
        return {
            "intencion": "conversacion_general",
            "respuesta": "Hola, soy tu acompañante urbano por Chamberí. Puedo buscarte el banco o farmacia más cercana, o sugerirte un camino adaptado.",
            "sugerencias_botones": ["Buscar banco", "Buscar farmacia", "Calcular ruta"],
            "accion_ui": "mostrar_opciones"
        }
