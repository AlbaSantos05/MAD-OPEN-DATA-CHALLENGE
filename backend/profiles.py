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
    sombra: str = "nada"         # NUEVO: preferencia por tramos arbolados/sombra: nada / algo / mucho
    sensible_polen: bool = False # NUEVO: True = preferencia por evitar zonas de alto polen
    cansado: bool = False        # contexto del momento: "estoy cansado"

    def __post_init__(self):
        for campo in ("cuestas", "descanso", "transporte", "sombra"):
            valor = getattr(self, campo)
            if valor not in NIVELES:
                raise ValueError(f"{campo} debe ser 'nada', 'algo' o 'mucho' (recibido: {valor})")

    def pesos(self):
        """Traduce el perfil a los pesos que usa el motor multicriterio."""
        p = {
            "cuesta": NIVELES[self.cuestas],
            "descanso": NIVELES[self.descanso],
            "transporte": NIVELES[self.transporte],
            "sombra": NIVELES[self.sombra],
            "escaleras": 10.0 if self.escaleras else 0.0,
        }
        # Si la persona dice que está cansada, sube temporalmente la importancia
        # de descansar, evitar cuestas y tener transporte cerca (documento 1, apartado 4)
        if self.cansado:
            p["descanso"] += 1.0
            p["cuesta"] += 1.0
            p["transporte"] += 0.5
        return p