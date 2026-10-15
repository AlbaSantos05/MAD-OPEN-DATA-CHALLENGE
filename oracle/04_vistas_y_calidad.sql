-- =====================================================================
-- 04 · Vistas para APEX, la API y las comprobaciones de calidad
-- Ejecutar como MADDATA después de la primera carga (también funcionan con tablas vacías).
-- =====================================================================

-- Tramos con su GeoJSON, listos para Leaflet y para el mapa de APEX
CREATE OR REPLACE VIEW v_tramos_geojson AS
SELECT tramo_id, nombre_via, tipo_via, length_m, slope_pct, steps, benches_20m,
       dist_bench_m, dist_transport_m, stop_accessible, dist_pharmacy_m,
       SDO_UTIL.TO_GEOJSON(geom) AS geojson
FROM tramos;

-- Recursos por tipo y fuente (página "Fuentes" de APEX)
CREATE OR REPLACE VIEW v_poi_resumen AS
SELECT p.tipo, f.organismo, f.nombre AS fuente, COUNT(*) AS n
FROM poi p JOIN fuentes f ON f.fuente_id = p.fuente_id
GROUP BY p.tipo, f.organismo, f.nombre;

-- Cifras globales del dataset (página "Calidad" y diapositiva de la presentación)
CREATE OR REPLACE VIEW v_resumen_dataset AS
SELECT COUNT(*)                                                        AS tramos,
       ROUND(SUM(length_m) / 1000, 1)                                  AS km_red,
       ROUND(AVG(slope_pct), 1)                                        AS pendiente_media_pct,
       ROUND(100 * AVG(CASE WHEN benches_20m > 0 THEN 1 ELSE 0 END), 1) AS pct_tramos_con_banco,
       ROUND(100 * AVG(CASE WHEN dist_transport_m <= 300 THEN 1 ELSE 0 END), 1) AS pct_tramos_transporte_300m,
       ROUND(MEDIAN(dist_pharmacy_m))                                  AS mediana_dist_farmacia_m,
       SUM(CASE WHEN steps = 'si' THEN 1 ELSE 0 END)                   AS tramos_escalera
FROM tramos;

-- Cobertura: cuántos tramos tienen cada indicador
CREATE OR REPLACE VIEW v_cobertura AS
SELECT 'slope_pct' AS indicador, COUNT(slope_pct) AS con_dato, COUNT(*) AS total FROM tramos UNION ALL
SELECT 'benches_20m',      COUNT(benches_20m),      COUNT(*) FROM tramos UNION ALL
SELECT 'dist_transport_m', COUNT(dist_transport_m), COUNT(*) FROM tramos UNION ALL
SELECT 'dist_pharmacy_m',  COUNT(dist_pharmacy_m),  COUNT(*) FROM tramos UNION ALL
SELECT 'dist_water_m',     COUNT(dist_water_m),     COUNT(*) FROM tramos UNION ALL
SELECT 'trees_20m',        COUNT(trees_20m),        COUNT(*) FROM tramos;

-- Avisos de calidad desglosados (un tramo puede tener varios)
CREATE OR REPLACE VIEW v_avisos_calidad AS
SELECT aviso, COUNT(*) AS tramos
FROM (SELECT REGEXP_SUBSTR(t.quality_flags, '[^;]+', 1, n.lvl) AS aviso
      FROM tramos t
      CROSS JOIN (SELECT LEVEL AS lvl FROM dual CONNECT BY LEVEL <= 6) n
      WHERE REGEXP_SUBSTR(t.quality_flags, '[^;]+', 1, n.lvl) IS NOT NULL)
GROUP BY aviso;

-- Contraste independiente: Oracle vuelve a contar los bancos a 20 m que contó Python.
-- Si coinciden (diferencia 0 en casi todos), el cálculo es correcto. Muestra de 200 tramos.
CREATE OR REPLACE VIEW v_contraste_bancos AS
SELECT t.tramo_id, t.benches_20m AS bancos_python,
       (SELECT COUNT(*) FROM poi p
         WHERE p.tipo = 'banco'
           AND SDO_WITHIN_DISTANCE(p.geom, t.geom, 'distance=20 unit=M') = 'TRUE') AS bancos_oracle
FROM tramos t
WHERE t.tramo_id <= 200;

-- Riesgo de polen por tramo y mes: especies del arbolado (Ayto.) x calendario de polen (CM).
-- Vacía hasta cargar ARBOL, ESPECIE_POLEN y POLEN_MES.
CREATE OR REPLACE VIEW v_polen_tramo_mes AS
SELECT t.tramo_id, pm.mes, pm.tipo_polen, COUNT(*) AS arboles, MAX(pm.nivel) AS nivel
FROM tramos t
JOIN poi a ON a.tipo = 'arbol'
          AND SDO_WITHIN_DISTANCE(a.geom, t.geom, 'distance=15 unit=M') = 'TRUE'
JOIN especie_polen ep ON ep.especie = a.especie
JOIN polen_mes pm ON pm.tipo_polen = ep.tipo_polen
GROUP BY t.tramo_id, pm.mes, pm.tipo_polen;

-- =====================================================================
-- Comprobaciones para ejecutar a mano tras cada carga (resultado esperado en el comentario)
-- =====================================================================
-- 1) Geometrías válidas -> 0 filas
-- SELECT tramo_id FROM tramos WHERE SDO_GEOM.VALIDATE_GEOMETRY_WITH_CONTEXT(geom, 0.05) <> 'TRUE';
-- 2) Todo está en Madrid -> 0 filas
-- SELECT tramo_id FROM tramos t WHERE t.geom.sdo_srid <> 4326
--    OR SDO_GEOM.SDO_MIN_MBR_ORDINATE(geom, 2) NOT BETWEEN 40.3 AND 40.6;
-- 3) Contraste Python vs Oracle -> casi todas las filas con diferencia 0
-- SELECT bancos_python - bancos_oracle AS diferencia, COUNT(*) FROM v_contraste_bancos GROUP BY bancos_python - bancos_oracle;
-- 4) Ningún tramo apunta a una parada inexistente -> 0 (lo garantiza la FK)
-- 5) Resumen
-- SELECT * FROM v_resumen_dataset;  SELECT * FROM v_cobertura;  SELECT * FROM v_avisos_calidad;
