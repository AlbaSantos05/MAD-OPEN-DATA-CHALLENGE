"""Servicio de Inteligencia Artificial para interpretación de lenguaje natural.

IMPORTANTE: El LLM actúa ÚNICAMENTE como intérprete de intenciones.
NO calcula rutas, NO consulta bases de datos y NO inventa recomendaciones.
"""
import os
import re
from typing import Literal, Optional
from pydantic import BaseModel, Field


# 1. Esquema Pydantic para Structured Outputs (Salida Estructurada Garantizada)
class IntencionUsuario(BaseModel):
    intencion: Literal["buscar_recurso", "calcular_ruta", "volver", "alerta_salud", "conversacion_general"] = Field(
        ..., description="Propósito principal detectado en el mensaje."
    )
    tipo_recurso: Optional[Literal["banco", "farmacia", "fuente", "metro", "bus", "transporte"]] = Field(
        None, description="Especifica el tipo si la intención es buscar_recurso."
    )
    cuestas: Optional[Literal["nada", "algo", "mucho"]] = Field(
        None, description="Preferencia explícita sobre dificultades con cuestas."
    )
    descanso: Optional[Literal["nada", "algo", "mucho"]] = Field(
        None, description="Preferencia explícita sobre necesidad de descanso."
    )
    escaleras: Optional[bool] = Field(
        None, description="True si expresa deseo explícito de evitar escaleras."
    )
    transporte: Optional[Literal["nada", "algo", "mucho"]] = Field(
        None, description="Preferencia de proximidad a transporte público."
    )
    cansado: bool = Field(
        False, description="True si la persona expresa cansancio o fatiga explícita."
    )


PROMPT_SISTEMA = """
Eres el módulo de comprensión de lenguaje natural para el Acompañante Urbano de Madrid.
Tu ÚNICA tarea es extraer la intención del usuario y clasificarla según el esquema estructurado.

Reglas estrictas de clasificación:
1. ALERTA DE SALUD (Urgencias físicas o médicas severas):
   - Si expresa dolor grave, falta de aire, mareo o malestar severo (ej. "me duele el pecho", "no puedo respirar", "me encuentro muy mal") -> intencion="alerta_salud". NO clasifiques esto como farmacia.
2. BÚSQUEDA DE RECURSOS ESPECÍFICOS:
   - Pedir banco, asiento o lugar para sentarse -> intencion="buscar_recurso", tipo_recurso="banco".
   - Pedir farmacia o comprar medicinas de forma explícita -> intencion="buscar_recurso", tipo_recurso="farmacia".
   - Pedir fuente, agua o beber -> intencion="buscar_recurso", tipo_recurso="fuente".  
   - Pedir autobús, EMT o parada -> intencion="buscar_recurso", tipo_recurso="bus".
   - Pedir metro o boca de metro -> intencion="buscar_recurso", tipo_recurso="metro".
3. VOLVER:
   - "quiero volver", "llévame a casa", "me vuelvo", "regresar" -> intencion="volver".
4. CÁLCULO DE RUTA:
   - Indicar deseo de ir a un sitio, caminar hacia una ubicación o calcular un trayecto -> intencion="calcular_ruta".
5. ESTADO DE FATIGA / CANSANCIO:
   - Expresiones como "estoy cansada", "no puedo más", "me fatigo" -> cansado=True y descanso="mucho".
6. EVITAR ESCALERAS:
   - Expresiones como "sin escaleras", "no puedo subir escalones" -> escaleras=True.
"""


def analizar_mensaje_llm(texto_usuario: str) -> IntencionUsuario:
    """Interpreta la intención del usuario usando Structured Outputs.
    
    Si no hay API key configurada o falla la llamada, salta directamente al fallback determinista.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    
    # Si no hay clave de API válida, ejecutamos directamente el fallback
    if api_key:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=api_key)

            # Uso de Structured Outputs con parse() para adherencia 100% al esquema Pydantic
            completion = client.beta.chat.completions.parse(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": PROMPT_SISTEMA},
                    {"role": "user", "content": texto_usuario}
                ],
                response_format=IntencionUsuario,
                temperature=0.0
            )
            return completion.choices[0].message.parsed

        except Exception as e:
            print(f"Aviso en conexión LLM ({e}). Activando clasificador básico por código.")

    # FALLBACK DETERMINISTA (Garantiza que funciona siempre en local/demo sin internet ni claves)
    txt = texto_usuario.lower()

    def hay(*palabras):
        """True si aparece alguna palabra o expresión COMPLETA (evita que «busca» cuente como «bus»)."""
        return any(re.search(rf"\b{re.escape(p)}", txt) for p in palabras)
    
    # 1. Chequeo de emergencias de salud
    if any(p in txt for p in ["me duele", "pecho", "falta el aire", "maread", "me encuentro mal", "urgencia"]):
        return IntencionUsuario(intencion="alerta_salud")
        
    # 2. Volver
    if hay("volver", "vuelvo", "vuelta", "a casa", "regresar", "regreso"):
        return IntencionUsuario(intencion="volver",
                                cansado=hay("cansad", "agotad", "no puedo más"))

    # 3. Chequeo de recursos
    if hay("banco", "sentar", "descans"):
        return IntencionUsuario(
            intencion="buscar_recurso", 
            tipo_recurso="banco", 
            cansado=("cansad" in txt or "agotad" in txt),
            descanso="mucho" if ("cansad" in txt or "agotad" in txt) else None
        )
    elif hay("farmacia", "medicin", "remedio"):
        return IntencionUsuario(intencion="buscar_recurso", tipo_recurso="farmacia")
    elif hay("fuente", "agua", "beber", "sed"):
        return IntencionUsuario(intencion="buscar_recurso", tipo_recurso="fuente")
    elif re.search(r"\b(bus|autob[uú]s|emt)\b", txt):
        return IntencionUsuario(intencion="buscar_recurso", tipo_recurso="bus")
    elif re.search(r"\bmetro\b", txt):
        return IntencionUsuario(intencion="buscar_recurso", tipo_recurso="metro")
    
    # 4. Ir a un sitio
    if hay("llévame", "llevame", "ir a ", "ir al ", "quiero ir", "ruta", "camino a", "cómo llego", "como llego"):
        return IntencionUsuario(intencion="calcular_ruta",
                                escaleras=True if hay("sin escaleras", "escalones") else None)

    # 5. Solo cansancio expresado
    if hay("cansad", "agotad", "no puedo más", "fatig"):
        return IntencionUsuario(intencion="conversacion_general", cansado=True, descanso="mucho")

    return IntencionUsuario(intencion="conversacion_general")