-- =====================================================================
-- 01 · Crear el esquema del proyecto
-- Ejecutar UNA VEZ como ADMIN en Database Actions > SQL (botón "Run Script", F5)
-- Contraseña: 12-30 caracteres, con mayúscula, minúscula y número, sin comillas ni "admin".
-- =====================================================================

CREATE USER maddata IDENTIFIED BY "MADcompana_pass1";

GRANT CREATE SESSION, CREATE TABLE, CREATE VIEW, CREATE SEQUENCE,
      CREATE PROCEDURE, CREATE SYNONYM TO maddata;

ALTER USER maddata QUOTA UNLIMITED ON data;

-- Habilita REST (ORDS) y el acceso a Database Actions para MADDATA.
-- Después podrás entrar en Database Actions como MADDATA en .../ords/maddata/_sdw/
BEGIN
  ORDS_ADMIN.ENABLE_SCHEMA(
    p_enabled             => TRUE,
    p_schema              => 'MADDATA',
    p_url_mapping_type    => 'BASE_PATH',
    p_url_mapping_pattern => 'maddata',
    p_auto_rest_auth      => FALSE);   -- REST de solo lectura sin login: el dataset es abierto
  COMMIT;
END;
/
