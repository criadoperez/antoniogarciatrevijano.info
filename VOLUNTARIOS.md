# Voluntarios de preservación del archivo — IPFS

Este archivo contiene los escritos, audios y documentos de Antonio García-Trevijano
(1928–2018) y está alojado en la red IPFS. Cualquier persona puede contribuir a su
preservación ejecutando un nodo voluntario que replique automáticamente todos los
contenidos del archivo.

---

## ¿Por qué es útil tu participación?

Actualmente el archivo completo (artículos de prensa, libros, fotografías,
grabaciones de audio con sus transcripciones) está alojado en un único servidor
IPFS. Si ese servidor fallara, el contenido desaparecería de la red.

Cada nodo voluntario que fija los CIDs raíz del archivo descarga y sirve una
copia completa e independiente. Cuantos más nodos participen, más resistente e
indestructible es el archivo: ningún fallo técnico, ni ninguna decisión
unilateral, puede hacerlo desaparecer.

---

## Qué hace tu nodo

- Descarga automáticamente todos los contenidos del archivo a tu máquina.
- Los sirve a cualquier usuario de la red IPFS que los solicite.
- Se mantiene sincronizado: cuando se añaden nuevos documentos o audios,
  tu nodo los descarga sin que tengas que hacer nada.
- No puede modificar el contenido ni añadir nada al archivo: solo el servidor
  principal publica los CIDs raíz que deben replicarse.

---

## Requisitos

| Requisito | Detalle |
|---|---|
| **Espacio en disco** | ~60 GB actualmente, creciendo a medida que se añaden audios |
| **Nodo IPFS (Kubo)** | Versión 0.20 o superior, en ejecución continua |
| **Conexión a internet** | Banda ancha; el puerto 4001 abierto mejora la disponibilidad |
| **Sistema operativo** | Linux, macOS o Windows |

Si no tienes Kubo instalado, descárgalo desde https://dist.ipfs.tech/#kubo e
inicia el daemon con `ipfs daemon`.

---

## Instalación

### 1. Instala y arranca el daemon IPFS (Kubo)

Si aún no tienes Kubo instalado, descárgalo desde:

```
https://dist.ipfs.tech/#kubo
```

Extrae el archivo, coloca el binario en tu `$PATH`, inicializa el repositorio
y arranca el daemon:

```bash
ipfs init
ipfs daemon
```

Deja el daemon corriendo en segundo plano (o abre otra terminal para el
siguiente paso). No continúes hasta ver la línea `Daemon is ready` en la salida.

### 2. Ejecuta el sincronizador de raíces

El archivo completo se replica fijando dos CIDs raíz:

- `archive-root`: contiene los documentos y audios del archivo.
- `site-root`: contiene la web estática publicada en IPFS.

El método recomendado para voluntarios ya no depende de `ipfs-cluster-follow`
ni de túneles P2P: basta con leer el manifiesto público de `www2` y fijar esas
raíces en tu nodo local.

Ejecuta:

```bash
curl -fsSL https://www2.antoniogarciatrevijano.info/cluster/sync-roots.sh | bash
```

Ese script:

1. Descarga `https://www2.antoniogarciatrevijano.info/cluster/pins.txt`.
2. Fija en tu Kubo los CIDs raíz publicados allí.
3. Desfija las raíces antiguas que ya no formen parte del manifiesto.

La primera descarga puede tardar varias horas. Es normal.

### 3. Actualizar tu copia más adelante

Cuando quieras refrescar tu nodo, vuelve a ejecutar exactamente el mismo
comando:

```bash
curl -fsSL https://www2.antoniogarciatrevijano.info/cluster/sync-roots.sh | bash
```

Usa siempre `www2`: es la copia estática servida directamente por nginx. La URL
equivalente en `www` puede agotarse al arrancar porque se sirve a través de
IPFS/IPNS.

---

## Ejecutar como tarea periódica

Si quieres que tu nodo se refresque automáticamente, programa el mismo script
de sincronización.

### Linux (systemd)

Guarda primero el script:

```bash
sudo curl -fsSL https://www2.antoniogarciatrevijano.info/cluster/sync-roots.sh \
  -o /usr/local/bin/antoniogarciatrevijano-sync-roots
sudo chmod +x /usr/local/bin/antoniogarciatrevijano-sync-roots
```

Crea `/etc/systemd/system/antoniogarciatrevijano-sync.service`:

```ini
[Unit]
Description=Sincroniza las raíces IPFS del archivo García-Trevijano
After=network-online.target ipfs.service
Wants=network-online.target ipfs.service

[Service]
Type=oneshot
User=TU_USUARIO
ExecStart=/usr/local/bin/antoniogarciatrevijano-sync-roots
```

Crea `/etc/systemd/system/antoniogarciatrevijano-sync.timer`:

```ini
[Unit]
Description=Ejecuta periódicamente la sincronización IPFS del archivo García-Trevijano

[Timer]
OnBootSec=5m
OnUnitActiveSec=1h
Persistent=true

[Install]
WantedBy=timers.target
```

Activa la tarea:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now antoniogarciatrevijano-sync.timer
sudo systemctl start antoniogarciatrevijano-sync.service
sudo systemctl status antoniogarciatrevijano-sync.service
```

### macOS (launchd)

Guarda primero el script:

```bash
curl -fsSL https://www2.antoniogarciatrevijano.info/cluster/sync-roots.sh \
  -o /usr/local/bin/antoniogarciatrevijano-sync-roots
chmod +x /usr/local/bin/antoniogarciatrevijano-sync-roots
```

Crea `~/Library/LaunchAgents/info.antoniogarciatrevijano.sync.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>info.antoniogarciatrevijano.sync</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/local/bin/antoniogarciatrevijano-sync-roots</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>StartInterval</key>
  <integer>3600</integer>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/info.antoniogarciatrevijano.sync.plist
```

---

## Verificar que tu nodo está sincronizado

Puedes comprobar el estado en cualquier momento:

```bash
# Ver las raíces fijadas
ipfs pin ls --type=recursive

# Comprobar una raíz concreta
ipfs pin ls --type=recursive QmfXv5Ai5ruR4qAgJzwfGxQ9rcpdEuxbtf7QnHnP7qCBi1
ipfs pin ls --type=recursive bafybeibqxzwwf5cz2eve6fcm75ximgfhryiejh5n4xsscl3t3pbod6f25u
```

---

## Preguntas frecuentes

**¿Consume muchos recursos?**
El proceso en sí consume muy poca CPU y RAM. El mayor impacto es el espacio
en disco (~60 GB) y el ancho de banda durante la sincronización inicial.
Una vez sincronizado, el tráfico de red es mínimo.

**¿Necesito abrir puertos en el router?**
No es obligatorio. Sin puertos abiertos tu nodo puede descargar y almacenar
el contenido sin problema. Abrir el puerto 4001 (TCP y UDP) en tu router
mejora la conectividad y permite que más usuarios puedan obtener los archivos
desde tu nodo.

**¿Puedo detener el nodo en cualquier momento?**
Sí. Detenerlo no borra nada, simplemente tu nodo deja de estar disponible
en la red mientras está parado. Al reiniciarlo se resincroniza automáticamente.

**¿Qué pasa si mi nodo se queda desactualizado?**
Vuelve a ejecutar `sync-roots.sh`. Si has configurado el timer o el agente del
sistema, la actualización será automática.

**¿Cómo sé que no estoy descargando nada malicioso?**
IPFS es un sistema de contenido verificado: cada archivo se identifica por
su huella criptográfica (CID). El manifiesto público solo publica los CIDs raíz
del archivo y de la web. Ningún tercero puede alterar el contenido sin cambiar
esos CIDs.

**¿Por qué ya no se recomienda `ipfs-cluster-follow`?**
Porque en algunas redes la conexión libp2p al peer del clúster no resulta
estable: el puerto público `9096` no siempre es alcanzable y el puente
`ipfs p2p forward /x/cluster` puede dejar un listener local aparentemente
abierto pero sin transportar tráfico útil. Publicar las dos raíces y fijarlas
directamente en Kubo evita ese problema y replica exactamente el mismo
contenido.

---

## Datos de replicación

| Campo | Valor |
|---|---|
| Servidor principal | `antoniogarciatrevijano.info` |
| Manifiesto de raíces | `https://www2.antoniogarciatrevijano.info/cluster/pins.txt` |
| Script de sincronización | `https://www2.antoniogarciatrevijano.info/cluster/sync-roots.sh` |
| Configuración legacy del clúster | `https://www2.antoniogarciatrevijano.info/cluster/service.json` |

---

Gracias por contribuir a la preservación de este archivo.
