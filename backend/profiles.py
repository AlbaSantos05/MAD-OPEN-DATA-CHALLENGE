"""Perfil del usuario: necesidades y preferencias que ajustan las recomendaciones."""
from dataclasses import dataclass

NIVELES = {"nada": 0.0, "algo": 1.0, "mucho": 2.0}


@dataclass
class Perfil:
    nombre: str = "Usuario"
    cuestas: str = "nada"        # dificultad con cuestas: nada / algo / mucho
    descanso: str = "nada"       # necesidad de bancos para descansar: nada / algo / mucho
    escaleras: bool = False      # True = evitar escaleras
    transporte: str = "nada"     # interés en estar cerca de metro/bus: nada / algo / mucho
    arbolado: str = "nada"       # preferencia por tramos con árboles cerca: nada / algo / mucho
                                 # (árboles cerca no garantiza sombra)
    sensible_polen: bool = False # NUEVO: True = preferencia por evitar zonas de alto polen
    cansado: bool = False        # contexto del momento: "estoy cansado"

    def __post_init__(self):
        for campo in ("cuestas", "descanso", "transporte", "arbolado"):
            valor = getattr(self, campo)
            if valor not in NIVELES:
                raise ValueError(f"{campo} debe ser 'nada', 'algo' o 'mucho' (recibido: {valor})")
