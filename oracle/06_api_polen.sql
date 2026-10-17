-- =====================================================================
-- 06 · Publica las tablas de polen en la API de solo lectura (módulo 'dataset' del 05)
-- Ejecutar como MADDATA en Database Actions, DESPUÉS de cargar los datos con
-- pipelines/cargar_ambiental.py. Se puede repetir sin problema.
--
--   GET .../dataset/polen/            -> calendario de polen por tipo y mes
--   GET .../dataset/polen/?mes=3      -> solo un mes
--   GET .../dataset/especie_polen/    -> qué especie de árbol produce qué polen
-- =====================================================================
BEGIN
  ORDS.DEFINE_TEMPLATE(p_module_name => 'dataset', p_pattern => 'polen/');
  ORDS.DEFINE_HANDLER(p_module_name => 'dataset', p_pattern => 'polen/', p_method => 'GET',
    p_source_type => ORDS.source_type_collection_feed,
    p_source => 'SELECT tipo_polen, mes, nivel_medio, nivel
                 FROM polen_mes
                 WHERE :mes IS NULL OR mes = TO_NUMBER(:mes)
                 ORDER BY mes, tipo_polen');

  ORDS.DEFINE_TEMPLATE(p_module_name => 'dataset', p_pattern => 'especie_polen/');
  ORDS.DEFINE_HANDLER(p_module_name => 'dataset', p_pattern => 'especie_polen/', p_method => 'GET',
    p_source_type => ORDS.source_type_collection_feed,
    p_source => 'SELECT especie, tipo_polen FROM especie_polen ORDER BY tipo_polen, especie');
  COMMIT;
END;
/

-- Comprobaciones. Resultado esperado: 180, 68 y bajo 159 / medio 9 / alto 12.
-- (Los comentarios van en su propia línea: al final de una sentencia, SQL Scripts de APEX da ORA-03405.)
SELECT COUNT(*) FROM polen_mes;
SELECT COUNT(*) FROM especie_polen;
SELECT nivel, COUNT(*) FROM polen_mes GROUP BY nivel;