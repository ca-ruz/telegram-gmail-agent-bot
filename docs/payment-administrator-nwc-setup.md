# Configuración para automatizar pagos con Core Lightning y NWC

## Objetivo

El bot podrá crear una factura Lightning única para cada miembro y periodo de
pago, enviarla por mensaje privado y comprobar automáticamente cuándo fue
liquidada.

Para esto necesitamos conectar el bot con el nodo Core Lightning mediante
Nostr Wallet Connect (NWC). La conexión será únicamente para recibir pagos y
consultar el estado de facturas.

## Lo que necesitamos del administrador de pagos

1. Un nodo Core Lightning funcionando en la red correcta, normalmente
   `bitcoin`/mainnet.
2. Capacidad de liquidez entrante suficiente para recibir pagos Lightning.
3. El plugin [Daywalker `cln-nip47`](https://github.com/daywalker90/cln-nip47)
   instalado y funcionando en el nodo.
4. El relay NWC que usará el plugin. Para las primeras pruebas se puede usar:

   ```text
   wss://relay.getalby.com/v1
   ```

   Este es el relay NWC público de Alby. No es necesario instalar Alby Hub;
   solo necesitamos el relay y el plugin funcionando en el nodo Core Lightning.
5. Una conexión NWC dedicada para el bot, configurada como receive-only, con
   presupuesto de gasto igual a cero.
6. La URI completa de esa conexión NWC, enviada por un canal privado y seguro.

## Configuración en el nodo

### 1. Confirmar el nodo

El administrador debe confirmar que el nodo está funcionando y en la red
correcta:

```bash
lightning-cli getinfo
```

No es necesario compartir la salida completa. Solo hay que confirmar que el
nodo está en `bitcoin`/mainnet y que puede recibir pagos.

### 2. Instalar el plugin

Descargar una versión de `cln-nip47` compatible con la arquitectura del nodo,
o compilarlo desde su repositorio oficial. Core Lightning debe poder ejecutar
el archivo del plugin.

El plugin puede iniciarse mediante la configuración de Core Lightning o con el
comando `plugin`:

```bash
lightning-cli plugin list
```

La respuesta debe mostrar el plugin activo. La documentación oficial de Core
Lightning está disponible en:

- [Instalación y administración de plugins](https://docs.corelightning.org/docs/plugins)
- [Comando `plugin`](https://docs.corelightning.org/reference/plugin)

### 3. Configurar el relay

Configurar el plugin para usar el relay elegido. Para las primeras pruebas:

```text
nip47-relays=wss://relay.getalby.com/v1
```

La opción debe quedar configurada en el arranque del plugin o en la
configuración de Core Lightning, según la instalación utilizada.

### 4. Crear la conexión NWC

Crear una conexión exclusiva para el bot con presupuesto de gasto cero:

```bash
lightning-cli nip47-create membership-bot 0
```

No agregar un intervalo. El valor `0` desactiva los pagos salientes para esa
conexión. El bot solo debe crear facturas entrantes y consultar su estado.

El comando mostrará una URI parecida a esta, pero nunca se debe compartir este
ejemplo literalmente:

```text
nostr+walletconnect://<wallet-pubkey>?relay=<relay>&secret=<private-secret>
```

La URI completa contiene un secreto y funciona como una credencial de acceso
al monedero.

## Qué necesitamos recibir

Enviar únicamente esta información por un canal privado:

- La URI NWC completa de la conexión dedicada.
- La URL del relay configurado, si no se usó el relay de Alby.
- La arquitectura del nodo, si fue necesario elegir un binario específico.
- La versión de Core Lightning y del plugin, si está disponible.
- Confirmación de que la conexión se creó con presupuesto `0` y sin intervalo.

No enviar:

- La semilla del nodo.
- El archivo `lightning-rpc`.
- Contraseñas.
- Macaroons, tokens o credenciales RPC.
- El archivo completo de configuración del nodo.
- La URI NWC en issues, grupos, capturas de pantalla, logs o repositorios.

## Qué hará el bot

El bot utilizará la conexión para:

1. Crear una factura única para cada miembro y periodo.
2. Asociar la factura con el miembro, el periodo y el monto esperado.
3. Enviar la factura al miembro por Telegram.
4. Detectar o consultar si la factura fue liquidada.
5. Marcar el pago como confirmado únicamente cuando la respuesta NWC incluya un
   estado explícito de liquidación y un preimage válido cuyo SHA-256 coincida
   con el hash de pago de la factura.
6. Reconciliar facturas después de reinicios o interrupciones.

El bot no podrá gastar fondos, pagar facturas externas ni acceder directamente
al puerto RPC del nodo.

## Prueba inicial

Antes de activar la automatización para todos los miembros:

1. Crear una conexión NWC de prueba separada.
2. Crear una factura de monto pequeño.
3. Pagarla desde otra cartera Lightning.
4. Confirmar que el bot recibe la factura y la identifica correctamente.
5. Confirmar que el estado cambia a pagado únicamente después de la
   liquidación real.
6. Revocar la conexión de prueba cuando termine la validación.

La función debe permanecer desactivada hasta que el administrador de pagos y
el administrador principal aprueben el resultado. El flujo manual continuará
disponible para pagos tempranos, tardíos, incompletos o imposibles de asociar.

## Revocar la conexión

Si la URI NWC se comparte accidentalmente, revocar inmediatamente la conexión
y crear una nueva:

```bash
lightning-cli nip47-revoke membership-bot
lightning-cli nip47-create membership-bot 0
```

La URI anterior no debe reutilizarse después de una posible exposición.

## Referencias

- [Daywalker `cln-nip47`](https://github.com/daywalker90/cln-nip47)
- [Alby NWC Relay](https://guides.getalby.com/developer-guide/developer-guide/nostr-wallet-connect-api/building-nwc-wallet-services/alby-nwc-relay)
- [NIP-47: Nostr Wallet Connect](https://github.com/nostr-protocol/nips/blob/master/47.md)
- [Core Lightning: recibir pagos](https://docs.corelightning.org/docs/faq)
