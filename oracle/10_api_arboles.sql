-- =====================================================================
-- 10 · Endpoint arboles.geojson (apartado 2: Oracle como única fuente de verdad)
-- poi.geojson excluye los árboles por tamaño. Este endpoint publica SOLO los árboles
-- con tipo de polen (unos 8.953 de 28.760), que son los que usa el aviso de polen.
-- Ejecutar como MADDATA (APEX › SQL Workshop › SQL Scripts). Se puede repetir.
--
--   GET .../dataset/arboles.geojson   -> FeatureCollection: poi_id, especie, tipo_polen
-- =====================================================================
BEGIN
  ORDS.DEFINE_TEMPLATE(p_module_name => 'dataset', p_pattern => 'arboles.geojson');
  ORDS.DEFINE_HANDLER(p_module_name => 'dataset', p_pattern => 'arboles.geojson', p_method => 'GET',
    p_source_type => ORDS.source_type_media,
    p_source => q'[
      SELECT 'application/geo+json',
             JSON_OBJECT('type' VALUE 'FeatureCollection',
               'features' VALUE JSON_ARRAYAGG(
                  JSON_OBJECT('type' VALUE 'Feature',
                    'properties' VALUE JSON_OBJECT('poi_id' VALUE p.poi_id, 'especie' VALUE p.especie,
                                                   'tipo_polen' VALUE e.tipo_polen),
                    'geometry' VALUE SDO_UTIL.TO_GEOJSON(p.geom) FORMAT JSON)
                  RETURNING CLOB)
             RETURNING CLOB)
      FROM poi p JOIN especie_polen e ON e.especie = p.especie
      WHERE p.tipo = 'arbol']');
  COMMIT;
END;
/

-- Comprobación: árboles que publicará el endpoint (esperado: 8953)
SELECT COUNT(*) FROM poi p JOIN especie_polen e ON e.especie = p.especie WHERE p.tipo = 'arbol';