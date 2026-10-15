-- =====================================================================
-- 02 · Tablas del dataset, metadatos espaciales e índices
-- Ejecutar como MADDATA (Database Actions > SQL > Run Script).
-- Para empezar de cero: ejecutar antes 99_borrar_todo.sql
--
-- Geometrías en WGS84 (SRID 4326): es lo que usan el mapa de APEX y Leaflet.
-- Las medidas (m, %) ya vienen calculadas en metros (EPSG:25830) desde Python.
-- =====================================================================

-- ---------------------------------------------------------------------
-- FUENTES: de dónde sale cada dato. Es lo primero que mirará el jurado.
-- ---------------------------------------------------------------------
CREATE TABLE fuentes (
  fuente_id      VARCHAR2(40)  CONSTRAINT fuentes_pk PRIMARY KEY,
  nombre         VARCHAR2(200) NOT NULL,
  organismo      VARCHAR2(20)  NOT NULL
                 CONSTRAINT fuentes_org_ck CHECK (organismo IN ('CM','CRTM','AYTO','OSM','OTRO')),
  portal         VARCHAR2(100),
  url            VARCHAR2(400),
  licencia       VARCHAR2(100),
  formato        VARCHAR2(40),
  fecha_descarga DATE,
  uso_en_proyecto VARCHAR2(600),
  limitaciones   VARCHAR2(600)
);

-- ---------------------------------------------------------------------
-- POI: recursos puntuales (zona piloto + 500 m de margen)
-- ---------------------------------------------------------------------
CREATE TABLE poi (
  poi_id     VARCHAR2(60)  CONSTRAINT poi_pk PRIMARY KEY,
  tipo       VARCHAR2(20)  NOT NULL
             CONSTRAINT poi_tipo_ck CHECK (tipo IN ('banco','farmacia','metro','bus','fuente','arbol',
                                                    'centro_salud','centro_mayores')),
  nombre     VARCHAR2(300),
  fuente_id  VARCHAR2(40)  NOT NULL CONSTRAINT poi_fuente_fk REFERENCES fuentes,
  accesible  VARCHAR2(12)  CONSTRAINT poi_acc_ck CHECK (accesible IN ('si','no','desconocido')),
  especie    VARCHAR2(150),
  geom       SDO_GEOMETRY  NOT NULL
);

-- ---------------------------------------------------------------------
-- TRAMOS: producto A, la red peatonal enriquecida (1 fila = 1 segmento físico de calle)
-- ---------------------------------------------------------------------
CREATE TABLE tramos (
  tramo_id         NUMBER(9)     CONSTRAINT tramos_pk PRIMARY KEY,
  nodo_u           NUMBER(15)    NOT NULL,
  nodo_v           NUMBER(15)    NOT NULL,
  nombre_via       VARCHAR2(200),
  tipo_via         VARCHAR2(40),
  length_m         NUMBER(7,1)   NOT NULL CONSTRAINT tramos_len_ck CHECK (length_m >= 0),
  slope_pct        NUMBER(4,1)   CONSTRAINT tramos_slope_ck CHECK (slope_pct BETWEEN 0 AND 30),
  steps            VARCHAR2(12)  NOT NULL
                   CONSTRAINT tramos_steps_ck CHECK (steps IN ('si','no','desconocido')),
  benches_20m      NUMBER(4),
  dist_bench_m     NUMBER(7,1),
  dist_transport_m NUMBER(7,1),
  nearest_stop_id  VARCHAR2(60)  CONSTRAINT tramos_stop_fk REFERENCES poi,
  stop_accessible  VARCHAR2(12)
                   CONSTRAINT tramos_acc_ck CHECK (stop_accessible IN ('si','no','desconocido')),
  dist_pharmacy_m  NUMBER(7,1),
  dist_water_m     NUMBER(7,1),
  trees_20m        NUMBER(5),
  quality_flags    VARCHAR2(400),
  geom             SDO_GEOMETRY  NOT NULL
);
CREATE INDEX tramos_nodos_ix ON tramos (nodo_u, nodo_v);

-- ---------------------------------------------------------------------
-- Datos ambientales de la Comunidad, ya resumidos por Python (se rellenan el sábado)
-- ---------------------------------------------------------------------
CREATE TABLE clima_mes_hora (
  estacion_id VARCHAR2(20),
  mes         NUMBER(2) CONSTRAINT clima_mes_ck CHECK (mes BETWEEN 1 AND 12),
  hora        NUMBER(2) CONSTRAINT clima_hora_ck CHECK (hora BETWEEN 0 AND 23),
  temp_media  NUMBER(4,1),
  n_medidas   NUMBER,
  CONSTRAINT clima_pk PRIMARY KEY (estacion_id, mes, hora)
);

CREATE TABLE polen_mes (
  tipo_polen  VARCHAR2(60),
  mes         NUMBER(2) CONSTRAINT polen_mes_ck CHECK (mes BETWEEN 1 AND 12),
  nivel_medio NUMBER,
  nivel       VARCHAR2(10) CONSTRAINT polen_nivel_ck CHECK (nivel IN ('bajo','medio','alto')),
  CONSTRAINT polen_pk PRIMARY KEY (tipo_polen, mes)
);

CREATE TABLE especie_polen (
  especie    VARCHAR2(150) CONSTRAINT especie_polen_pk PRIMARY KEY,
  tipo_polen VARCHAR2(60)  NOT NULL
);

-- ---------------------------------------------------------------------
-- ZONAS: producto B, índice por sección censal (se rellena el lunes)
-- ---------------------------------------------------------------------
CREATE TABLE zonas (
  seccion_id        VARCHAR2(20) CONSTRAINT zonas_pk PRIMARY KEY,
  barrio            VARCHAR2(100),
  poblacion_65      NUMBER,
  km_red            NUMBER(6,2),
  bancos_por_km     NUMBER(6,1),
  pct_red_transp300 NUMBER(5,1),
  pendiente_media   NUMBER(4,1),
  indice            NUMBER(5,1) CONSTRAINT zonas_indice_ck CHECK (indice BETWEEN 0 AND 100),
  geom              SDO_GEOMETRY
);

-- ---------------------------------------------------------------------
-- DICCIONARIO: qué significa cada columna y cómo se calcula
-- ---------------------------------------------------------------------
CREATE TABLE diccionario (
  tabla       VARCHAR2(30),
  columna     VARCHAR2(30),
  descripcion VARCHAR2(400) NOT NULL,
  unidad      VARCHAR2(20),
  fuente_id   VARCHAR2(40) CONSTRAINT dicc_fuente_fk REFERENCES fuentes,
  metodo      VARCHAR2(800),
  CONSTRAINT diccionario_pk PRIMARY KEY (tabla, columna)
);

-- ---------------------------------------------------------------------
-- CARGAS: registro de cada ejecución del cargador (trazabilidad)
-- ---------------------------------------------------------------------
CREATE TABLE cargas (
  carga_id   NUMBER GENERATED ALWAYS AS IDENTITY CONSTRAINT cargas_pk PRIMARY KEY,
  fecha      TIMESTAMP DEFAULT SYSTIMESTAMP,
  tabla      VARCHAR2(30),
  filas      NUMBER,
  origen     VARCHAR2(200),
  notas      VARCHAR2(400)
);

-- ---------------------------------------------------------------------
-- Metadatos espaciales (obligatorios antes de crear los índices)
-- Tolerancia 0.05 = 5 cm (en SRID 4326 Oracle la interpreta en metros)
-- ---------------------------------------------------------------------
INSERT INTO user_sdo_geom_metadata (table_name, column_name, diminfo, srid) VALUES ('TRAMOS', 'GEOM',
  SDO_DIM_ARRAY(SDO_DIM_ELEMENT('X', -180, 180, 0.05), SDO_DIM_ELEMENT('Y', -90, 90, 0.05)), 4326);
INSERT INTO user_sdo_geom_metadata (table_name, column_name, diminfo, srid) VALUES ('POI', 'GEOM',
  SDO_DIM_ARRAY(SDO_DIM_ELEMENT('X', -180, 180, 0.05), SDO_DIM_ELEMENT('Y', -90, 90, 0.05)), 4326);
INSERT INTO user_sdo_geom_metadata (table_name, column_name, diminfo, srid) VALUES ('ZONAS', 'GEOM',
  SDO_DIM_ARRAY(SDO_DIM_ELEMENT('X', -180, 180, 0.05), SDO_DIM_ELEMENT('Y', -90, 90, 0.05)), 4326);
COMMIT;

CREATE INDEX tramos_sidx ON tramos (geom) INDEXTYPE IS MDSYS.SPATIAL_INDEX_V2 PARAMETERS ('layer_gtype=LINE');
CREATE INDEX poi_sidx    ON poi (geom)    INDEXTYPE IS MDSYS.SPATIAL_INDEX_V2 PARAMETERS ('layer_gtype=POINT');
CREATE INDEX zonas_sidx  ON zonas (geom)  INDEXTYPE IS MDSYS.SPATIAL_INDEX_V2;
CREATE INDEX poi_tipo_ix ON poi (tipo);
