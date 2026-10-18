-- =====================================================================
-- 08 · Coherencia de FUENTES y DICCIONARIO con lo que hay de verdad en Oracle
-- Ejecutar como MADDATA (APEX › SQL Workshop › SQL Scripts, o Database Actions con F5),
-- DESPUÉS de 06 y 07. Se puede repetir: los UPDATE y MERGE dejan siempre el mismo resultado.
--
-- Fechas de descarga = fecha en que cada fichero entró en el repositorio (git log).
-- Nota: en APEX los comentarios van en su propia línea (al final de una sentencia dan ORA-03405).
-- =====================================================================

-- ---------------------------------------------------------------- FUENTES usadas
-- Fuentes de agua: ya cargadas (96 en la zona)
UPDATE fuentes SET
  fecha_descarga  = DATE '2026-10-16',
  uso_en_proyecto = 'Fuentes de agua para beber de la zona y 500 m alrededor (POI tipo fuente) y distancia por la red a la más cercana (TRAMOS.DIST_WATER_M).',
  limitaciones    = 'El inventario trae un campo ESTADO (operativa, fuera de servicio, cerrada temporalmente) que el dataset todavía no filtra: puede incluir fuentes que no funcionan.'
WHERE fuente_id = 'AYTO_FUENTES';

-- Arbolado: ya cargado (28.760 árboles en la zona)
UPDATE fuentes SET
  fecha_descarga  = DATE '2026-10-16',
  uso_en_proyecto = 'Árboles a menos de 15 m de cada tramo (TRAMOS.TREES_20M) y su especie (POI.ESPECIE), cruzada con el calendario de polen (ESPECIE_POLEN).',
  limitaciones    = 'Presencia de árboles no equivale a sombra garantizada. Solo el 31 % de los árboles pertenece a un género cuyo polen mide la red (la sófora, la especie más abundante, no se mide).'
WHERE fuente_id = 'AYTO_ARBOLADO';

-- Polen: histórico 2024-2026 de los dos captadores de Madrid capital
UPDATE fuentes SET
  fecha_descarga  = DATE '2026-10-17',
  formato         = 'CSV (un fichero por año)',
  uso_en_proyecto = 'Calendario de polen por tipo y mes (POLEN_MES): media diaria de los captadores AYTM (Arganzuela) y FACF (Ciudad Universitaria) de 2024 a 2026, clasificada en bajo, medio o alto con los umbrales oficiales del Ayuntamiento de Madrid (percentiles PALINOCAM 2010-2019). Cruzado con las especies del arbolado por género (ESPECIE_POLEN).',
  limitaciones    = 'Patrón estacional histórico: no describe el polen de un día ni de una calle concretos. Los umbrales son diarios y se aplican a medias mensuales, que suavizan los picos. 15 tipos con umbral oficial.'
WHERE fuente_id = 'CM_POLEN';

-- ---------------------------------------------------------------- FUENTES evaluadas y descartadas
UPDATE fuentes SET
  fecha_descarga  = DATE '2026-10-16',
  uso_en_proyecto = 'Evaluada y descartada.',
  limitaciones    = 'Comprobado en data/raw/meteorologia.csv: solo magnitudes meteorológicas (81-89) y ningún registro del municipio de Madrid (079). La red de la CM no tiene estaciones en la capital. El fichero se conserva en el repositorio como prueba.'
WHERE fuente_id = 'CM_METEO';

UPDATE fuentes SET
  fecha_descarga  = DATE '2026-10-17',
  uso_en_proyecto = 'Evaluada y descartada.',
  limitaciones    = 'No se descargó: la red de calidad del aire de la CM no tiene estaciones en Madrid capital. Se usa la estación 38 (Cuatro Caminos) de la red municipal (AYTO_CALIDAD_AIRE). La fecha es la de la evaluación.'
WHERE fuente_id = 'CM_ESTACIONES';

COMMIT;

-- ---------------------------------------------------------------- DICCIONARIO
-- MERGE: actualiza la fila si existe y la crea si no
MERGE INTO diccionario d
USING (
  SELECT 'TRAMOS' tabla, 'DIST_TRANSPORT_M' columna,
         'Distancia andando al acceso de Metro o parada de autobús EMT más cercano' descripcion, 'm' unidad,
         'CRTM_GTFS_METRO' fuente_id,
         'Dijkstra por la red peatonal, como DIST_BENCH_M, sobre accesos de Metro (CRTM_GTFS_METRO) y paradas de autobús EMT (CRTM_GTFS_EMT). La fuente de cada caso está en POI.FUENTE_ID de NEAREST_STOP_ID.' metodo FROM dual UNION ALL
  SELECT 'TRAMOS', 'DIST_WATER_M', 'Distancia andando a la fuente de agua más cercana', 'm', 'AYTO_FUENTES',
         'Dijkstra por la red peatonal, como DIST_BENCH_M. No filtra todavía las fuentes fuera de servicio.' FROM dual UNION ALL
  SELECT 'TRAMOS', 'TREES_20M', 'Árboles a menos de 15 m del tramo (el nombre de la columna es histórico)', 'árboles', 'AYTO_ARBOLADO',
         'Árboles dentro de un buffer de 15 m alrededor de la línea del tramo (EPSG:25830). No equivale a sombra.' FROM dual UNION ALL
  SELECT 'POI', 'NOMBRE', 'Nombre del recurso', NULL, NULL,
         'Estación de Metro, parada de bus o texto fijo («Fuente de agua»). Vacío en árboles. Sin nombres de personas (minimización: no se carga el titular de las farmacias).' FROM dual UNION ALL
  SELECT 'POI', 'FUENTE_ID', 'Fuente de datos de la que sale el recurso', NULL, NULL,
         'Clave de FUENTES: AYTO_BANCOS, CM_FARMACIAS, CRTM_GTFS_METRO, CRTM_GTFS_EMT, AYTO_FUENTES, AYTO_ARBOLADO.' FROM dual UNION ALL
  SELECT 'POI', 'ESPECIE', 'Especie del árbol (nombre científico)', NULL, 'AYTO_ARBOLADO',
         'Solo en tipo arbol. Se cruza con ESPECIE_POLEN.ESPECIE.' FROM dual UNION ALL
  SELECT 'POI', 'ACCESIBLE', 'Accesibilidad declarada en la fuente', NULL, 'CRTM_GTFS_METRO',
         'Metro: wheelchair_boarding de la estación (1 = si, 2 = no, otro = desconocido). Bus EMT: siempre desconocido (el GTFS no lo informa). Resto de recursos: vacío.' FROM dual UNION ALL
  SELECT 'POLEN_MES', 'TIPO_POLEN', 'Tipo polínico (nombre del captador)', NULL, 'CM_POLEN',
         'Solo los 15 tipos con umbral oficial del Ayuntamiento.' FROM dual UNION ALL
  SELECT 'POLEN_MES', 'MES', 'Mes del año (1-12)', NULL, 'CM_POLEN', 'Todos los años disponibles juntos (2024-2026).' FROM dual UNION ALL
  SELECT 'POLEN_MES', 'NIVEL_MEDIO', 'Media de la concentración diaria de polen en ese mes', 'granos/m3', 'CM_POLEN',
         'Media diaria de los captadores AYTM y FACF; después, media de los días del mes.' FROM dual UNION ALL
  SELECT 'POLEN_MES', 'NIVEL', 'Nivel de polen: bajo, medio o alto', NULL, 'CM_POLEN',
         'Umbrales oficiales del Ayuntamiento (Informe de análisis del polen, nov. 2025): bajo < «Medio» <= medio < «Alto» <= alto (incluye «Muy alto»).' FROM dual UNION ALL
  SELECT 'ESPECIE_POLEN', 'ESPECIE', 'Especie del arbolado (nombre científico, como en POI.ESPECIE)', NULL, 'AYTO_ARBOLADO', NULL FROM dual UNION ALL
  SELECT 'ESPECIE_POLEN', 'TIPO_POLEN', 'Tipo de polen que produce la especie', NULL, 'CM_POLEN',
         'Asignado por género (primera palabra del nombre científico). Ej.: PLATANUS -> Plátano de paseo; PINUS y CEDRUS -> Pinos.' FROM dual UNION ALL
  SELECT 'AIRE_MES_HORA', 'MES', 'Mes del año (1-12)', NULL, 'AYTO_CALIDAD_AIRE', 'Todas las fechas disponibles juntas (ene 2025 - sep 2026).' FROM dual UNION ALL
  SELECT 'AIRE_MES_HORA', 'HORA', 'Hora del día (0-23)', 'h', 'AYTO_CALIDAD_AIRE', 'Hora de inicio: 9 = de 09:00 a 10:00 (columna H10 del fichero).' FROM dual
) s
ON (d.tabla = s.tabla AND d.columna = s.columna)
WHEN MATCHED THEN UPDATE SET d.descripcion = s.descripcion, d.unidad = s.unidad,
                             d.fuente_id = s.fuente_id, d.metodo = s.metodo
WHEN NOT MATCHED THEN INSERT (tabla, columna, descripcion, unidad, fuente_id, metodo)
  VALUES (s.tabla, s.columna, s.descripcion, s.unidad, s.fuente_id, s.metodo);

COMMIT;

-- ---------------------------------------------------------------- Comprobaciones
-- 1) Ninguna fecha nula ni «pendiente de descargar» (debe devolver 0 filas).
-- Se busca «pendiente de» y no «pendiente» a secas, porque la pendiente de las calles (EU_DEM_25M) también la contiene.
SELECT fuente_id, fecha_descarga, limitaciones FROM fuentes
WHERE fecha_descarga IS NULL OR LOWER(limitaciones) LIKE '%pendiente de%' OR LOWER(uso_en_proyecto) LIKE '%pendiente de%';

-- 2) Columnas documentadas por tabla
SELECT tabla, COUNT(*) AS columnas_documentadas FROM diccionario GROUP BY tabla ORDER BY tabla;