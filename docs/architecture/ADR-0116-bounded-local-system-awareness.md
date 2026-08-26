# ADR-0116: conciencia local acotada del sistema

- Estado: aceptado
- Fases: 1, 3, 4 y 5

## Decisión

1. Audio, red y rendimiento comparten la herramienta de bajo riesgo
   `system_observe_status(domain)`, limitada por esquema a esos tres dominios.
2. Red y rendimiento usan consultas fijas, rutas absolutas, entorno mínimo, salida acotada y timeout
   de dos segundos. Audio llama directamente a CoreAudio. No existe shell, argumento libre, monitor
   residente o persistencia.
3. Audio devuelve únicamente volumen de salida y estado de silencio. No inicia ni consulta el
   dispositivo de entrada.
4. Red inspecciona solo interfaces físicas `enN` activas. Devuelve cantidad y disponibilidad de
   IPv4/IPv6, nunca IP, MAC, SSID, ruta, nombre de interfaz o contenido original.
5. El resultado de red se denomina conectividad local. No se interpreta como acceso a Internet;
   comprobar un recurso remoto pertenece a las herramientas web y a su política separada.
6. Rendimiento devuelve carga media de un minuto, núcleos lógicos y porcentaje agregado de memoria
   disponible. No enumera procesos, aplicaciones o asignaciones de memoria.
7. Las órdenes exactas se autorizan, ejecutan y sintetizan de forma determinista. Un fallo o contrato
   inválido produce una frase local fija y nunca alcanza un modelo.

## Motivo

macOS ya ofrece estas señales con menor latencia y mayor privacidad que una inferencia. Un contrato
único elimina definiciones repetidas mientras conserva límites distintos por dominio y la política
de denegación por defecto. El filtrado en el ejecutor impide que identificadores locales entren en
memoria, auditoría o prompts.

## Límites

- La conectividad local no prueba DNS, ruta de salida, captive portal o acceso a Internet.
- El volumen configurado no prueba que un dispositivo físico esté produciendo sonido.
- La carga media no equivale a porcentaje instantáneo de CPU.
- El porcentaje de memoria es la estimación agregada de `memory_pressure -Q` en ese instante.
- Consultas compuestas o no exactas conservan el planificador y las políticas normales.
