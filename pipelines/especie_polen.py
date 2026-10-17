"""Relaciona cada especie del arbolado de Chamberí con su tipo de polen.

Entrada : data/dataset/poi.geojson  (árboles: tipo = "arbol", campo "especie", nombre científico)
Salida  : data/processed/especie_polen.csv -> columnas de la tabla Oracle ESPECIE_POLEN
          (especie, tipo_polen)

Método: se asigna por GÉNERO (primera palabra del nombre científico), porque los
captadores identifican el polen por género o familia, no por especie. Los nombres de
tipo_polen son exactamente los de polen.csv / polen_mes.csv, para que el cruce funcione.

Solo se incluyen los géneros cuyo polen mide la red y tiene umbral oficial
(ver pipelines/calcular_polen.py). El resto de especies no se asocia a ningún polen.

Ejecutar desde la raíz del proyecto:
    python pipelines/especie_polen.py
"""
import json
from collections import Counter
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent.parent
ENTRADA = BASE / "data" / "dataset" / "poi.geojson"
SALIDA = BASE / "data" / "processed" / "especie_polen.csv"

# Género (en mayúsculas, como en el inventario del Ayuntamiento) -> tipo de polen de polen.csv
GENERO_A_POLEN = {
    "PLATANUS": "Plátano de paseo",
    # Cupresáceas y taxáceas (cipreses, enebros, tuyas, tejos, secuoyas...)
    "CUPRESSUS": "Cupresáceas/Taxáceas", "JUNIPERUS": "Cupresáceas/Taxáceas",
    "THUJA": "Cupresáceas/Taxáceas", "PLATYCLADUS": "Cupresáceas/Taxáceas",
    "CHAMAECYPARIS": "Cupresáceas/Taxáceas", "CALOCEDRUS": "Cupresáceas/Taxáceas",
    "TAXUS": "Cupresáceas/Taxáceas", "SEQUOIA": "Cupresáceas/Taxáceas",
    "SEQUOIADENDRON": "Cupresáceas/Taxáceas", "TAXODIUM": "Cupresáceas/Taxáceas",
    "METASEQUOIA": "Cupresáceas/Taxáceas", "CRYPTOMERIA": "Cupresáceas/Taxáceas",
    "QUERCUS": "Quercus",
    "OLEA": "Olivo",
    # «Pinos y cedros» en el informe del Ayuntamiento
    "PINUS": "Pinos", "CEDRUS": "Pinos",
    "POPULUS": "Populus",
    "ULMUS": "Olmos",
    "ACER": "Arce",
    "FRAXINUS": "Fresno",
    "MORUS": "Moreras", "BROUSSONETIA": "Moreras",
}


def main():
    datos = json.loads(ENTRADA.read_text(encoding="utf-8"))
    especies = Counter(
        f["properties"].get("especie")
        for f in datos["features"]
        if f["properties"].get("tipo") == "arbol" and f["properties"].get("especie")
    )

    filas = []
    for especie in especies:
        genero = especie.strip().split()[0].upper()
        if genero in GENERO_A_POLEN:
            filas.append({"especie": especie, "tipo_polen": GENERO_A_POLEN[genero]})

    tabla = pd.DataFrame(filas).sort_values(["tipo_polen", "especie"])
    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    tabla.to_csv(SALIDA, index=False, encoding="utf-8")

    # Resumen por pantalla
    total = sum(especies.values())
    por_tipo = Counter()
    for _, f in tabla.iterrows():
        por_tipo[f["tipo_polen"]] += especies[f["especie"]]
    asociados = sum(por_tipo.values())
    print(f"{len(especies)} especies, {total} árboles en Chamberí")
    print(f"Guardadas {len(tabla)} especies en {SALIDA.relative_to(BASE)}")
    print(f"Árboles asociados a un tipo de polen: {asociados} ({asociados / total:.0%})\n")
    for tipo, n in por_tipo.most_common():
        print(f"  {tipo:<24} {n:>6} árboles")


if __name__ == "__main__":
    main()