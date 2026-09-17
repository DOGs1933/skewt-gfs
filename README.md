# Perfiles GFS y Skew-T en Python

Programa para un servidor Linux. Descarga un recorte regional del GFS 0,25°, extrae perfiles para las estaciones configuradas y genera Skew-T, hodógrafas, CSV, JSON y una galería HTML. No requiere R, Bash de procesamiento, wgrib2 ni descargar el globo completo. Los scripts originales no se modificaron.

## Instalación con ambiente virtual y systemd

Esta entrega funciona directamente en Linux, sin Docker. Git permite versionar el código; `.venv` contiene sus dependencias de Python; systemd mantiene el proceso funcionando. Los datos quedan en una carpeta normal del servidor.

Requisitos: Linux con systemd, Python 3.11 o 3.12 y soporte `venv`, certificados CA y salida HTTPS a NOMADS. La instalación inicial también necesita acceso a los paquetes de Python. En Debian/Ubuntu puede requerir instalar `python3-venv`, `libgomp1` y `fonts-dejavu-core` mediante el administrador. `doctor` verifica que las dependencias meteorológicas se carguen. Reserve inicialmente 2 GB de RAM y varios GB de disco; es una orientación, no una medición de consumo.

1. Clone el repositorio público `https://github.com/DOGs1933/skewt-gfs.git` en una carpeta propia del usuario del servidor, sin espacios en la ruta, y entre en `skewt-gfs`. La descarga por HTTPS no requiere iniciar sesión en GitHub. También puede descomprimir el paquete e ingresar en su carpeta `skewt_gfs`.
2. Revise `config.toml`: coordenadas, nombres, horizonte y conservación de datos. `storage.root = "data"` guarda los productos junto al programa, en una subcarpeta dedicada. También puede indicar una ruta absoluta.
3. Prepare el ambiente virtual y ejecute las pruebas:

```sh
bash scripts/install_linux.sh
```

El instalador crea `.venv`, instala las dependencias, ejecuta `doctor` y las pruebas, y guarda las versiones instaladas en `installed-versions.txt`. No instala paquetes de Python globalmente. Para escoger Python 3.12 explícitamente, use `PYTHON_BIN=python3.12 bash scripts/install_linux.sh`.

4. Haga una primera descarga real:

```sh
.venv/bin/python -m skewt_gfs run
```

Compruebe que termine con `status: complete`. Los productos estarán en `data/index.html` y `data/runs/`; los tiempos y pesos reales estarán en el manifiesto del lote. Revise también los avisos sobre terreno y coordenadas de cada estación.

5. Después de esa comprobación, active la ejecución automática:

```sh
bash scripts/install_systemd.sh
```

Este paso crea y activa un servicio **del usuario actual**. Para que continúe tras cerrar sesión y arranque con el servidor, un administrador debe habilitar la permanencia de ese usuario:

```sh
sudo loginctl enable-linger "$(id -un)"
```

No ejecute el instalador del programa con `sudo`; reserve los permisos de administrador para las dependencias del sistema y la orden anterior.

## Consultar y controlar el servicio

```sh
systemctl --user status skewt-gfs.service
journalctl --user -u skewt-gfs.service -f
.venv/bin/python -m skewt_gfs status
systemctl --user stop skewt-gfs.service       # detener
systemctl --user start skewt-gfs.service      # reanudar
systemctl --user restart skewt-gfs.service    # aplicar cambios de configuración
```

Para desactivar también el arranque automático, use `systemctl --user disable --now skewt-gfs.service`. Detener el servicio no borra los datos.

La galería está en `data/index.html`. Puede copiar la carpeta de un lote completo para consultarla localmente. Si necesita acceso por navegador desde otras computadoras, un servidor web como Nginx puede servir los productos. Se incluye `deploy/nginx.conf` como referencia: su ruta raíz y puerto deben adaptarse a la instalación. El ejemplo restringe el acceso a GRIB, registros y archivos internos. Este paquete no instala ni activa un servidor web, no publica en cuentas externas y no envía correos.

## Git, dependencias y actualizaciones

Se incluye `.gitignore` para excluir el ambiente virtual, los datos, la demostración y los registros. `.gitattributes` mantiene los scripts con finales de línea compatibles con Linux. Para obtener el programa desde el repositorio público:

```sh
git clone https://github.com/DOGs1933/skewt-gfs.git
cd skewt-gfs
```

Para crear sus propios commits necesita una identidad de Git configurada. Revise siempre `git status` antes de confirmar; si cambia la carpeta de datos, añada esa ubicación al archivo de exclusiones. El archivo `config.toml` se versiona junto al programa y no debe contener credenciales.

Para actualizar desde un remoto que usted haya configurado: detenga el servicio, conserve la configuración y la revisión de Git actual, incorpore la revisión elegida, vuelva a ejecutar `bash scripts/install_linux.sh`, haga una corrida real y reinicie el servicio cuando termine correctamente. Los datos quedan fuera del código versionado.

MetPy y ecCodes tienen versiones fijadas en `requirements.txt`; otras dependencias admiten rangos. El archivo `installed-versions.txt` recoge las versiones efectivamente instaladas: consérvelo con el respaldo de una instalación validada si necesita reconstruir ese ambiente. Esto no sustituye el registro de Python y las bibliotecas del sistema operativo.

## Qué hace automáticamente

- Consulta cada 15 minutos y busca los ciclos 00, 06, 12 y 18 UTC, desde el más reciente. La fecha del ciclo no se toma como garantía de disponibilidad: verifica el inventario del último plazo necesario.
- Genera **un lote por ciclo disponible**: cinco localidades por siete horas válidas = **35 perfiles por lote**, normalmente cuatro lotes diarios. La primera hora válida es la próxima hora UTC entera; muestra esa hora y otras seis horas hacia adelante. Si arranca a las 10:20 UTC usando ciclo 06, procesa f005–f011, válidos 11–17 UTC. No llama «ahora» a una hora ya pasada.
- Entre lotes, la ventana mostrada va envejeciendo. No mantiene seis horas futuras nuevas cada minuto. Si esa ventana caduca y todavía no hay ciclo nuevo, puede ampliar los plazos del mismo ciclo, siempre dentro de la antigüedad máxima configurada (18 h).
- Comparte los dos recortes GRIB de cada hora entre las cinco localidades: 14 descargas por lote de siete horas. Mantiene al menos diez segundos entre solicitudes y reintenta fallos temporales con espera. Requiere tanto el producto principal como el complementario antes de considerar listo un ciclo.
- Valida integridad, fechas, campos y perfiles. Publica únicamente lotes completos. Ante un error conserva el anterior, registra el problema en `status.json` y reintenta en el siguiente intervalo. La página avisa cuando su cobertura temporal caducó.
- Evita procesos superpuestos mediante un bloqueo del sistema operativo y reutiliza GRIB cuya suma SHA-256 es correcta. No repite un lote vigente con el mismo ciclo y configuración.

## Cambiar de localidad, carpeta o servidor

Edite `config.toml`; las rutas relativas se interpretan desde ese archivo, sin depender de la carpeta desde la que se lanza Python. Puede usar `--config /ruta/config.toml` para otra configuración. Cada instalación/configuración independiente debe tener una carpeta de datos dedicada. No borre el archivo marcador de esa carpeta.

Cada bloque `[[stations]]` contiene `id`, `name`, `lat` y `lon`; opcionalmente `elevation_m`. Latitudes y longitudes se escriben en grados con signo: sur y oeste negativos. El recorte se calcula automáticamente a partir de las estaciones. Se rechazan regiones de más de 30° por eje; para regiones lejanas use configuraciones separadas. El cruce del antimeridiano no está soportado.

Se incluyeron El Alto, Trinidad, Santa Cruz–Viru Viru, Oruro y Cochabamba. Santa Cruz usa −17.64472, −63.13528: se corrigió la longitud −66.13 que aparecía en el material anterior. Las otras coordenadas se conservaron como ubicaciones aproximadas; confirme los puntos exactos y la elevación de cada estación antes del uso operativo.

`nearest` usa un nodo fijo por estación; `bilinear` usa hasta cuatro nodos fijos y los mismos pesos en todas las variables. En ambos casos se conservan las coordenadas y distancias de los nodos en el JSON. Si falta un campo en esos nodos, no se sustituye por un vecino lejano. Los niveles bajo el terreno del modelo se excluyen; la extracción bilineal excluye un nivel si queda bajo la superficie en cualquiera de sus nodos.

## Origen y selección de los datos

[Filtro principal GFS 0,25°](https://nomads.ncep.noaa.gov/gribfilter.php?ds=gfs_0p25) y [producto complementario pgrb2b](https://nomads.ncep.noaa.gov/gribfilter.php?ds=gfs_0p25b) de NOAA/NOMADS. El programa construye dos URL por hora mediante `filter_gfs_0p25.pl` y `filter_gfs_0p25b.pl`, con fecha, ciclo, plazo, región, variables y niveles. Conserva cada URL y su SHA-256 en `inputs[].sources[]` del manifiesto y en el JSON del perfil. La [documentación oficial del filtro](https://nomads.ncep.noaa.gov/info.php?page=gribfilter) explica su automatización y la pausa entre solicitudes.

Selecciona temperatura, humedad relativa, altura geopotencial y componentes u/v del viento en **39 niveles de presión**: cada 25 hPa desde 1000 hasta 100 hPa, más 70 y 50 hPa. Los 16 niveles intermedios provienen del producto complementario y son datos del modelo, no interpolaciones locales. Añade presión y altura de superficie, temperatura/rocío a 2 m y viento a 10 m.

También descarga **ocho diagnósticos nativos**: CAPE y CIN de superficie y de las capas 0–90, 0–180 y 0–255 hPa sobre el terreno. Se muestran aparte en PNG/SVG y se guardan, con sus capas y unidades, en `gfs_diagnostics` del JSON. No se renombran como MU/ML: esas capas no equivalen a 3 km o 500 m. Son otra formulación del mismo modelo, no observaciones ni una validación independiente de la atmósfera.

Los valores originales y el error de empaquetado GRIB se conservan en JSON. La figura muestra `~0` si un valor no nulo está dentro de esa precisión; una pequeña desviación positiva de CIN por empaquetado no se confunde con un dato ausente.

La versión 1.2 activa `include_secondary_levels = true` por defecto en `[forecast]`, incluso con los archivos de configuración anteriores de 23 niveles. Los niveles suplementarios dentro del intervalo configurado se añaden automáticamente; `plan` muestra la lista efectiva. Puede desactivarse expresamente para una comparación con menos niveles. No se modifican las coordenadas ni la carpeta de datos existentes.

El lector exige todos los campos solicitados y verifica que ambas fuentes coincidan en ciclo, hora y malla. Si falta un archivo o campo, no publica un lote incompleto. La selección del filtro combina variables y niveles, por lo que puede traer campos adicionales; el lector usa únicamente los necesarios. Los archivos y el servicio de NOMADS son operativos: no se debe suponer disponibilidad indefinida de ciclos históricos.

```sh
python -m skewt_gfs plan   # ver horas, recorte y enlaces sin descargar
```

## Productos y conservación

| Archivo/carpeta | Contenido | Conservación por defecto |
|---|---|---|
| `data/index.html` | Galería del último lote promovido | Se actualiza tras éxito |
| `data/status.json` | Última comprobación, error o éxito | Último estado |
| `data/latest.json` | Referencia al último lote | Última referencia |
| `data/runs/<lote>/*.csv` | Perfiles y unidades en las columnas | Permanente |
| `data/runs/<lote>/*.json` | Datos, calidad, ciclo, horas, origen e índices | Permanente |
| `data/runs/<lote>/manifest.json` | Configuración, versiones, pesos y sumas de archivos | Permanente |
| `data/runs/<lote>/*.png, *.svg, *.html` | Figuras y galería del lote | 90 días; se preserva el último lote |
| `data/raw/` | GRIB regionales y su registro | 30 días |
| `data/staging/` | Trabajo parcial de ejecuciones fallidas | 7 días |
| `data/logs/` | Registro de ejecución | Rotación de 5 copias de 2 MB más la actual |

La limpieza se ejecuta después de un lote exitoso y solo sobre carpetas administradas. Las carpetas vacías pueden permanecer. Al vencer las figuras, se conserva el manifiesto con `images_expired=true` y los datos numéricos. Para reconstruir exactamente un lote posteriormente conviene conservar los GRIB y el entorno de versiones; para estudios y comparación de perfiles suelen bastar CSV/JSON. Haga copia de seguridad de configuración, perfiles y manifiestos si necesita un archivo histórico duradero.

## Peso y tiempo: medir en la primera corrida

Para las cinco localidades actuales se midieron **168 160 bytes por plazo** en f006 (104 527 del principal + 63 633 del complementario), frente a 98 645 bytes de la selección anterior. A ese tamaño, siete plazos por cuatro ciclos serían aproximadamente **4,7 MB/día**, más inventarios, reintentos y posibles ampliaciones. Es una estimación basada en un archivo real: el tamaño varía por hora y región. No se multiplica por cinco estaciones, porque comparten el recorte. Las bibliotecas se descargan durante la instalación y son mucho mayores.

En `manifest.json`, `inputs[].bytes` registra el peso de cada GRIB ensamblado; `inputs[].sources[].bytes` conserva los tamaños de los dos archivos descargados. El ensamblado es local y no añade tráfico, pero guardar fuentes y ensamblado duplica aproximadamente el espacio de GRIB en disco. `transferred_bytes` incluye las respuestas leídas por ese descargador (incluidas consultas y reintentos), sin cabeceras HTTP/TLS. `elapsed_seconds` mide la producción desde la primera descarga hasta preparar los productos; la búsqueda del ciclo y la limpieza añaden tiempo al proceso. Los intentos fallidos también consumen tráfico; el manifiesto no es una auditoría completa de red.

La espera mínima entre 14 descargas nuevas suma aproximadamente 130 segundos; se añaden consultas de disponibilidad, transferencia, decodificación y gráficos. La caché evita repetir descargas válidas. Las mediciones reales se documentan en [VALIDACION.md](VALIDACION.md); mida también la primera corrida de su servidor. Para disco, añada las figuras y los días de retención.

## Alcance meteorológico

Se trata de perfiles deterministas de GFS, no radiosondeos observados ni un modelo nuevo calculado localmente. La nueva corrida aporta otra inicialización del pronóstico; esta aplicación no estima su incertidumbre ni asigna una mejora porcentual automática.

La presión es la del nivel original. HGT ya representa altura geopotencial en metros y no se divide por gravedad. El viento mantiene u/v y una dirección meteorológica «desde donde sopla», con calma sin dirección definida. El rocío en niveles de presión usa Magnus sobre agua y humedad relativa acotada a [0,1 %, 100 %] cuando sea necesario, con aviso en el JSON; en aire muy frío es una aproximación que merece revisión si se requiere termodinámica precisa.

La base del perfil combina presión superficial, T/Td a 2 m y viento a 10 m del modelo. No equivale a una observación en el aeropuerto. La orografía de GFS puede diferir mucho en los Andes; añadir `elevation_m` permite advertir diferencias, **no corregir automáticamente el terreno**. Los niveles eliminados y avisos acompañan cada producto.

Calcula CAPE/CIN y LCL para parcelas SB (superficie), MU (máxima theta-e en los primeros 3 km) y ML (capa hasta 500 m AGL), con MetPy y corrección de temperatura virtual. El LCL se muestra en metros sobre el terreno GFS y también en hPa para SB. Cuando no existe LFC, CIN aparece como `s/LFC`, con `null` en JSON; el cero convencional de MetPy se conserva por separado. No debe interpretarse como ausencia de inhibición. La trayectoria dibujada es SB e incluye el LCL.

La [auditoría de CAPE/CIN](AUDITORIA_CAPE_CIN.md) explica las diferencias con R, incluida su posible selección de niveles bajo tierra. No se promete igualdad numérica con thundeR. Se interpola a una malla numérica de hasta 1 hPa para integrar; esto no aumenta la resolución meteorológica del GFS. No se reproducen el envío de correo ni todos los índices de convección/cizalladura anteriores. El PNG usa los diagramas meteorológicos de MetPy; el SVG es una vista complementaria con isobaras, isotermas, adiabáticas secas y hodógrafa, sin toda la retícula húmeda del PNG.

## Pruebas y comandos

```sh
python -m skewt_gfs doctor
python -m unittest discover -s tests -v
python -m skewt_gfs demo
python -m skewt_gfs run
python -m skewt_gfs watch
python -m skewt_gfs status
```

`demo` crea `demo/index.html` con datos sintéticos expresamente rotulados y sin índices MetPy; nunca los publica en `data`. `doctor` comprueba dependencias y configuración. `run` hace un intento y devuelve código distinto de cero ante fallos. `watch` repite automáticamente y conserva errores en el registro. Las pruebas científicas usan GRIB y perfiles sintéticos para comprobar lectura, unidades, MetPy y gráficos, no validan la precisión meteorológica contra observaciones.

Para reprocesar un ciclo que todavía exista en NOMADS, use fechas reales disponibles:

```sh
python -m skewt_gfs run --cycle 2026-09-16T06:00:00Z --at 2026-09-16T10:00:00Z --force
```

Las fechas anteriores son un ejemplo de sintaxis, no una garantía de disponibilidad. Un lote histórico se archiva y no sustituye a otro con ciclo y vigencia más recientes. `--force` crea otro lote, sin sobrescribir el archivo anterior.

**Verificación de la versión 1.2.0:** 34 pruebas aprobadas, incluidas ecCodes, MetPy, gráficos, archivos complementarios, capas CAPE/CIN, fallos de descarga y regresiones científicas. Se comprobó la publicación completa de 35 perfiles reales (f000–f006 del ciclo 16/09/2026 18 UTC), usando los dos productos y los diagnósticos nativos. Véase [VALIDACION.md](VALIDACION.md). No se ejecutó systemd ni se modificó el servidor remoto desde este equipo.
