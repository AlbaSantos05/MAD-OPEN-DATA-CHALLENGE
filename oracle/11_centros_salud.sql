-- =====================================================================
-- 11 · Centros de salud de la Comunidad de Madrid (Chamberí y 500 m alrededor)
-- Ejecutar como MADDATA (APEX › SQL Workshop › SQL Scripts). Se puede repetir:
-- crea la tabla si no existe y vuelve a cargar las filas.
-- Datos generados por pipelines/centros_salud.py a partir de data/raw/centros_salud.geojson.
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
      lat         NUMBER(9,6)   NOT NULL CONSTRAINT cs_lat_ck CHECK (lat BETWEEN 40 AND 41),
      lon         NUMBER(9,6)   NOT NULL CONSTRAINT cs_lon_ck CHECK (lon BETWEEN -4 AND -3)
    )]';
EXCEPTION
  WHEN ya_existe THEN NULL;
END;
/

-- ---------------------------------------------------------------- fuente (antes que los datos)
MERGE INTO fuentes f
USING (SELECT 'CM_CENTROS_SALUD' AS fuente_id FROM dual) s
ON (f.fuente_id = s.fuente_id)
WHEN NOT MATCHED THEN INSERT VALUES (
  'CM_CENTROS_SALUD', 'Centros de Salud (IDEM, Infraestructura de Datos Espaciales de la Comunidad de Madrid)', 'CM',
  'idem.comunidad.madrid', 'https://idem.comunidad.madrid/ags/rest/services/Hosted/Centros_de_Salud/FeatureServer/0',
  'Ver condiciones del portal', 'GeoJSON (servicio ArcGIS REST)', DATE '2026-10-18',
  'Centro de salud más cercano (botón y avisos del acompañante, por ejemplo ante un mareo). Tabla CENTROS_SALUD: 10 centros a menos de 500 m de la red peatonal del prototipo.',
  'Sin horarios ni servicio de urgencias. El filtro de 500 m es en línea recta. Un centro de salud no sustituye al 112 en una urgencia.');

-- ---------------------------------------------------------------- datos
DELETE FROM centros_salud;
INSERT INTO centros_salud (centro_id, nombre, tipo_centro, direccion, url, lat, lon) VALUES ('CS_85', 'Centro de salud Andrés Mellado', 'centro de salud', 'Calle Andrés Mellado 37', 'https://www.comunidad.madrid/centros/centro-salud-andres-mellado', 40.434416, -3.714518);
INSERT INTO centros_salud (centro_id, nombre, tipo_centro, direccion, url, lat, lon) VALUES ('CS_88', 'Centro de salud Argüelles', 'centro de salud', 'Calle Quintana 11', 'https://www.comunidad.madrid/centros/centro-salud-arguelles', 40.427804, -3.717265);
INSERT INTO centros_salud (centro_id, nombre, tipo_centro, direccion, url, lat, lon) VALUES ('CS_107', 'Centro de salud Cea Bermúdez', 'centro de salud', 'Calle Cea Bermúdez 10', 'https://www.comunidad.madrid/centros/centro-salud-cea-bermudez', 40.438947, -3.707137);
INSERT INTO centros_salud (centro_id, nombre, tipo_centro, direccion, url, lat, lon) VALUES ('CS_118', 'Centro de salud Eloy Gonzalo', 'centro de salud', 'Calle Eloy Gonzalo 24', 'https://www.comunidad.madrid/centros/centro-salud-eloy-gonzalo', 40.434182, -3.701228);
INSERT INTO centros_salud (centro_id, nombre, tipo_centro, direccion, url, lat, lon) VALUES ('CS_122', 'Centro de salud Espronceda', 'centro de salud', 'Calle Espronceda 24', 'https://www.comunidad.madrid/centros/centro-salud-espronceda', 40.440515, -3.696683);
INSERT INTO centros_salud (centro_id, nombre, tipo_centro, direccion, url, lat, lon) VALUES ('CS_133', 'Centro de salud Guzmán el Bueno', 'centro de salud', 'Calle Guzmán El Bueno 11', 'https://www.comunidad.madrid/centros/centro-salud-guzman-bueno', 40.431268, -3.713588);
INSERT INTO centros_salud (centro_id, nombre, tipo_centro, direccion, url, lat, lon) VALUES ('CS_141', 'Centro de salud Justicia', 'centro de salud', 'Calle Regueros 3', 'https://www.comunidad.madrid/centros/centro-salud-justicia', 40.424461, -3.696777);
INSERT INTO centros_salud (centro_id, nombre, tipo_centro, direccion, url, lat, lon) VALUES ('CS_143', 'Centro de salud Lagasca', 'centro de salud', 'Calle Lagasca 122', 'https://www.comunidad.madrid/centros/centro-salud-lagasca', 40.435608, -3.684206);
INSERT INTO centros_salud (centro_id, nombre, tipo_centro, direccion, url, lat, lon) VALUES ('CS_171', 'Centro de salud Palma Norte', 'centro de salud', 'Calle Palma c/v Norte 59', 'https://www.comunidad.madrid/centros/centro-salud-palma-norte', 40.426631, -3.707362);
INSERT INTO centros_salud (centro_id, nombre, tipo_centro, direccion, url, lat, lon) VALUES ('CS_185', 'Centro de salud Reina Victoria', 'centro de salud', 'Avenida Reina Victoria 21', 'https://www.comunidad.madrid/centros/centro-salud-reina-victoria', 40.446846, -3.707527);
INSERT INTO cargas (tabla, filas, origen) VALUES ('CENTROS_SALUD', 10, 'data/processed/centros_salud.csv (vía oracle/11_centros_salud.sql)');
COMMIT;

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

-- Comprobación: debe dar 10
SELECT COUNT(*) FROM centros_salud;