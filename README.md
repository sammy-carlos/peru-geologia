# Geología del Perú (INGEMMET)

La geología y la geomorfología de todo el Perú publicadas por INGEMMET en GEOCATMIN,
descargadas una vez y guardadas en formatos abiertos para usarlas en otros proyectos:

- **GeoParquet**, una tabla por capa, con todos los atributos originales: para consultar y
  calcular (DuckDB, QGIS, Python).
- **PMTiles** vectorial, un solo archivo con todas las capas: para mostrarlas en mapas web
  sin servidor (MapLibre).

El código vive en este repositorio y los datos en Hugging Face
(`WaterCompetenceCenter/peru-geologia`).

## Capas

| Id | Capa | Servicio GEOCATMIN | Geometría |
|---|---|---|---|
| `litologia_100k` | Geología 1:100 000 | `SERV_GEOLOGIA_100K_INTEGRADA` / 6 | polígonos |
| `fallas_100k` | Fallas 1:100 000 | `SERV_GEOLOGIA_100K_INTEGRADA` / 3 | líneas |
| `pliegues_100k` | Pliegues 1:100 000 | `SERV_GEOLOGIA_100K_INTEGRADA` / 4 | líneas |
| `litologia_50k` | Litología 1:50 000 (integrada) | `SERV_GEOLOGIA_50K_INTEGRADA` / 7 | polígonos |
| `fallas_50k` | Fallas 1:50 000 (integrada) | `SERV_GEOLOGIA_50K_INTEGRADA` / 2 | líneas |
| `pliegues_50k` | Pliegues 1:50 000 (integrada) | `SERV_GEOLOGIA_50K_INTEGRADA` / 3 | líneas |
| `geomorfologia` | Geomorfología | `SERV_GEOMORFOLOGIA` / 0 | polígonos |

La lista está en `layers.json`; agregar una capa es agregar una entrada y volver a correr los
pasos.

## Qué se conserva

- Todos los atributos de INGEMMET, con su nombre y tipo originales. No se agrupan, renombran
  ni derivan unidades.
- La fuente de cada elemento: `source`, `source_layer_url` y `retrieved_at` (cuándo se listó la
  capa).
- Las geometrías en EPSG:4326, tal como las entrega el servicio. Las que llegan inválidas se
  reparan y quedan marcadas en `geometry_repaired`.
- La descarga cruda, lote por lote, en `raw/` (con la descripción de cada capa y su simbología
  oficial en `layer.json`), para poder reconstruir todo aunque el servicio cambie.

## Cómo se reproduce

```bash
python3 scripts/download.py            # 1. descarga cruda en raw/ (horas; se puede cortar y retomar)
uv run scripts/build_master.py         # 2. un GeoParquet por capa en data/, con su reporte
```

El paso 3, el PMTiles, usa [tippecanoe](https://github.com/felt/tippecanoe).

## Fuente y uso

Datos: Instituto Geológico, Minero y Metalúrgico (INGEMMET), GEOCATMIN,
<https://geocatmin.ingemmet.gob.pe>. Toda publicación derivada debe citar a INGEMMET como
fuente. Antes de publicar este repositorio hay que revisar las condiciones de uso de
INGEMMET.
