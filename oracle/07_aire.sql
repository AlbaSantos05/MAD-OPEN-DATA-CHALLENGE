-- =====================================================================
-- 07 · Calidad del aire por mes y hora (estación 38, Cuatro Caminos)
-- Ejecutar como MADDATA en Database Actions (Run Script, F5), ANTES de
-- pipelines/cargar_ambiental.py. Se puede repetir: lo que ya existe se deja igual.
--
-- Crea:  tabla AIRE_MES_HORA · fuente AYTO_CALIDAD_AIRE · diccionario · endpoint de la API
--   GET .../dataset/aire/                -> 288 filas (12 meses x 24 horas)
--   GET .../dataset/aire/?mes=10         -> un mes
--   GET .../dataset/aire/?mes=10&hora=9  -> una hora concreta
-- =====================================================================

-- ---------------------------------------------------------------- tabla
DECLARE
  ya_existe EXCEPTION;
  PRAGMA EXCEPTION_INIT(ya_existe, -955);
BEGIN
  EXECUTE IMMEDIATE q'[
    CREATE TABLE aire_mes_hora (
      mes                NUMBER(2) CONSTRAINT aire_mes_ck  CHECK (mes BETWEEN 1 AND 12),
      hora               NUMBER(2) CONSTRAINT aire_hora_ck CHECK (hora BETWEEN 0 AND 23),
      no2_media          NUMBER(6,1),
      pm10_media         NUMBER(6,1),
      pm25_media         NUMBER(6,1),
      categoria          VARCHAR2(30) CONSTRAINT aire_cat_ck CHECK (categoria IN
                           ('Buena','Razonablemente buena','Regular','Desfavorable',
                            'Muy desfavorable','Extremadamente desfavorable')),
      pct_regular_o_peor NUMBER(5,1),
      n_horas            NUMBER,
      CONSTRAINT aire_pk PRIMARY KEY (mes, hora)
    )]';
EXCEPTION
  WHEN ya_existe THEN NULL;   -- ya estaba creada
END;
/

-- ---------------------------------------------------------------- fuente
MERGE INTO fuentes f
USING (SELECT 'AYTO_CALIDAD_AIRE' AS fuente_id FROM dual) s
ON (f.fuente_id = s.fuente_id)
WHEN NOT MATCHED THEN INSERT VALUES (
  'AYTO_CALIDAD_AIRE', 'Calidad del aire. Datos horarios (fichero desde 2025)', 'AYTO',
  'datos.madrid.es', 'https://datos.madrid.es/dataset/201200-0-calidad-aire-horario', 'Ver condiciones del portal',
  'CSV', DATE '2026-10-17',
  'Calidad del aire por mes y hora en la estación 38 (Cuatro Caminos, Chamberí): NO2, PM10 y PM2.5 clasificados con el ICA del MITECO (AIRE_MES_HORA).',
  'Patrón histórico (ene 2025 - sep 2026) de una sola estación: no es el aire de hoy ni de cada calle. La estación 38 no mide ozono. Octubre solo tiene un año de datos.');

-- ---------------------------------------------------------------- diccionario
MERGE INTO diccionario d
USING (
  SELECT 'AIRE_MES_HORA' tabla, 'NO2_MEDIA' columna, 'Media del NO2 horario a esa hora del día en ese mes' descripcion, 'µg/m3' unidad,
         'Solo horas validadas (V). Hxx del fichero = hora que termina a las xx; se guarda como hora xx-1.' metodo FROM dual UNION ALL
  SELECT 'AIRE_MES_HORA', 'PM10_MEDIA', 'Media de la media móvil de 24 h de PM10', 'µg/m3',
         'Media móvil de 24 h con al menos 18 h válidas, como pide el ICA.' FROM dual UNION ALL
  SELECT 'AIRE_MES_HORA', 'PM25_MEDIA', 'Media de la media móvil de 24 h de PM2.5', 'µg/m3',
         'Media móvil de 24 h con al menos 18 h válidas, como pide el ICA.' FROM dual UNION ALL
  SELECT 'AIRE_MES_HORA', 'CATEGORIA', 'Categoría ICA típica a esa hora (la peor de NO2, PM10 y PM2.5 sobre las medias)', NULL,
         'Umbrales del Índice de Calidad del Aire del MITECO (Orden TEC/351/2019 y su actualización).' FROM dual UNION ALL
  SELECT 'AIRE_MES_HORA', 'PCT_REGULAR_O_PEOR', 'Porcentaje de horas en categoría Regular o peor', '%',
         'Cada hora se clasifica con el ICA (la peor de los tres contaminantes) y se cuenta.' FROM dual UNION ALL
  SELECT 'AIRE_MES_HORA', 'N_HORAS', 'Horas válidas usadas en el cálculo', 'horas', NULL FROM dual
) s
ON (d.tabla = s.tabla AND d.columna = s.columna)
WHEN NOT MATCHED THEN INSERT (tabla, columna, descripcion, unidad, fuente_id, metodo)
  VALUES (s.tabla, s.columna, s.descripcion, s.unidad, 'AYTO_CALIDAD_AIRE', s.metodo);

COMMIT;

-- ---------------------------------------------------------------- API
BEGIN
  ORDS.DEFINE_TEMPLATE(p_module_name => 'dataset', p_pattern => 'aire/');
  ORDS.DEFINE_HANDLER(p_module_name => 'dataset', p_pattern => 'aire/', p_method => 'GET',
    p_source_type => ORDS.source_type_collection_feed,
    p_source => 'SELECT mes, hora, no2_media, pm10_media, pm25_media, categoria, pct_regular_o_peor, n_horas
                 FROM aire_mes_hora
                 WHERE (:mes IS NULL OR mes = TO_NUMBER(:mes))
                   AND (:hora IS NULL OR hora = TO_NUMBER(:hora))
                 ORDER BY mes, hora');
  COMMIT;
END;
/

-- Comprobación: la tabla existe (0 filas hasta ejecutar cargar_ambiental.py; 288 después)
SELECT COUNT(*) FROM aire_mes_hora;