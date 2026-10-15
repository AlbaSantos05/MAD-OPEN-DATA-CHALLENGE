# Base de datos en Oracle Autonomous Database

Aquí vive el **dataset del proyecto**: la red peatonal de Chamberí enriquecida (`TRAMOS`), los recursos (`POI`), el catálogo de fuentes (`FUENTES`) y el diccionario de datos (`DICCIONARIO`). Python la llena, y el motor, la app y el jurado la leen.

```
data/raw  ──► pipelines/construir_dataset.py ──► data/dataset/*.geojson ──► pipelines/cargar_oracle.py ──► Oracle
                                                                                                          ├─ APEX (jurado)
                                                                                                          └─ ORDS /dataset/ (app)
```

## Orden de ejecución (la primera vez, ~1 h)

| # | Qué | Dónde | Como |
|---|-----|-------|------|
| 1 | Crear la Autonomous Database | Consola OCI | — |
| 2 | `01_crear_usuario.sql` | Database Actions › SQL | ADMIN |
| 3 | `02_tablas.sql` | Database Actions › SQL | MADDATA |
| 4 | `03_fuentes_y_diccionario.sql` | Database Actions › SQL | MADDATA |
| 5 | `python pipelines/construir_dataset.py` | Tu ordenador | — |
| 6 | `python pipelines/cargar_oracle.py` | Tu ordenador | MADDATA |
| 7 | `04_vistas_y_calidad.sql` | Database Actions › SQL | MADDATA |
| 8 | `05_api_rest.sql` | Database Actions › SQL | MADDATA |
| 9 | Comprobaciones (final de `04`) | Database Actions › SQL | MADDATA |

En Database Actions ejecuta cada fichero completo con **Run Script (F5)**, no con Run Statement.

## 1. Crear la base de datos

1. Consola de OCI › menú › *Oracle Database* › *Autonomous Database* › **Create Autonomous Database**.
2. *Display name* y *Database name*: `MADOPENDATA`.
3. *Workload type*: **Transaction Processing**. Marca **Always Free**. Elige la versión más reciente (26ai o 23ai).
4. Contraseña de `ADMIN`: guárdala en un gestor de contraseñas, nunca en el repo.
5. *Network access*: **Secure access from everywhere**. Deja marcado *Require mutual TLS (mTLS)*.
6. Cuando esté en verde (*Available*): **Database connection › Download wallet**. Pon una contraseña al wallet y **descomprime** el zip en una carpeta fuera del repo (por ejemplo `Documentos/Wallet_MADOPENDATA`).

## 2–4. Esquema, tablas y fuentes

1. En la página de la base: **Database Actions › SQL** (entras como ADMIN).
2. Abre `01_crear_usuario.sql`, **cambia la contraseña** y ejecútalo con F5.
3. Cierra sesión y vuelve a entrar en Database Actions como **MADDATA**. La URL es la misma cambiando `/ords/admin/` por `/ords/maddata/`.
4. Ejecuta `02_tablas.sql` y después `03_fuentes_y_diccionario.sql`. Comprueba con `SELECT COUNT(*) FROM fuentes;` que devuelve 11.

## 5–6. Construir y cargar el dataset

```bash
pip install -r requirements.txt
python pipelines/construir_dataset.py        # ~5 s; escribe data/dataset/
cp .env.example .env                          # y rellénalo
python pipelines/cargar_oracle.py --dry-run   # valida sin conectarse
python pipelines/cargar_oracle.py             # carga de verdad
```

Debe terminar con algo como `Carga terminada: 4384 POI y 5082 tramos en Oracle; geometrías no válidas: 0.`

`ORACLE_DSN` es uno de los alias de `tnsnames.ora` del wallet. Usa `madopendata_low` para cargas y consultas.

## 7–9. Vistas, API y comprobaciones

Ejecuta `04_vistas_y_calidad.sql` y `05_api_rest.sql`. Después prueba en el navegador:

```
https://<tu-host>/ords/maddata/dataset/resumen/
https://<tu-host>/ords/maddata/dataset/tramos.geojson
```

`<tu-host>` es el mismo dominio que ves en la URL de Database Actions.

Comprobaciones que tienen que salir bien (están comentadas al final de `04`):
- geometrías no válidas: 0 filas;
- `v_contraste_bancos`: Oracle y Python cuentan los mismos bancos en casi todos los tramos;
- `v_cobertura` y `v_avisos_calidad`: para la diapositiva de calidad de datos.

## Volver a cargar

- **Cambian los datos:** repite los pasos 5 y 6. El cargador borra y vuelve a cargar `POI` y `TRAMOS`, así que siempre deja la base igual que los ficheros.
- **Cambia el diseño de las tablas:** ejecuta `99_borrar_todo.sql` y repite desde el paso 3.

## Problemas frecuentes

| Error | Causa | Solución |
|---|---|---|
| `ORA-01017 invalid username/password` | Contraseña o usuario mal | Revisa `.env`; el usuario es `maddata` |
| `DPY-6005` / `DPY-4011` al conectar | Wallet sin descomprimir, ruta mal o contraseña del wallet mal | `ORACLE_WALLET_DIR` debe apuntar a la carpeta que contiene `tnsnames.ora` y `ewallet.pem` |
| `ORA-13203 failed to read USER_SDO_GEOM_METADATA` | Índice creado sin metadatos | Ejecuta `99`, luego `02` entero |
| `ORA-02291 integrity constraint ... FUENTE_FK violated` | Falta una fuente | Añádela en `03` y vuelve a ejecutar esa línea |
| `ORA-02266` al hacer TRUNCATE | Tabla con FK entrante | Ya lo resuelve el cargador (usa DELETE en `POI`) |
| La base se para sola | Las Always Free se detienen tras 7 días sin uso | Botón *Start* en la consola |

## Lo que sigue (sábado y domingo)

- Descargar `fuentes.csv` y `arbolado.csv` del Ayuntamiento a `data/raw/`. `construir_dataset.py` los detecta solo y rellena `dist_water_m` y `trees_20m`.
- Rellenar `POLEN_MES`, `ESPECIE_POLEN` y `CLIMA_MES_HORA` a partir de los históricos de la Comunidad.
- App APEX «Explorador de datos» sobre este esquema.
