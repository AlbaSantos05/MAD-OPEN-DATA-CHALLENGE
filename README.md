# MADcompaña · Acompañante urbano para personas mayores

**MAD Open Data Challenge 2026** · Comunidad de Madrid · UC3M

MADcompaña acompaña a una persona mayor mientras pasea por Madrid. No es un mapa ni un buscador de rutas: la persona sale a caminar **sin destino** y la aplicación hace lo que haría alguien que va a su lado. Mira lo que hay por delante, avisa solo cuando hace falta y adapta cada consejo a su perfil y a lo que cuenta durante el paseo («estoy cansada», «tengo calor», «me estoy mareando»).

Todo se construye con **datos abiertos** de la Comunidad de Madrid, el Ayuntamiento, el CRTM y OpenStreetMap, integrados en un **dataset propio** de la red peatonal. El prototipo cubre **Chamberí**, pero el código no está atado al distrito: basta con regenerar el dataset para otra zona.

> **Demo**
> - Interfaz (Oracle APEX): _enlace pendiente_
> - API (FastAPI en Render): _URL pendiente_ · documentación interactiva en `/docs`
> - API de datos (Oracle ORDS, solo lectura): `https://g65cd3d29f3f6ee-madcompanadb.adb.eu-madrid-1.oraclecloudapps.com/ords/maddata/dataset/`

---

## Qué hace

**El mismo barrio cambia según la persona.** Para cada persona, cada tramo de calle se clasifica por sus **motivos**. El perfil (cuestas, descanso, escaleras, transporte, arbolado, polen), lo que ha dicho durante el paseo, el mes y la hora deciden qué motivos cuentan y con qué umbral.

| Categoría | Cuándo | Ejemplos de motivo |
|---|---|---|
| no apta (barrera) | Algo que para esa persona impide pasar | Escaleras si las evita; cuesta fuerte (desde el 5 % si le cuestan mucho) |
| regular (incomodidad) | Algo que le resulta incómodo | Cuesta moderada, banco lejos, parada lejos, 5 o más árboles con polen medio o alto ese mes, falta de árboles y de fuentes con calor |
| apta | Ningún motivo | — |

Ejemplo en abril: para Carmen (le cuestan mucho las cuestas, necesita descansar, evita escaleras, es alérgica) hay en Chamberí 526 tramos no aptos y 1.103 regulares. Para Antonio (sin dificultades) no hay ningún tramo no apto y solo 120 regulares.

**Acompañamiento paso a paso.** En cada posición, el sistema:

1. sitúa a la persona en la red peatonal y deduce hacia dónde va;
2. mira unos 150 m por delante y las salidas del siguiente cruce;
3. revisa un radar de 120 m alrededor (zona actual, lo que viene y tramos no aptos cerca);
4. decide **como mucho un aviso**, por orden de prioridad, con un mínimo de 40 m entre avisos. Cada tipo de aviso tiene su distancia de espera, y de un mismo tramo solo se avisa una vez.

| Prioridad | Aviso |
|---|---|
| 100 | Alerta de salud (síntomas graves → 112) |
| 96 | Llegada (en modo destino) |
| 95 | Respuesta a lo que acaba de decir la persona |
| 90 | Barrera por delante, con la salida alternativa |
| 70 | Cansancio: distancia andada y transporte para volver |
| 62 | Incomodidad por delante (cuesta, polen…), con alternativa |
| 60 | Dónde descansar |
| 30 / 25 / 20 | Agua / centro de salud cercano / inicio del paseo |

**Lo que dice la persona cambia el paseo.** Si cuenta que está cansada, que tiene calor, que se marea o que le duelen las piernas, recibe una respuesta inmediata (por ejemplo: sentarse, el banco más cercano, el centro de salud y «si va a más, llama al 112»), y los umbrales se endurecen durante los pasos siguientes. Con «ya estoy mejor» vuelven a la normalidad. Con «quiero volver» se calcula la vuelta a casa andando o en transporte.

**Cada aviso explica por qué**, con el dato y el organismo que lo publica. Ejemplo real del simulador:

> *Carmen, más adelante, en Calle de Cea Bermúdez, hay 6 árboles cuyo polen suele estar alto en abril (sobre todo plátano de paseo). Si prefieres, a la derecha está mejor.*

**Capa diagnóstica para la administración** (`pipelines/capa_diagnostica.py`). Divide el distrito en zonas de 200 m y las puntúa de 0 a 100 según su comodidad para una persona mayor: bancos, transporte, farmacia, centro de salud, pendiente, agua y escaleras. Señala el punto débil de cada zona. En Chamberí salen 135 zonas, con una media de 88 y una mínima de 37; los puntos débiles más frecuentes son el centro de salud lejano y la falta de fuentes cerca.

## Arquitectura

```
Datos abiertos (CM, Ayto., CRTM, OSM, EU-DEM)
        │  data/raw/
        ▼
pipelines/  ──►  dataset propio: data/dataset/tramos.geojson (5.082 tramos, 199 km)
        │                         data/dataset/poi.geojson    (bancos, farmacias, fuentes, metro, bus, árboles)
        │                         data/processed/*.csv         (polen, aire, especies, centros de salud)
        ▼
Oracle Autonomous Database (esquema MADDATA)  ──►  API REST ORDS de solo lectura (/dataset/)
        │
        ▼
backend/ (FastAPI, en Render)  ── lee la API de Oracle; con copia local de respaldo
        │
        ▼
Interfaz Oracle APEX
```

| Carpeta | Contenido |
|---|---|
| `data/raw/` | Descargas originales de los portales de datos abiertos |
| `data/processed/` | Datos limpios y tablas derivadas (polen por mes, aire por mes y hora, especie → polen, centros de salud, zonas diagnósticas) |
| `data/dataset/` | El dataset propio: red peatonal enriquecida y puntos de interés |
| `pipelines/` | Limpieza, construcción del dataset, cálculos ambientales, capa diagnóstica y carga en Oracle |
| `oracle/` | Scripts SQL (usuario, tablas, catálogo de fuentes, diccionario, vistas de calidad, API REST). Guía en [`oracle/README.md`](oracle/README.md) |
| `backend/` | Motor de rutas, acompañante, interpretación de mensajes y API |
| `docs/propuesta/` | Propuesta presentada: aproximación técnica, fuentes de datos e interfaz |

## El dataset

Cada **tramo** de la red peatonal lleva, entre otros: pendiente (%), si es escalera, bancos a menos de 20 m y distancia andando al banco más cercano, distancia andando a metro o bus (y si la parada es accesible), a farmacia y a fuente, árboles a menos de 15 m, y avisos de calidad del dato. La cobertura de todos los campos es del 100 % salvo el nombre de la vía. El catálogo de fuentes (`FUENTES`) y el diccionario de datos (`DICCIONARIO`) están en la propia base de datos y se consultan por la API.

| Fuente | Organismo | Uso |
|---|---|---|
| Oficinas de farmacia | Comunidad de Madrid | Farmacia más cercana (sin nombre del titular) |
| Histórico de mediciones de polen (Red Palinológica) | Comunidad de Madrid | Calendario de polen por tipo y mes |
| Centros de salud (IDEM) | Comunidad de Madrid | Centro de salud más cercano, avisos |
| Calidad del aire, datos horarios | Ayuntamiento de Madrid | Índice de calidad del aire (ICA) habitual por mes y hora |
| Bancos (mobiliario urbano) | Ayuntamiento de Madrid | Descanso |
| Fuentes de agua para beber | Ayuntamiento de Madrid | Agua |
| Arbolado (especies) | Ayuntamiento de Madrid | Arbolado y cruce especie → polen |
| GTFS Metro y EMT | CRTM | Paradas, distancia andando, accesibilidad |
| Red peatonal | OpenStreetMap (ODbL) | Geometría, nombres, escaleras |
| Elevación EU-DEM 25 m | Copernicus (vía Open Topo Data) | Pendiente de cada tramo |

## Decisiones de diseño

- **Solo datos abiertos y estáticos.** No hay datos en tiempo real: polen y aire se dan como patrón histórico («lo habitual en abril a esta hora»), nunca como «hoy hace…». La aplicación solo habla del tiempo actual si lo ha dicho la persona.
- **El LLM solo interpreta.** Un modelo de lenguaje (con salida estructurada y respaldo determinista por palabras clave) entiende lo que dice la persona. Qué aconsejar lo deciden siempre los datos y reglas explícitas, así que cada aviso es explicable.
- **Prudencia con lo que los datos no dicen.** Que haya árboles no garantiza sombra, y que haya farolas no garantiza seguridad; nunca se afirma ninguna de las dos cosas.
- **Minimización de datos.** No se cargan nombres de titulares de farmacias. El servidor no guarda estado: el contexto del paseo viaja con cada petición.
- **Escalable.** Nada está fijado a Chamberí: las descargas se recortan a partir de la red del dataset, así que regenerarlo para otra zona es volver a ejecutar los pipelines.

## Cómo ejecutarlo

Requisitos: Python 3.10 o superior.

```bash
pip install -r requirements.txt

# API (desde la raíz del proyecto; desde backend/ falla el import)
uvicorn backend.main:app --reload
# → http://127.0.0.1:8000/docs

# Pruebas del acompañante (35 comprobaciones), con los datos locales
DATOS_FUENTE=local python backend/test_acompanamiento.py

# Paseo simulado: Carmen frente a Antonio en abril
DATOS_FUENTE=local python backend/simular_paseo.py --mes 4
```

Por defecto el backend lee los datos de la API de Oracle y, si no responde, usa la copia de `data/`. Para `/chat` con interpretación por LLM hace falta `OPENAI_API_KEY`; sin ella se usa el respaldo por palabras clave. Para regenerar el dataset o cargarlo en Oracle, copia `.env.example` a `.env` y sigue [`oracle/README.md`](oracle/README.md).

### Endpoints principales

| Método | Ruta | Para qué |
|---|---|---|
| POST | `/acompanar` | Un paso del paseo libre: como mucho un aviso, con su motivo y fuente, y el contexto para el paso siguiente. Campo opcional `dice` |
| POST | `/chat` | Mensaje en lenguaje natural (estados, «quiero volver», atajos) |
| POST | `/mapa_personal` | GeoJSON de las calles coloreadas por idoneidad para ese perfil |
| POST | `/volver` | Vuelta al inicio andando, con transporte cercano |
| POST | `/ir_a` | Modo destino: se recalcula en cada paso |
| POST | `/ruta` | Ruta más corta andando, descrita para el perfil |
| GET | `/atajo/{tipo}` | Banco, farmacia, fuente, metro, bus o centro de salud más cercano |
| GET | `/ambiente` | Polen del mes y calidad del aire habitual a esa hora |
| GET | `/paseo_demo` | Puntos de un paseo real por Chamberí para la demostración |
| GET | `/salud` | Estado de la API |

## Limitaciones conocidas

- El polen y el aire son medias históricas de estaciones de medida: no describen un día ni una calle concretos.
- OpenStreetMap es colaborativo y puede faltar alguna escalera o camino.
- La accesibilidad de las paradas depende de lo que informe el GTFS, que en la mayoría de estaciones no la detalla.
- El prototipo cubre Chamberí; extenderlo a todo Madrid es cuestión de volver a ejecutar los pipelines.

## Equipo

Alba, Lucía y Antía · Grado en Ciencia de Datos e Inteligencia Artificial
