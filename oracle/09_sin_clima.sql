-- =====================================================================
-- 09 · Elimina CLIMA_MES_HORA (decisión del equipo: no se usa temperatura)
-- Motivo: la meteorología de la CM (CM_METEO) no tiene estaciones en Madrid capital y no
-- se incorpora otra fuente de temperatura. Regla de la entrega: ninguna tabla vacía.
-- Ejecutar como MADDATA (APEX › SQL Workshop › SQL Scripts). Se puede repetir.
-- =====================================================================
DECLARE
  no_existe EXCEPTION;
  PRAGMA EXCEPTION_INIT(no_existe, -942);
BEGIN
  EXECUTE IMMEDIATE 'DROP TABLE clima_mes_hora PURGE';
EXCEPTION
  WHEN no_existe THEN NULL;
END;
/

DELETE FROM diccionario WHERE tabla = 'CLIMA_MES_HORA';

UPDATE fuentes SET
  limitaciones = 'Comprobado en data/raw/meteorologia.csv: solo magnitudes meteorológicas (81-89) y ningún registro del municipio de Madrid (079). La red de la CM no tiene estaciones en la capital, y el equipo decidió no usar temperatura, así que la tabla CLIMA_MES_HORA se eliminó. El fichero se conserva en el repositorio como prueba.'
WHERE fuente_id = 'CM_METEO';

COMMIT;

-- Comprobación: debe devolver 0 (la tabla ya no existe)
SELECT COUNT(*) FROM user_tables WHERE table_name = 'CLIMA_MES_HORA';