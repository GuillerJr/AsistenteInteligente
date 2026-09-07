# P9 — Calificación real del driver de navegador

P9 prueba el controlador Chrome CDP de Jarvis contra Google Chrome real, no contra un mock. Mantiene
la prueba determinista y privada: usa un perfil efímero, un documento DOM en memoria y ninguna URL
externa. Tampoco usa la voz del propietario.

## Compuerta

```bash
./script/p9_browser_gate.sh
```

La compuerta exige que el checkout, `~/Applications/Jarvis.app` y el daemon correspondan a la misma
revisión. Primero repite P8. Después valida la firma de Chrome, comprueba que el puerto local 9222 no
esté ocupado e inicia una instancia headless con un perfil dentro de `/private/tmp`. La red externa
queda denegada por configuración de Chrome y por un proxy local cerrado.

## Los siete contratos

| Contrato | Evidencia real |
|---|---|
| `chrome_driver_discovery` | Los metadatos locales contienen el bundle `com.google.Chrome`. |
| `chrome_runtime` | La instancia efímera aparece como proceso del usuario actual. |
| `loopback_cdp_contract` | CDP responde por loopback y `Browser.getVersion` identifica Chrome. |
| `memory_only_dom` | `Page.setDocumentContent` carga la fixture fija sin archivo ni navegación. |
| `dom_text_replacement` | `Runtime.evaluate` enfoca el input, cambia su valor y verifica el resultado. |
| `dom_press_verification` | Un clic DOM cambia el estado lógico de `ready` a `complete`. |
| `focus_and_pointer_isolation` | La app frontal y el puntero físico son idénticos antes y después. |

El reporte usa el esquema privado `live_isolated_chrome_driver`. Solo conserva la versión principal
de Chrome, versión CDP, latencias y booleanos. No serializa texto del usuario, URLs, DOM, imágenes,
audio, transcripciones ni datos del perfil del navegador.

## Límite honesto

Esta prueba demuestra compatibilidad del transporte y operaciones DOM básicas con la versión local
de Chrome. No afirma que selectores de sitios externos como YouTube sean estables ni que una cuenta
autenticada permita todas las acciones. Las automatizaciones de un sitio concreto deben tener su
propio contrato no destructivo; ante un selector desconocido Jarvis continúa fallando cerrado.

La precisión biométrica y el wake word físico permanecen diferidos hasta el bloque final de voz.
