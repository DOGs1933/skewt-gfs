# CAPE, CIN y LCL: comparación con los scripts R

Revisión del 17 de septiembre de 2026 UTC; versión Python 1.1.0.

## Resultado del caso reportado

Se descargó el GFS 0,25° del 16/09/2026 18 UTC, plazo f006, válido el
17/09/2026 00 UTC. El resultado anterior de Cochabamba se reprodujo:
SB CAPE = 0 J/kg, CIN convencional de MetPy = 0 J/kg y LCL = 620,7115 hPa.
El terreno GFS es 3028,219 m. Interpolando las alturas del mismo perfil,
el LCL está aproximadamente a **1117 m sobre ese terreno**. No son 621 metros.
No se encontró LFC para esa parcela: un CIN convencional igual a cero
no significa que la atmósfera carezca de inhibición.

Archivo regional: 98 645 bytes, 123 mensajes GRIB2, SHA-256
`9060c2b9a569f19cf8db1bdeac4135ac7787ae1dfd56f62b1fe9a7a0d633a72c`.
El perfil termodinámico de ese caso está en `tests/fixtures/` para regresión sin red.

## Qué hacía R

Los tres scripts llaman a `thunder::sounding_save(..., parcel="SB")`.
Ese argumento elige la trayectoria dibujada, pero la tabla de thundeR
contiene índices SB, MU y ML. La versión Python inicial solo incluía SB.
thundeR presenta LCL en altura sobre su primer nivel; Python presentaba presión.

El R de 2026 promedia los cuatro nodos válidos más cercanos por variable y nivel.
Luego usa `complete.cases` suponiendo que así desaparecen los niveles bajo tierra.
Esa suposición falla: GFS puede proporcionar valores finitos extrapolados bajo el terreno.
También puede cambiar de nodos al descartar valores faltantes antes de buscar vecinos.

Se reconstruyó esa selección de datos sobre el mismo recorte GFS, sin ejecutar R:

| Localidad | Primer nivel con esa lógica | Altura de ese nivel | Terreno del nodo usado por Python |
|---|---:|---:|---:|
| El Alto | 1000 hPa | 172 m | 4002 m |
| Oruro | 1000 hPa | 118 m | 3888 m |
| Cochabamba | 1000 hPa | 142 m | 3028 m |

Como diagnóstico, calcular SB con **MetPy** sobre esos datos seleccionados como R
produce aproximadamente 3875, 454 y 3046 J/kg, respectivamente. **No son resultados
ejecutados con thundeR ni estimaciones válidas de CAPE superficial**: muestran la
sensibilidad a arrancar desde una superficie artificial bajo tierra.

Los scripts anteriores, de 2024 y LFA, también cambiaban los datos de entrada:
estimación simplificada de Td, presión reconstruida con atmósfera estándar y
recortes de niveles específicos por estación. No constituyen una referencia
numérica equivalente al perfil actual, con presión superficial y T/Td a 2 m.

## Cambios en Python

- SB conserva la superficie del modelo; se mantienen los controles que excluyen
  niveles bajo el terreno. No se ajusta la temperatura para forzar CAPE positiva.
- Se añaden MU (máxima theta-e en los primeros 3 km) y ML (capa hasta 500 m AGL).
- La integración usa MetPy 1.7.1 y temperatura virtual. Se interpola T/Td en log(p)
  con separación máxima de 1 hPa, conservando niveles nativos y límites de capa.
  Esta malla numérica reduce la dependencia de niveles muy separados; **no añade
  información ni resolución al GFS**. La trayectoria SB dibujada usa esa misma malla
  e incluye el LCL.
- CIN se guarda como `null` y se muestra `s/LFC` cuando no hay LFC. El resultado
  convencional de la biblioteca se conserva en `parcels.*.CIN_metpy_J_kg`.
- LCL, LFC y EL incluyen presión y altura aproximada AGL, interpolando HGT en log(p)
  y restando el terreno del modelo. No se extrapolan alturas fuera del perfil.
- Si existe LFC pero falta EL, se indica que CAPE está limitado por el tope del perfil.
- La versión del programa cambia la huella del lote para regenerar los productos.

## Diferencias que siguen siendo deliberadas

No se promete igualdad exacta con thundeR: son motores numéricos diferentes y los
scripts no fijaban su versión. thundeR integra sobre una malla vertical en altura;
su código también anula CIN cuando CAPE es cero. MetPy integra en log(p), seleccionando
LFC inferior y EL superior. ML usa promedios ponderados por presión de temperatura
potencial y razón de mezcla; thundeR utiliza su propio promedio de capa. Se conserva
la profundidad física de 500 m, pero no una réplica numérica de esa fórmula.

Tampoco se reemplaza `nearest` por el promedio variable de cuatro nodos del R:
eso cambiaría el perfil y su terreno. La extracción bilineal ya disponible permite
usar una selección consistente si se configura expresamente.

## Referencias verificadas

- [thundeR: significado de parcel y altura de referencia](https://bczernecki.github.io/thundeR/reference/sounding_plot.html).
- [Código thundeR revisado, commit c93a34e](https://github.com/bczernecki/thundeR/tree/c93a34e9cb85a95401b136a1128205349b5ee63d):
  `R/sounding_plot.R` (tabla SB/MU/ML), `src/main.cpp` (`LapseRate::finish`,
  `putMaxTHTE`, `putMeanLayerParameters`, `sounding_default2`).
- [MetPy: CAPE/CIN](https://unidata.github.io/MetPy/latest/api/generated/metpy.calc.cape_cin.html).
- [MetPy: parcela mezclada](https://unidata.github.io/MetPy/latest/api/generated/metpy.calc.mixed_parcel.html).

Los scripts R originales no fueron modificados ni incluidos en este repositorio.

## Ampliación posterior: versión 1.2.0

La descarga incluye ahora los 16 niveles intermedios del
[producto complementario GFS](https://nomads.ncep.noaa.gov/gribfilter.php?ds=gfs_0p25b):
875, 825, 775, 725, 675, 625, 575, 525, 475, 425, 375, 325, 275, 225, 175 y 125 hPa.
Son campos originales del producto, no interpolaciones creadas por este programa.
Se solicitan temperatura, RH, altura y u/v en los 39 niveles combinados.

El producto principal aporta además CAPE/CIN nativos de superficie y de las capas
0–90, 0–180 y 0–255 hPa sobre el terreno. Se conserva su identidad: no se asume
que sean numéricamente equivalentes a nuestros SB, MU de 3 km o ML de 500 m.

En f006 de la misma corrida, **GFS también proporciona CAPE superficial cero en
las cinco localidades**. Incorporar más niveles no cambia ese resultado, pero sí
otros índices: MU CAPE de El Alto pasa de 5,12 a 13,44 J/kg. En Cochabamba, la
altura interpolada del LCL SB pasa de 1117 a 1118 m AGL; la presión sigue siendo
620,7115 hPa. No se trata de una corrección de la elevación real de la estación.

Las comparaciones de otras horas y los límites de esta comprobación figuran en
[VALIDACION.md](VALIDACION.md). Los pequeños residuos de CAPE/CIN debidos al
empaquetado GRIB se distinguen usando su `packingError`, conservando el número
original en JSON.
