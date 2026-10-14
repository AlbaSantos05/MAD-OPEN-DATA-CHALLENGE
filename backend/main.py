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
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

# Para poder importar recommendation.py y profiles.py estando en backend/
sys.path.insert(0, str(Path(__file__).resolve().parent))
from recommendation import Motor, DATOS  # noqa: E402
from profiles import Perfil  # noqa: E402

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

Nivel = Literal["nada", "algo", "mucho"]
TipoAtajo = Literal["banco", "farmacia", "metro", "bus", "transporte"]
CAPAS = {"calles", "bancos", "farmacias", "metro", "bus"}


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
