# ADR-0178: Runtime de Capability Packs sin código descargado

## Estado

Aceptada.

## Contexto

Las Skills declarativas especializan a Jarvis, pero una integración distribuible necesita agrupar
instrucciones, referencias, contratos de herramientas, autenticación y ciclo de vida. Cargar Python,
shell o bibliotecas desde un paquete convertiría la instalación en ejecución arbitraria y permitiría
evitar el Tool Broker.

## Decisión

1. Un Capability Pack es un documento JSON cerrado con manifiesto, Skills, recursos de texto y
   conectores MCP opcionales. No admite ejecutables ni scripts.
2. El paquete fuente lleva un checksum SHA-256 canónico. Al instalar, Jarvis crea además un sello
   HMAC local con una subclave de dominio derivada de Keychain para detectar manipulación del
   almacén privado sin reutilizar directamente la clave IPC.
3. El manifiesto declara exactamente capacidades, hosts, esquemas, riesgos y nombres. No puede
   reemplazar Skills integradas, herramientas existentes ni ampliar las capacidades de un rol.
4. Los argumentos se validan contra un subconjunto cerrado y acotado de JSON Schema. Cadenas con
   apariencia de credencial se rechazan antes de salir del Mac.
5. Los conectores usan MCP stateless `2026-07-28` sobre HTTPS público. Jarvis valida y fija la IP,
   no sigue redirecciones, limita respuestas y exige correspondencia JSON-RPC.
6. Los tokens Bearer viven únicamente en una entrada Keychain derivada del plugin y conector. Una
   desinstalación elimina esas entradas.
7. Lecturas externas tienen riesgo mínimo medio. Escrituras y control son alto o crítico y requieren
   una aprobación de un solo uso que muestra plugin, destino y nombres de campos divulgados.
8. El catálogo del broker y los ejecutores se construye de forma inmutable al arrancar; un cambio de
   plugin requiere reiniciar el daemon.

## Consecuencias

- Jarvis puede reutilizar Skills y servidores MCP sin confiar en código descargado.
- La selección carga únicamente el Capability Pack pertinente y sus referencias permanecen locales.
- Una manipulación, colisión, downgrade, permiso implícito, endpoint privado o contrato no acotado
  falla de forma cerrada.
- OAuth interactivo y plugins con código nativo quedan fuera de este contrato; pueden añadirse en el
  futuro como runtimes separados sin debilitar esta frontera.
