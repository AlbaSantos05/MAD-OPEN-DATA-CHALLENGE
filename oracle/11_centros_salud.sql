-- =====================================================================
-- 11 · Centros de salud de la Comunidad de Madrid (zona del prototipo y 500 m alrededor)
-- Ejecutar como MADDATA (APEX › SQL Workshop › SQL Scripts). Se puede repetir.
-- SOLO estructura: tabla, fuente, diccionario y API. Sin datos escritos a mano.
-- Los datos los genera pipelines/centros_salud.py (para la zona que cubra el dataset)
-- y los carga pipelines/cargar_ambiental.py, igual que el polen y el aire.
--
--   GET .../dataset/centros_salud/          -> lista (JSON)
--   GET .../dataset/centros_salud.geojson   -> para el mapa
-- =====================================================================

-- ---------------------------------------------------------------- tabla
DECLARE
  ya_existe EXCEPTION;
  PRAGMA EXCEPTION_INIT(ya_existe, -955);
BEGIN
  EXECUTE IMMEDIATE q'[
    CREATE TABLE centros_salud (
      centro_id   VARCHAR2(20)  CONSTRAINT centros_salud_pk PRIMARY KEY,
      nombre      VARCHAR2(200) NOT NULL,
      tipo_centro VARCHAR2(60),
      direccion   VARCHAR2(200),
      url         VARCHAR2(300),
      lat         NUMBER(9,6)   NOT NULL CONSTRAINT cs_lat_ck CHECK (lat BETWEEN 39.8 AND 41.2),
      lon         NUMBER(9,6)   NOT NULL CONSTRAINT cs_lon_ck CHECK (lon BETWEEN -4.6 AND -3.0)
    )]';
EXCEPTION
  WHEN ya_existe THEN NULL;
END;
/

-- Rango de coordenadas = toda la Comunidad de Madrid (la primera versión lo limitaba a la capital)
BEGIN
  FOR c IN (SELECT constraint_name FROM user_constraints
            WHERE table_name = 'CENTROS_SALUD' AND constraint_name IN ('CS_LAT_CK', 'CS_LON_CK')) LOOP
    EXECUTE IMMEDIATE 'ALTER TABLE centros_salud DROP CONSTRAINT ' || c.constraint_name;
  END LOOP;
  EXECUTE IMMEDIATE 'ALTER TABLE centros_salud ADD CONSTRAINT cs_lat_ck CHECK (lat BETWEEN 39.8 AND 41.2)';
  EXECUTE IMMEDIATE 'ALTER TABLE centros_salud ADD CONSTRAINT cs_lon_ck CHECK (lon BETWEEN -4.6 AND -3.0)';
END;
/

-- ---------------------------------------------------------------- fuente (antes que los datos)
MERGE INTO fuentes f
USING (SELECT 'CM_CENTROS_SALUD' AS fuente_id FROM dual) s
ON (f.fuente_id = s.fuente_id)
WHEN MATCHED THEN UPDATE SET
  uso_en_proyecto = 'Centro de salud más cercano (botón y avisos del acompañante, por ejemplo ante un mareo). Tabla CENTROS_SALUD: centros a menos de 500 m de la red peatonal del dataset, descargados de la IDEM con el rectángulo de esa red (pipelines/centros_salud.py).',
  limitaciones    = 'Sin horarios ni servicio de urgencias. El filtro de 500 m es en línea recta. Un centro de salud no sustituye al 112 en una urgencia.'
WHEN NOT MATCHED THEN INSERT VALUES (
  'CM_CENTROS_SALUD', 'Centros de Salud (IDEM, Infraestructura de Datos Espaciales de la Comunidad de Madrid)', 'CM',
  'idem.comunidad.madrid', 'https://idem.comunidad.madrid/ags/rest/services/Hosted/Centros_de_Salud/FeatureServer/0',
  'Ver condiciones del portal', 'GeoJSON (servicio ArcGIS REST)', DATE '2026-10-18',
  'Centro de salud más cercano (botón y avisos del acompañante, por ejemplo ante un mareo). Tabla CENTROS_SALUD: centros a menos de 500 m de la red peatonal del dataset, descargados de la IDEM con el rectángulo de esa red (pipelines/centros_salud.py).',
  'Sin horarios ni servicio de urgencias. El filtro de 500 m es en línea recta. Un centro de salud no sustituye al 112 en una urgencia.');

-- ---------------------------------------------------------------- diccionario
MERGE INTO diccionario d
USING (
  SELECT 'CENTROS_SALUD' tabla, 'CENTRO_ID' columna, 'Identificador: CS_ + fid del servicio IDEM' descripcion, NULL unidad, NULL metodo FROM dual UNION ALL
  SELECT 'CENTROS_SALUD', 'NOMBRE', 'Nombre del centro', NULL, NULL FROM dual UNION ALL
  SELECT 'CENTROS_SALUD', 'TIPO_CENTRO', 'Tipo de centro (centro de salud, consultorio local...)', NULL, 'Campo tipocentro del IDEM (CS = centro de salud).' FROM dual UNION ALL
  SELECT 'CENTROS_SALUD', 'DIRECCION', 'Dirección postal', NULL, 'Campos tipovial + nomvia + numero del IDEM.' FROM dual UNION ALL
  SELECT 'CENTROS_SALUD', 'URL', 'Ficha del centro en comunidad.madrid', NULL, NULL FROM dual UNION ALL
  SELECT 'CENTROS_SALUD', 'LAT', 'Latitud', 'WGS84', NULL FROM dual UNION ALL
  SELECT 'CENTROS_SALUD', 'LON', 'Longitud', 'WGS84', NULL FROM dual
) s
ON (d.tabla = s.tabla AND d.columna = s.columna)
WHEN MATCHED THEN UPDATE SET d.descripcion = s.descripcion, d.unidad = s.unidad, d.metodo = s.metodo
WHEN NOT MATCHED THEN INSERT (tabla, columna, descripcion, unidad, fuente_id, metodo)
  VALUES (s.tabla, s.columna, s.descripcion, s.unidad, 'CM_CENTROS_SALUD', s.metodo);
COMMIT;

-- ---------------------------------------------------------------- API
BEGIN
  ORDS.DEFINE_TEMPLATE(p_module_name => 'dataset', p_pattern => 'centros_salud/');
  ORDS.DEFINE_HANDLER(p_module_name => 'dataset', p_pattern => 'centros_salud/', p_method => 'GET',
    p_source_type => ORDS.source_type_collection_feed,
    p_source => 'SELECT centro_id, nombre, tipo_centro, direccion, url, lat, lon FROM centros_salud ORDER BY nombre');

  ORDS.DEFINE_TEMPLATE(p_module_name => 'dataset', p_pattern => 'centros_salud.geojson');
  ORDS.DEFINE_HANDLER(p_module_name => 'dataset', p_pattern => 'centros_salud.geojson', p_method => 'GET',
    p_source_type => ORDS.source_type_media,
    p_source => q'[
      SELECT 'application/geo+json',
             JSON_OBJECT('type' VALUE 'FeatureCollection',
               'features' VALUE JSON_ARRAYAGG(
                  JSON_OBJECT('type' VALUE 'Feature',
                    'properties' VALUE JSON_OBJECT('centro_id' VALUE centro_id, 'nombre' VALUE nombre,
                                                   'tipo_centro' VALUE tipo_centro, 'direccion' VALUE direccion,
                                                   'url' VALUE url),
                    'geometry' VALUE JSON_OBJECT('type' VALUE 'Point',
                                                 'coordinates' VALUE JSON_ARRAY(lon, lat)))
                  RETURNING CLOB)
             RETURNING CLOB)
      FROM centros_salud]');
  COMMIT;
END;
/

-- Comprobación: la tabla existe (0 filas hasta ejecutar pipelines/cargar_ambiental.py)
SELECT COUNT(*) FROM centros_salud;