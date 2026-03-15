# Voluntarios de preservación del archivo — IPFS Cluster

Este archivo contiene los escritos, audios y documentos de Antonio García-Trevijano
(1928–2018) y está alojado en la red IPFS. Cualquier persona puede contribuir a su
preservación ejecutando un nodo voluntario que replique automáticamente todos los
contenidos del archivo.

---

## ¿Por qué es útil tu participación?

Actualmente el archivo completo (artículos de prensa, libros, fotografías,
grabaciones de audio con sus transcripciones) está alojado en un único servidor
IPFS. Si ese servidor fallara, el contenido desaparecería de la red.

Cada nodo voluntario que se une al clúster descarga y sirve una copia completa e
independiente. Cuantos más nodos participen, más resistente e indestructible es
el archivo: ningún fallo técnico, ni ninguna decisión unilateral, puede hacerlo
desaparecer.

---

## Qué hace tu nodo

- Descarga automáticamente todos los contenidos del archivo a tu máquina.
- Los sirve a cualquier usuario de la red IPFS que los solicite.
- Se mantiene sincronizado: cuando se añaden nuevos documentos o audios,
  tu nodo los descarga sin que tengas que hacer nada.
- No puede modificar el contenido ni añadir nada al archivo: solo el servidor
  principal puede gestionar el conjunto de archivos.

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

### 2. Habilita el túnel de conexión al clúster

La comunicación entre tu nodo y el servidor del clúster se realiza a través
de la propia red IPFS, lo que evita problemas con cortafuegos y NAT.

Primero, habilita la función de reenvío P2P en Kubo (solo es necesario la
primera vez):

```bash
ipfs config --bool Experimental.Libp2pStreamMounting true
```

Reinicia el daemon IPFS para aplicar el cambio. A continuación, crea el
túnel:

```bash
ipfs p2p forward /x/cluster /ip4/127.0.0.1/tcp/19096 \
    /p2p/12D3KooWJEhcyZ5jtpmVsPGqrQFn2BfYdMkCCigWSfM6x555yd3F
```

Este comando crea un puerto local (19096) que conecta con el servidor del
clúster a través de la red IPFS. El túnel permanece activo mientras el
daemon IPFS esté en ejecución; hay que volver a crearlo cada vez que se
reinicie el daemon (ver la sección de servicios más abajo para automatizarlo).

### 3. Descarga ipfs-cluster-follow

Descarga el binario para tu sistema desde:

```
https://dist.ipfs.tech/#ipfs-cluster-follow
```

Extrae el archivo y coloca el binario en algún directorio de tu `$PATH`
(por ejemplo `/usr/local/bin/` en Linux o macOS).

### 4. Únete al clúster (primera vez)

```bash
ipfs-cluster-follow antoniogarciatrevijano \
    init https://www.antoniogarciatrevijano.info/cluster/service.json
```

Este comando:
1. Descarga la configuración del clúster desde el servidor.
2. Genera una identidad local única para tu nodo.

### 5. Ejecuciones posteriores

Una vez inicializado, basta con ejecutar para conectarse al servidor y comenzar la sincronización del contenido

```bash
ipfs-cluster-follow antoniogarciatrevijano run
```
La descarga inicial podría durar varias horas, es normal.

---

## Ejecutar como servicio en segundo plano

Para que el nodo se inicie automáticamente y funcione en segundo plano, puedes
crear un servicio del sistema.

### Linux (systemd)

Crea dos archivos de servicio. El primero establece el túnel P2P después de
que IPFS arranque. Crea `/etc/systemd/system/ipfs-cluster-tunnel.service`:

```ini
[Unit]
Description=Túnel P2P al clúster García-Trevijano
After=network.target ipfs.service
Requires=ipfs.service

[Service]
Type=oneshot
User=TU_USUARIO
ExecStart=/usr/local/bin/ipfs p2p forward /x/cluster /ip4/127.0.0.1/tcp/19096 /p2p/12D3KooWJEhcyZ5jtpmVsPGqrQFn2BfYdMkCCigWSfM6x555yd3F
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
```

El segundo ejecuta el seguidor del clúster. Crea
`/etc/systemd/system/ipfs-cluster-follow.service`:

```ini
[Unit]
Description=IPFS Cluster Follow — Archivo García-Trevijano
After=network.target ipfs.service ipfs-cluster-tunnel.service
Wants=ipfs.service
Requires=ipfs-cluster-tunnel.service

[Service]
User=TU_USUARIO
ExecStart=/usr/local/bin/ipfs-cluster-follow antoniogarciatrevijano run
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Sustituye `TU_USUARIO` por tu nombre de usuario en ambos archivos. Luego
activa los servicios:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ipfs-cluster-tunnel
sudo systemctl enable --now ipfs-cluster-follow
sudo systemctl status ipfs-cluster-follow
```

### macOS (launchd)

Crea el archivo `~/Library/LaunchAgents/info.antoniogarciatrevijano.tunnel.plist`
para el túnel:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>info.antoniogarciatrevijano.tunnel</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/local/bin/ipfs</string>
    <string>p2p</string>
    <string>forward</string>
    <string>/x/cluster</string>
    <string>/ip4/127.0.0.1/tcp/19096</string>
    <string>/p2p/12D3KooWJEhcyZ5jtpmVsPGqrQFn2BfYdMkCCigWSfM6x555yd3F</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
</dict>
</plist>
```

Y el archivo `~/Library/LaunchAgents/info.antoniogarciatrevijano.cluster.plist`
para el seguidor:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>info.antoniogarciatrevijano.cluster</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/local/bin/ipfs-cluster-follow</string>
    <string>antoniogarciatrevijano</string>
    <string>run</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/info.antoniogarciatrevijano.tunnel.plist
launchctl load ~/Library/LaunchAgents/info.antoniogarciatrevijano.cluster.plist
```

---

## Verificar que tu nodo está sincronizado

Puedes comprobar el estado en cualquier momento:

```bash
# Ver el estado del nodo
ipfs-cluster-follow antoniogarciatrevijano info

# Ver los archivos que está sincronizando
ipfs-cluster-follow antoniogarciatrevijano list
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
Al reiniciarlo, descargará automáticamente los contenidos nuevos que se hayan
añadido durante su ausencia.

**¿Cómo sé que no estoy descargando nada malicioso?**
IPFS es un sistema de contenido verificado: cada archivo se identifica por
su huella criptográfica (CID). El clúster solo acepta instrucciones de pinado
del servidor principal, cuya identidad está fijada en la configuración.
Ningún tercero puede inyectar contenido en el clúster.

---

## Datos del clúster

| Campo | Valor |
|---|---|
| Nombre del clúster | `antoniogarciatrevijano` |
| Servidor principal | `antoniogarciatrevijano.info` |
| Peer ID del clúster | `12D3KooWJzSmawZK3Kuq46u1oq28BpoUEdjKMhanax6dZM9ht6GS` |
| Peer ID IPFS del servidor | `12D3KooWJEhcyZ5jtpmVsPGqrQFn2BfYdMkCCigWSfM6x555yd3F` |
| URL de configuración | `https://www.antoniogarciatrevijano.info/cluster/service.json` |

---

Gracias por contribuir a la preservación de este archivo.
