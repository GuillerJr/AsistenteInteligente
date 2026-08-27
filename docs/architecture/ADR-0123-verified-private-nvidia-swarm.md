# ADR-0123: enjambre NVIDIA verificado con frontera privada

Fecha: 2026-08-26

## Decisión

1. El routing permanece determinista y local. `nemotron-3.5-lightning-30b-a3b` planifica solo las
   solicitudes que no tienen una ruta nativa exacta.
2. `deepseek-v4-pro-0813` analiza código y ciberseguridad. Su fallback es
   `deepseek-v4-flash-0731`.
3. Las solicitudes de seguridad clasificadas localmente como riesgo alto o crítico ejecutan en
   paralelo un segundo análisis independiente con `nemotron-3-super-120b-a12b`; el respaldo es
   `gpt-oss-120b`. El revisor no recibe herramientas.
4. `nemotron-3-nano-omni-30b-a3b-reasoning` conserva visión, con `muse-glimmer-30b` como respaldo.
5. El synthesizer rápido compara consenso, incertidumbre y desacuerdos de los análisis de alto
   riesgo. Ningún modelo autoriza o ejecuta acciones.
6. La carga NVIDIA excluye memoria persistente, perfil, historial conversacional e identidad del
   hablante. Una redacción local elimina patrones comunes de credenciales, correo, teléfono y
   usuario de rutas macOS de la solicitud actual.
7. Autorizaciones y resultados de herramientas solo pueden sintetizarse on-device. Un fallo del
   modelo local produce una respuesta local de privacidad, nunca fallback NVIDIA.
8. Respuestas vacías o malformadas conmutan al modelo alterno. Un endpoint `404` o `410` queda fuera
   durante la vida del cliente para no pagar de nuevo su latencia.
9. `probe-nvidia-swarm` verifica los cuatro primarios con entradas sintéticas y
   `probe-nvidia-tools` valida function calling sin ejecutar nada.

## Evidencia

- Catálogo gratuito NVIDIA NIM: 47 endpoints observados el 26 de agosto de 2026.
- DeepSeek V4 Pro: endpoint gratuito, contexto 1M, orientado a código, razonamiento y flujos
  agénticos.
- Nemotron 3 Super: endpoint gratuito, contexto 1M, function calling, salida estructurada y
  razonamiento declarados por NVIDIA.
- Probes reales: Lightning, DeepSeek V4 Pro y Nemotron Super respondieron con su modelo primario;
  DeepSeek emitió el function call sintético exacto. Vision necesitó un fallback transitorio en el
  primer probe y respondió con Nemotron Omni en el segundo.

## Consecuencias

- Conversación y planificación común mantienen baja latencia; el modelo de 120B solo se paga cuando
  el riesgo lo justifica.
- Los análisis de alto impacto ya no dependen de una sola familia de modelos.
- La personalización sigue disponible para el cerebro local, pero no cruza la frontera NVIDIA.
- Un resultado privado puede quedar sin resumen si Apple Intelligence no está disponible; se
  prefiere degradación explícita a transmisión implícita.
