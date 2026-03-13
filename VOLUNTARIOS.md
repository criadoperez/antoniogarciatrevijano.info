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

### 1. Descarga ipfs-cluster-follow

Descarga el binario para tu sistema desde:

```
https://dist.ipfs.tech/#ipfs-cluster-follow
```

Extrae el archivo y coloca el binario en algún directorio de tu `$PATH`
(por ejemplo `/usr/local/bin/` en Linux o macOS).

### 2. Únete al clúster (primera vez)

```bash
ipfs-cluster-follow antoniogarciatrevijano \
    --init https://antoniogarciatrevijano.info/cluster/service.json \
    run
```

Este comando:
1. Descarga la configuración del clúster desde el servidor.
2. Genera una identidad local única para tu nodo.
3. Se conecta al servidor y comienza a sincronizar el contenido.

El proceso de descarga inicial puede tardar varias horas dependiendo de tu
conexión y del espacio disponible. Es normal.

### 3. Ejecuciones posteriores

Una vez inicializado, basta con ejecutar:

```bash
ipfs-cluster-follow antoniogarciatrevijano run
```

---

## Ejecutar como servicio en segundo plano

Para que el nodo se inicie automáticamente y funcione en segundo plano, puedes
crear un servicio del sistema.

### Linux (systemd)

Crea el archivo `/etc/systemd/system/ipfs-cluster-follow.service`:

```ini
[Unit]
Description=IPFS Cluster Follow — Archivo García-Trevijano
After=network.target ipfs.service
Wants=ipfs.service

[Service]
User=TU_USUARIO
ExecStart=/usr/local/bin/ipfs-cluster-follow antoniogarciatrevijano run
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Sustituye `TU_USUARIO` por tu nombre de usuario. Luego activa el servicio:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ipfs-cluster-follow
sudo systemctl status ipfs-cluster-follow
```

### macOS (launchd)

Crea el archivo `~/Library/LaunchAgents/info.antoniogarciatrevijano.cluster.plist`:

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
| Puerto swarm | `9096` |
| Peer ID del servidor | `12D3KooWJzSmawZK3Kuq46u1oq28BpoUEdjKMhanax6dZM9ht6GS` |
| URL de configuración | `https://antoniogarciatrevijano.info/cluster/service.json` |

---

Gracias por contribuir a la preservación de este archivo.
