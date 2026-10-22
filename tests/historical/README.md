# Implementaciones retiradas

Este directorio conserva el cerebro híbrido, los clientes de generación Apple y MLX,
la ejecución especulativa, MLX distribuido y el grafo anterior para verificar sus antiguas
fronteras de privacidad. No forma parte
del paquete `aegis_core` ni se instala en Jarvis.

Las pruebas del comportamiento vigente importan directamente `build_nvidia_swarm_graph`
de producción, con proveedores simulados que implementan `complete_nvidia_only`.
Solo las regresiones de rutas retiradas llaman explícitamente a
`build_historical_swarm_graph`. Las pruebas mixtas seleccionan ambos constructores
de forma visible, según el contrato probado. No se añaden capacidades de producto a
esta copia.

El daemon utiliza `build_nvidia_swarm_graph`. Rechaza configuración local y la
completación solo llama `complete_nvidia_only`; una caída de NVIDIA nunca cambia de
proveedor. `tests/test_production_orchestration.py` comprueba la importación del daemon
sin módulos históricos y la ausencia de fallback.

`providers/mlx_provider.py` de producción conserva únicamente la transcripción de voz
con Whisper y su error declarado. La generación de texto, la verificación de borradores
y la decisión de enviar solicitudes a un cerebro local solo existen en estas regresiones.
