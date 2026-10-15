-- =====================================================================
-- 05 · API REST de SOLO LECTURA con ORDS
-- Ejecutar como MADDATA (se puede repetir: DEFINE_MODULE sustituye el módulo). Usamos un módulo propio con handlers GET (y no AutoREST),
-- porque AutoREST sin autenticación permitiría también insertar o borrar filas.
--
-- URL base: https://<id>-madopendata.adb.<region>.oraclecloudapps.com/ords/maddata/dataset/
--   GET .../dataset/tramos/            -> tramos paginados (JSON, 500 por página)
--   GET .../dataset/tramos.geojson     -> todos los tramos como FeatureCollection (Leaflet)
--   GET .../dataset/poi/               -> recursos paginados
--   GET .../dataset/poi.geojson        -> recursos como FeatureCollection
--   GET .../dataset/fuentes/           -> catálogo de fuentes
--   GET .../dataset/diccionario/       -> diccionario de datos
--   GET .../dataset/resumen/           -> cifras globales del dataset
-- =====================================================================
BEGIN
  ORDS.DEFINE_MODULE(
    p_module_name    => 'dataset',
    p_base_path      => 'dataset/',
    p_items_per_page => 500,
    p_status         => 'PUBLISHED',
    p_comments       => 'Dataset abierto del acompañante urbano (MAD Open Data Challenge 2026)');

  -- Tablas en JSON paginado ------------------------------------------------
  ORDS.DEFINE_TEMPLATE(p_module_name => 'dataset', p_pattern => 'tramos/');
  ORDS.DEFINE_HANDLER(p_module_name => 'dataset', p_pattern => 'tramos/', p_method => 'GET',
    p_source_type => ORDS.source_type_collection_feed,
    p_source => 'SELECT tramo_id, nombre_via, tipo_via, length_m, slope_pct, steps, benches_20m,
                        dist_bench_m, dist_transport_m, nearest_stop_id, stop_accessible,
                        dist_pharmacy_m, dist_water_m, trees_20m, quality_flags
                 FROM tramos ORDER BY tramo_id');

  ORDS.DEFINE_TEMPLATE(p_module_name => 'dataset', p_pattern => 'poi/');
  ORDS.DEFINE_HANDLER(p_module_name => 'dataset', p_pattern => 'poi/', p_method => 'GET',
    p_source_type => ORDS.source_type_collection_feed,
    p_source => 'SELECT poi_id, tipo, nombre, fuente_id, accesible, especie,
                        p.geom.sdo_point.y AS lat, p.geom.sdo_point.x AS lon
                 FROM poi p ORDER BY tipo, poi_id');

  ORDS.DEFINE_TEMPLATE(p_module_name => 'dataset', p_pattern => 'fuentes/');
  ORDS.DEFINE_HANDLER(p_module_name => 'dataset', p_pattern => 'fuentes/', p_method => 'GET',
    p_source_type => ORDS.source_type_collection_feed,
    p_source => 'SELECT * FROM fuentes ORDER BY organismo, fuente_id');

  ORDS.DEFINE_TEMPLATE(p_module_name => 'dataset', p_pattern => 'diccionario/');
  ORDS.DEFINE_HANDLER(p_module_name => 'dataset', p_pattern => 'diccionario/', p_method => 'GET',
    p_source_type => ORDS.source_type_collection_feed,
    p_source => 'SELECT * FROM diccionario ORDER BY tabla, columna');

  ORDS.DEFINE_TEMPLATE(p_module_name => 'dataset', p_pattern => 'resumen/');
  ORDS.DEFINE_HANDLER(p_module_name => 'dataset', p_pattern => 'resumen/', p_method => 'GET',
    p_source_type => ORDS.source_type_collection_item,
    p_source => 'SELECT * FROM v_resumen_dataset');

  -- GeoJSON completo para el mapa ------------------------------------------
  ORDS.DEFINE_TEMPLATE(p_module_name => 'dataset', p_pattern => 'tramos.geojson');
  ORDS.DEFINE_HANDLER(p_module_name => 'dataset', p_pattern => 'tramos.geojson', p_method => 'GET',
    p_source_type => ORDS.source_type_media,
    p_source => q'[
      SELECT 'application/geo+json',
             JSON_OBJECT('type' VALUE 'FeatureCollection',
               'features' VALUE JSON_ARRAYAGG(
                  JSON_OBJECT('type' VALUE 'Feature',
                    'properties' VALUE JSON_OBJECT(
                       'tramo_id' VALUE tramo_id, 'nodo_u' VALUE nodo_u, 'nodo_v' VALUE nodo_v,
                       'nombre_via' VALUE nombre_via, 'tipo_via' VALUE tipo_via,
                       'length_m' VALUE length_m, 'slope_pct' VALUE slope_pct, 'steps' VALUE steps,
                       'benches_20m' VALUE benches_20m, 'dist_bench_m' VALUE dist_bench_m,
                       'dist_transport_m' VALUE dist_transport_m, 'nearest_stop_id' VALUE nearest_stop_id,
                       'stop_accessible' VALUE stop_accessible, 'dist_pharmacy_m' VALUE dist_pharmacy_m,
                       'dist_water_m' VALUE dist_water_m, 'trees_20m' VALUE trees_20m,
                       'quality_flags' VALUE quality_flags NULL ON NULL),
                    'geometry' VALUE SDO_UTIL.TO_GEOJSON(geom) FORMAT JSON)
                  RETURNING CLOB)
             RETURNING CLOB)
      FROM tramos]');

  ORDS.DEFINE_TEMPLATE(p_module_name => 'dataset', p_pattern => 'poi.geojson');
  ORDS.DEFINE_HANDLER(p_module_name => 'dataset', p_pattern => 'poi.geojson', p_method => 'GET',
    p_source_type => ORDS.source_type_media,
    p_source => q'[
      SELECT 'application/geo+json',
             JSON_OBJECT('type' VALUE 'FeatureCollection',
               'features' VALUE JSON_ARRAYAGG(
                  JSON_OBJECT('type' VALUE 'Feature',
                    'properties' VALUE JSON_OBJECT('poi_id' VALUE poi_id, 'tipo' VALUE tipo,
                                                   'nombre' VALUE nombre, 'fuente_id' VALUE fuente_id,
                                                   'accesible' VALUE accesible NULL ON NULL),
                    'geometry' VALUE SDO_UTIL.TO_GEOJSON(geom) FORMAT JSON)
                  RETURNING CLOB)
             RETURNING CLOB)
      FROM poi WHERE tipo <> 'arbol']');

  COMMIT;
END;
/
