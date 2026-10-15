-- =====================================================================
-- 99 · Borrar TODO el esquema del proyecto para volver a empezar
-- Ejecutar como MADDATA. Después: 02, 03, carga en Python, 04 y 05.
-- =====================================================================
BEGIN
  FOR t IN (SELECT table_name FROM user_tables
            WHERE table_name IN ('TRAMOS','POI','FUENTES','DICCIONARIO','CLIMA_MES_HORA','POLEN_MES',
                                 'ESPECIE_POLEN','ZONAS','CARGAS')) LOOP
    EXECUTE IMMEDIATE 'DROP TABLE ' || t.table_name || ' CASCADE CONSTRAINTS PURGE';
  END LOOP;
  DELETE FROM user_sdo_geom_metadata WHERE table_name IN ('TRAMOS','POI','ZONAS');
  BEGIN ORDS.DELETE_MODULE(p_module_name => 'dataset'); EXCEPTION WHEN OTHERS THEN NULL; END;
  COMMIT;
END;
/
