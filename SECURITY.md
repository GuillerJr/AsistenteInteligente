# Seguridad de la base propia

La variante está en fase experimental. Sus adaptadores son sintéticos y no se
conectan a cuentas, dispositivos ni datos del dueño. Las pruebas del núcleo no
acreditan seguridad productiva, TCC, aislamiento de plugins ni firmas IPC.

La base registra autorizaciones ligadas a una operación exacta y conserva resultados
inciertos tras una interrupción. Los callbacks inyectados siguen siendo código de
confianza. Antes de exponerlos a entradas reales faltan límites de tiempo/datos,
aislamiento, persistencia, recuperación y transportes autenticados.

No se deben incorporar credenciales ni conversaciones al repositorio o sus logs.
El dueño realiza login, permisos de privacidad y decisiones de autorización.
La limpieza del código no elimina ni migra su almacenamiento de aplicación.

No publicar credenciales, datos personales ni pruebas con secretos al comunicar
un problema. Los cambios de política se revisan con pruebas de denegación,
revocación, cancelación y resultados inciertos antes de habilitar capacidades reales.
