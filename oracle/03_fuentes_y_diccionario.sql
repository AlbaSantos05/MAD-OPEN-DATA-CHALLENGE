-- =====================================================================
-- 03 · Catálogo de fuentes y diccionario de datos
-- Ejecutar como MADDATA ANTES de cargar datos (POI y TRAMOS apuntan a FUENTES).
-- Revisad URL, licencia y fecha de descarga de cada fila antes de la entrega.
-- =====================================================================

-- ---------------------------------------------------------------- FUENTES
INSERT INTO fuentes VALUES ('CRTM_GTFS_METRO', 'GTFS Metro de Madrid (modo 4): estaciones y accesos', 'CRTM',
  'Datos abiertos CRTM', 'https://datos.crtm.es/', 'Ver condiciones del portal', 'GTFS (txt)', DATE '2026-10-14',
  'Accesos de Metro: distancia por la red al transporte y accesibilidad de la estación (wheelchair_boarding).',
  'El feed descargado (google_transit_M4) es de mayo de 2025: actualizar. La mayoría de estaciones no informa accesibilidad.');

INSERT INTO fuentes VALUES ('CRTM_GTFS_EMT', 'GTFS autobús urbano EMT (modo 6) publicado por el CRTM', 'CRTM',
  'Datos abiertos CRTM', 'https://datos.crtm.es/', 'Ver condiciones del portal', 'GTFS (txt)', DATE '2026-10-14',
  'Paradas de autobús: distancia por la red al transporte.',
  'El feed no incluye accesibilidad de las paradas.');

INSERT INTO fuentes VALUES ('CM_FARMACIAS', 'Oficinas de farmacia de la Comunidad de Madrid', 'CM',
  'datos.comunidad.madrid', 'https://datos.comunidad.madrid/dataset/oficinas_farmacia', 'Ver condiciones del portal',
  'CSV', DATE '2026-10-14',
  'Distancia por la red a la farmacia más cercana y atajo "farmacia".',
  'No se carga el nombre del titular (dato personal innecesario). No incluye horarios ni guardias.');

INSERT INTO fuentes VALUES ('CM_POLEN', 'Histórico de mediciones de polen', 'CM',
  'datos.comunidad.madrid', 'https://datos.comunidad.madrid/dataset/mediciones_polen_historico', 'Ver condiciones del portal',
  'CSV', NULL,
  'Calendario medio de polen por tipo y mes (POLEN_MES), cruzado con las especies del arbolado.',
  'Patrón estacional histórico: no describe el polen de un día concreto ni de una calle concreta.');

INSERT INTO fuentes VALUES ('CM_METEO', 'Datos meteorológicos históricos de la Red de Calidad del Aire', 'CM',
  'datos.comunidad.madrid', 'https://datos.comunidad.madrid/dataset/calidad_aire_datos_meteo_historico', 'Ver condiciones del portal',
  'CSV', NULL,
  'Temperatura media por mes y hora (CLIMA_MES_HORA) para ajustar los pesos según la época y la hora del paseo.',
  'Valores de estación de medida: no representan cada calle.');

INSERT INTO fuentes VALUES ('CM_ESTACIONES', 'Estaciones de la Red de Calidad del Aire', 'CM',
  'datos.comunidad.madrid', 'https://datos.comunidad.madrid/dataset/calidad_aire_estaciones', 'Ver condiciones del portal',
  'CSV', NULL,
  'Ubicación de las estaciones, para asignar a cada tramo la estación más cercana.', NULL);

INSERT INTO fuentes VALUES ('AYTO_BANCOS', 'Mobiliario urbano: bancos', 'AYTO',
  'datos.madrid.es', 'https://datos.madrid.es/dataset/300095-0-mobiliario-bancos', 'Ver condiciones del portal',
  'CSV', DATE '2026-10-14',
  'Bancos a menos de 20 m de cada tramo y distancia por la red al banco más cercano.',
  'Solo bancos en estado OPERATIVO. No informa del estado de conservación.');

INSERT INTO fuentes VALUES ('AYTO_FUENTES', 'Fuentes de agua para beber', 'AYTO',
  'datos.madrid.es', 'https://datos.madrid.es/dataset/300051-0-fuentes', 'Ver condiciones del portal',
  'CSV', NULL, 'Distancia por la red a la fuente más cercana.', 'Pendiente de descargar.');

INSERT INTO fuentes VALUES ('AYTO_ARBOLADO', 'Arbolado municipal (especies)', 'AYTO',
  'datos.madrid.es', 'https://datos.madrid.es/dataset/300761-0-arbolado-especies', 'Ver condiciones del portal',
  'CSV', NULL, 'Árboles a menos de 15 m de cada tramo y su especie (cruce con polen).',
  'Presencia de árboles no equivale a sombra garantizada. Pendiente de descargar.');

INSERT INTO fuentes VALUES ('OSM_RED', 'Red peatonal de OpenStreetMap (OSMnx, network_type=walk)', 'OSM',
  'openstreetmap.org', 'https://www.openstreetmap.org/', 'ODbL 1.0', 'API Overpass', DATE '2026-10-14',
  'Geometría de los tramos, nombre de la vía, escaleras (highway=steps), puentes y túneles.',
  'Fuente colaborativa: puede faltar alguna escalera o camino.');

INSERT INTO fuentes VALUES ('EU_DEM_25M', 'Elevación EU-DEM 25 m vía Open Topo Data', 'OTRO',
  'opentopodata.org', 'https://www.opentopodata.org/datasets/eudem/', 'Ver condiciones de Copernicus', 'API JSON', DATE '2026-10-14',
  'Altitud de cada cruce para estimar la pendiente de los tramos.',
  'Resolución de 25 m: en tramos de menos de 15 m la pendiente se fija a 0. Sustituir por el MDT del Ayuntamiento si da tiempo.');

-- ---------------------------------------------------------------- DICCIONARIO (TRAMOS)
INSERT INTO diccionario VALUES ('TRAMOS','TRAMO_ID','Identificador del tramo (segmento físico de calle)',NULL,'OSM_RED',
  'Una fila por segmento: OSM da cada calle en los dos sentidos y nos quedamos con uno.');
INSERT INTO diccionario VALUES ('TRAMOS','NODO_U','Cruce de un extremo (id de nodo OSM, el menor de los dos)',NULL,'OSM_RED',NULL);
INSERT INTO diccionario VALUES ('TRAMOS','NODO_V','Cruce del otro extremo (id de nodo OSM, el mayor de los dos)',NULL,'OSM_RED',NULL);
INSERT INTO diccionario VALUES ('TRAMOS','NOMBRE_VIA','Nombre de la calle (vacío en aceras y pasos sin nombre)',NULL,'OSM_RED',NULL);
INSERT INTO diccionario VALUES ('TRAMOS','TIPO_VIA','Tipo de vía OSM (footway, residential, steps...)',NULL,'OSM_RED',NULL);
INSERT INTO diccionario VALUES ('TRAMOS','LENGTH_M','Longitud del tramo','m','OSM_RED','Longitud geodésica calculada por OSMnx.');
INSERT INTO diccionario VALUES ('TRAMOS','SLOPE_PCT','Pendiente media absoluta del tramo','%','EU_DEM_25M',
  '|altitud(v) - altitud(u)| / longitud x 100. Tope 12 %. Tramos de menos de 15 m = 0 (el modelo de 25 m no los resuelve).');
INSERT INTO diccionario VALUES ('TRAMOS','STEPS','Si el tramo es una escalera',NULL,'OSM_RED','si cuando highway=steps.');
INSERT INTO diccionario VALUES ('TRAMOS','BENCHES_20M','Bancos a menos de 20 m del tramo','bancos','AYTO_BANCOS',
  'Bancos dentro de un buffer de 20 m alrededor de la línea del tramo (EPSG:25830).');
INSERT INTO diccionario VALUES ('TRAMOS','DIST_BENCH_M','Distancia andando al banco más cercano','m','AYTO_BANCOS',
  'Dijkstra por la red peatonal desde el extremo más cercano del tramo. El banco se engancha al cruce más próximo (máx. 150 m).');
INSERT INTO diccionario VALUES ('TRAMOS','DIST_TRANSPORT_M','Distancia andando al acceso de Metro o parada de bus más cercano','m','CRTM_GTFS_METRO',
  'Igual que DIST_BENCH_M, sobre accesos de Metro (CRTM) y paradas EMT (CRTM).');
INSERT INTO diccionario VALUES ('TRAMOS','NEAREST_STOP_ID','Acceso o parada que da esa distancia',NULL,'CRTM_GTFS_METRO','Clave de POI.');
INSERT INTO diccionario VALUES ('TRAMOS','STOP_ACCESSIBLE','Si esa estación es accesible',NULL,'CRTM_GTFS_METRO',
  'wheelchair_boarding de la estación padre: 1 = si, 2 = no, 0 o sin dato = desconocido.');
INSERT INTO diccionario VALUES ('TRAMOS','DIST_PHARMACY_M','Distancia andando a la farmacia más cercana','m','CM_FARMACIAS',
  'Dijkstra por la red peatonal, como DIST_BENCH_M.');
INSERT INTO diccionario VALUES ('TRAMOS','DIST_WATER_M','Distancia andando a la fuente de agua más cercana','m','AYTO_FUENTES',
  'Vacía hasta descargar el dataset de fuentes.');
INSERT INTO diccionario VALUES ('TRAMOS','TREES_20M','Árboles a menos de 15 m del tramo','árboles','AYTO_ARBOLADO',
  'Vacía hasta descargar el arbolado. No equivale a sombra.');
INSERT INTO diccionario VALUES ('TRAMOS','QUALITY_FLAGS','Avisos de calidad separados por punto y coma',NULL,NULL,
  'pendiente_eudem25m, tramo_corto_pendiente_0, pendiente_recortada_12, puente_o_tunel.');
INSERT INTO diccionario VALUES ('TRAMOS','GEOM','Geometría del tramo (línea)','WGS84',NULL,'SRID 4326.');

-- ---------------------------------------------------------------- DICCIONARIO (POI)
INSERT INTO diccionario VALUES ('POI','POI_ID','Identificador del recurso: prefijo de fuente + id original',NULL,NULL,
  'BANCO_, FARM_, CRTM_, EMT_, FUENTE_, ARBOL_.');
INSERT INTO diccionario VALUES ('POI','TIPO','Tipo de recurso',NULL,NULL,'banco, farmacia, metro, bus, fuente, arbol...');
INSERT INTO diccionario VALUES ('POI','ACCESIBLE','Accesibilidad declarada en la fuente',NULL,'CRTM_GTFS_METRO','Solo Metro la informa.');
INSERT INTO diccionario VALUES ('POI','GEOM','Ubicación del recurso (punto)','WGS84',NULL,
  'Recursos de la zona piloto y de 500 m alrededor, para no perder los que están al otro lado del límite.');
COMMIT;
