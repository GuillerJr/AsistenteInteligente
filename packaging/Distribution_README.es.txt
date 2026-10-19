JARVIS — Distribución para macOS con Apple Silicon

Este paquete incorpora la aplicación y su daemon Python. No necesita el repositorio,
un entorno virtual ni Homebrew para arrancar. macOS mínimo: 14.0; arquitectura: arm64.

Las respuestas y la voz neural requieren conectividad y una clave NVIDIA configurada
de forma privada en Jarvis. El paquete nunca incluye claves, memoria, conversaciones,
grabaciones ni modelos biométricos personales. Apple Speech se utiliza para reconocer
voz, no como segunda voz de salida.

Instalación de una distribución pública aprobada:
1. Verifica el origen, el manifiesto firmado y la integridad del archivo.
2. Extrae Jarvis.app y cópiala a Aplicaciones. No la ejecutes dentro del ZIP.
3. Abre Jarvis y completa su configuración. Concede solo los permisos que necesites.
4. Configura la clave del proveedor por el flujo privado de la aplicación.
5. Realiza el enrolamiento y las pruebas de voz únicamente cuando decidas hacerlo.

Un candidato ad-hoc o de validación local NO es una release pública notarizada.
No desactives Gatekeeper ni elimines cuarentena para distribuirlo como si lo fuera.
Un informe de validación sin voz no certifica enrolamiento ni autenticación vocal.

Las actualizaciones requieren un manifiesto Ed25519 auténtico, una versión superior
y la verificación de la firma del bundle. El instalador conserva la versión anterior
durante la transacción. Nunca sustituyas los datos privados con los de otra instalación.

Quitar Jarvis.app no implica autorización para borrar conversaciones, credenciales o
modelos del propietario. La gestión de esos datos es una decisión separada y explícita.

Licencia del producto: LICENSE.txt, junto a este documento.
Aviso del modelo VAD: AegisAudio_AegisAudioCore.bundle/Contents/Resources/SILERO_NOTICE.txt.
El inventario de dependencias bloqueadas acompaña al ZIP en Jarvis.spdx.json;
no constituye por sí solo una auditoría de licencias de terceros.
