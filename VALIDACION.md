# Estado de verificación

## Versión 1.2.0: descarga ampliada

17 de septiembre de 2026 UTC, mismas bibliotecas que se detallan en la revisión anterior.

- **34 pruebas aprobadas, ninguna omitida**: 26 de lógica/descarga/calidad,
  6 de índices y 2 de integración científica con múltiples verificaciones GRIB.
- Activación automática de los niveles complementarios en la configuración existente:
  39 niveles solicitados; 16 provienen de `pgrb2b`.
- Comprobación de disponibilidad de ambas fuentes, caché independiente y reconstrucción
  del ensamblado si se corrompe. Un complementario ausente no genera un perfil parcial.
- Lectura estricta de ocho diagnósticos CAPE/CIN, con capas en Pa convertidas a hPa.
  Se rechazan campos faltantes o discrepancias de ciclo, hora y malla.
- Prueba de extracción con los mismos nodos y pesos del perfil, y de tolerancia al
  error de empaquetado GRIB sin perder los valores originales.
- **35 perfiles reales publicados** con CSV, JSON, SVG, PNG y manifiesto: cinco
  localidades, f000 a f006 del ciclo 16/09/2026 18 UTC. Se inspeccionó visualmente
  el PNG con las tablas MetPy y GFS.

El primer lote ampliado tardó **161,875 s**, sin contar la búsqueda previa de
disponibilidad. Descargó 1 026 785 bytes, incluidos los inventarios; f006 ya estaba
en caché. La suma de las dos fuentes para los siete plazos fue **1 178 561 bytes**.
Son mediciones de este equipo Windows, no del servidor Linux. Un día de cuatro
lotes equivalentes implica aproximadamente **4,7 MB** de GRIB más consultas y reintentos.

Comparaciones obtenidas (J/kg):

| Caso | SB CAPE MetPy, 39 niveles | CAPE GFS, superficie |
|---|---:|---:|
| El Alto f000 | 137,86 | 61 |
| Cochabamba f000 | 87,52 | 41 |
| Trinidad f000 | 54,92 | 0 |
| Las cinco localidades f006 | 0 | 0 |

Las diferencias entre formulaciones se conservan, no se fuerzan a coincidir.
GFS es una referencia del mismo modelo, no un radiosondeo observado.
En El Alto f006, MU CAPE cambia de 5,12 a 13,44 J/kg al incorporar los niveles
intermedios. El CAPE GFS de la capa 0–180 hPa es 24 J/kg; esa capa no equivale a
nuestra búsqueda MU en 3 km. El nuevo caso está incluido en `tests/fixtures/`.

El CIN nativo puede contener residuos de empaquetado: se verificó, por ejemplo,
un valor de +0,4213 J/kg con `packingError` de 0,5000 J/kg. Se conserva ese valor
en JSON y se muestra `~0`, sin tratarlo como dato faltante.

No se modificaron `config.toml`, las dependencias ni el servidor remoto. Los
pasos de actualización al final de este documento siguen siendo aplicables.

## Histórico: versión 1.1.0, antes de ampliar los datos

17 de septiembre de 2026 UTC. Versión 1.1.0; Windows, Python 3.12.14,
MetPy 1.7.1, ecCodes Python 2.44.0, NumPy 2.5.3, SciPy 1.18.1 y Matplotlib 3.11.2.

| Comprobación | Resultado |
|---|---|
| Pruebas de lógica, calidad, caché, bloqueo, publicación y descarga simulada | 22 aprobadas |
| Integración ecCodes: GRIB sintético, campos, fechas y unidades | Aprobada |
| Integración MetPy/PNG para las cinco localidades | Aprobada |
| Regresiones CAPE/CIN/LCL: inestabilidad, inhibición, ausencia de LFC, alturas, cobertura y convergencia | 5 aprobadas |
| Total | **29 aprobadas, ninguna omitida** |
| Compilación de módulos Python | Correcta |
| Generación completa de demostración | 35 perfiles CSV/JSON/SVG |
| Inspección visual del PNG corregido de Cochabamba | Tabla SB/MU/ML, CIN s/LFC y LCL en m AGL/hPa legibles |
| Descarga real NOMADS | GFS 16/09/2026 18 UTC, f000/f006/f012 |
| Publicación completa con esos datos reales | **15 perfiles**, CSV/JSON/SVG/PNG y manifiesto |
| Activación de systemd | Pendiente; este equipo no es el servidor Linux |
| Instalación remota | Pendiente de datos del servidor y acceso SSH |

El lote real de 15 perfiles tardó 19,078 s en este equipo, con 196 917 bytes
descargados y reutilizando f006 desde caché. No es una estimación del tiempo del
servidor ni de un lote de 35 perfiles descargado desde cero. Se conservaron los
SHA-256, URL, tamaños, fechas y versiones en el manifiesto de la prueba.

Ejemplos de resultados reales (CAPE y CIN en J/kg):

| Localidad / plazo | SB CAPE | SB CIN | MU CAPE | MU CIN | ML CAPE | ML CIN |
|---|---:|---:|---:|---:|---:|---:|
| El Alto f000 | 130,19 | 0 | 130,19 | 0 | 44,77 | 0 |
| Cochabamba f000 | 81,41 | 0 | 81,41 | 0 | 4,34 | −36,30 |
| El Alto f006 | 0 | sin LFC | 5,12 | −1,53 | 0 | sin LFC |
| Cochabamba f006 | 0 | sin LFC | 0 | sin LFC | 0 | sin LFC |

En Cochabamba f006 el LCL SB es 620,7115 hPa, aproximadamente 1117 m sobre el
terreno GFS. La [auditoría](AUDITORIA_CAPE_CIN.md) explica la comparación con R y
por qué no corresponde forzar CAPE positiva a partir de niveles bajo tierra.

Estas pruebas verifican implementación, datos de entrada y publicación; no
validan la exactitud meteorológica de GFS frente a radiosondeos observados.
El motor R/thundeR se inspeccionó en código fuente, pero no se ejecutó una
comparación numérica R/Python con entradas idénticas.

Matplotlib 3.11.2 emite avisos de funciones obsoletas usadas internamente por
MetPy al dibujar; las imágenes y las pruebas se completaron correctamente.

Los scripts R originales no fueron modificados ni publicados.

Para actualizar en Linux sin cambiar la configuración local:

```bash
cd /srv/skewt-gfs
git pull --ff-only
.venv/bin/python -m unittest discover -s tests -v
```

Si el servicio de usuario ya está activo, detenerlo antes de la corrida manual
para evitar competir por el bloqueo:

```bash
systemctl --user stop skewt-gfs.service
.venv/bin/python -m skewt_gfs run --force
systemctl --user start skewt-gfs.service
```

Si no se instaló el servicio, ejecutar solamente la línea de Python.
La versión nueva cambia la huella del lote: se regeneran los productos y se
conservan los anteriores. No se cambiaron dependencias ni `config.toml`.
