# ADR-0019: descubrimiento TCP local acotado

- Estado: aceptado
- Fecha: 2026-08-19

## Filtro de ingeniería

1. **Cuestionar:** descubrir hosts no requiere por defecto `nmap`, ICMP ni privilegios elevados.
2. **Eliminar:** se descartan shell, DNS, fingerprinting, payloads, UDP y persistencia de resultados.
3. **Simplificar:** `socket.connect_ex` distingue puerto abierto, rechazo y host no observable.
4. **Acelerar:** un timeout fijo y un pool efímero acotan el ciclo sin bloquear el event loop.
5. **Automatizar:** broker, confirmación, ejecución y auditoría siguen el mismo grafo existente.

## Decisión

`network_discover_hosts` acepta únicamente una IP o CIDR numérica dentro de loopback, RFC 1918 o
ULA, entre uno y ocho puertos TCP únicos y un máximo de 256 direcciones. La autorización es de riesgo
alto y consume una confirmación vinculada al digest exacto de llamada.

El ejecutor revalida el modelo, el alcance y el tamaño después de la autorización. Sondea los pares
IP/puerto con APIs nativas, 250 ms por conexión y hasta 32 workers efímeros. La llamada completa se
desplaza a un thread mediante `asyncio.to_thread`, por lo que no bloquea IPC ni audio. El resultado
solo incluye hosts que respondieron y sus puertos abiertos; la auditoría conserva hash y tamaño,
nunca el contenido del resultado.

## Límites deliberados

- No se habilita `ping`: ICMP añadiría subprocess o privilegios sin mejorar el MVP.
- No se detectan servicios, versiones, sistema operativo ni vulnerabilidades.
- Es una observación activa: una conexión aceptada puede quedar registrada por el host sondeado.
- El rechazo TCP prueba respuesta del host; un timeout solo significa que no fue observable.
- La superficie de confirmación implementada posteriormente se documenta en ADR-0020 y preserva el
  digest exacto y el consumo de un solo uso.

## Encaje en el roadmap

- **Fase 4:** convierte el contrato de descubrimiento en una observación local ejecutable y
  auditable, sin habilitar todavía terminal ni acciones mutables.
