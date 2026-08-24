# ADR-0070: Voz híbrida y segura de Jarvis

- Estado: aceptado
- Fase: 3 y 5 — Capacidades sensoriales e interfaz voice-first
- Fecha: 2026-08-24
- Reemplaza: ADR-0013

## Contexto

La voz local cerró el primer ciclo funcional, pero no aportaba una identidad sonora consistente.
Copiar la voz de un actor no es un requisito técnico ni legalmente prudente: Jarvis necesita una
voz propia, masculina, sobria y precisa, con continuidad cuando el endpoint gratuito no esté
disponible.

## Decisión

NVIDIA Magpie Multilingual sintetiza la ruta principal mediante API. Solo el daemon Python consulta
Keychain y habla con NVIDIA; la app Swift no recibe la credencial. `speech.synthesize` acepta texto
normalizado de hasta 2.000 caracteres y devuelve exclusivamente token aleatorio, nombre, SHA-256 y
tamaño de un WAV. El archivo vive en un directorio `0700`, se crea `0600` sin seguir enlaces y tiene
TTL de 60 segundos y un cupo de cuatro.

Swift vuelve a comprobar propietario, permisos, tipo regular, inode, tamaño, digest y cabecera WAV
antes de leer. Después lo mantiene en memoria, llama `speech.release` y reproduce con una cadencia
ligeramente más contenida. Un timeout, respuesta inválida o ausencia de red activa automáticamente
`AVSpeechSynthesizer`, escogiendo la voz española masculina de mayor calidad instalada y aplicando
la misma personalidad prosódica.

## Filtro del algoritmo de ingeniería

1. Se cuestionó que una identidad sonora exigiera un modelo local pesado o clonación de voz.
2. Se eliminaron selector de voces, caché, streaming, base de audio y dependencias TTS nuevas.
3. Se reutilizaron cliente NVIDIA, UDS autenticado, Keychain y frameworks AVFoundation nativos.
4. Una sola llamada sintetiza la respuesta completa; el fallback local evita bloquear el turno.
5. La generación, validación, liberación y recuperación ocurren en segundo plano sin abrir UI.

## Consecuencias

Jarvis obtiene voz propia sin descargar modelos ni entregar secretos al proceso visual. El endpoint
preview puede cambiar o aplicar límites, pero el ciclo voice-first sigue funcionando localmente. La
latencia inicial depende de red y la respuesta completa debe caber en 8 MiB y 120 segundos de audio.
