---
orphan: true
---

# Día del concurso

Una lista de verificación para quienes operan un concurso en un despliegue
Docker de este fork, desde la semana anterior hasta el respaldo final. No
repite las demás guías: cada paso enlaza a la página que lo explica. Esas
páginas están en inglés por ahora, y aquí se citan sus secciones con su
título en inglés.

[English version](contest-day.md)

Los comandos se ejecutan en el servidor, desde la raíz del repositorio. Donde
un comando usa Docker Compose directamente, `<project>` es `CMS_PROJECT_NAME`
del `.env` (`cms-prod` si no está definido).

## La semana anterior

### Dimensionar el despliegue

Todos los concursos simultáneos comparten una sola cola y los mismos
workers. Los números de abajo vienen de una prueba de carga con 250
concursantes simulados en un servidor; tómalos como punto de partida, no como
límite.

- **Workers:** `CMS_WORKER_COUNT` (por defecto 1). La prueba de carga usó 8.
  Con menos, la cola tarda más en vaciarse al terminar el concurso (ver "Al
  terminar").
- **Procesos del Contest Web Server:** `CMS_CWS_COUNT` (por defecto 1). Con
  2, un servidor bajo carga fuerte rechazó algunos envíos hechos en los
  últimos segundos; con 4, ninguno (el porqué está en "Al terminar").
- **Puertos:** el proceso *i* escucha en `CMS_CWS_HTTP_PORT` + *i*. Nada
  verifica que ese rango no choque con `CMS_AWS_HTTP_PORT` y
  `CMS_RWS_HTTP_PORT`, y con los valores por defecto (8888, 8889, 8890) un
  segundo proceso cae en el puerto del Admin. Mueve los puertos del Admin y
  del Ranking por encima del rango, y reparte los procesos con `ip_hash` en el
  proxy inverso. Ver "Scaling Contest Web Server" en
  [docker-scripts.md](docker-scripts.md).
- **Reloj:** mantén el reloj del servidor sincronizado con NTP. Pon en
  `CMS_TIMEZONE` la zona horaria del concurso, para que el Admin Web Server
  muestre el equivalente local del horario del ranking (ver "Windows" en
  [multi-contest.md](multi-contest.md)).

Los cambios al `.env` se aplican cuando se recrean los contenedores
(`./up.sh`).

### Preparar los concursos

1. Importa los concursos y sus problemas. Un concurso importado de un
   respaldo con `./import.sh` llega **inactivo**, salvo que hayas elegido
   borrar la base de datos antes (ver `import.sh` en
   [docker-scripts.md](docker-scripts.md)).
2. Crea los grupos de ranking, asigna los concursos, marca **Active** y
   define las ventanas de ocultar y congelar: sigue "Before an exam day" en
   [multi-contest.md](multi-contest.md). Crea un grupo oculto **antes** de
   asignarle un concurso.
3. Crea los equipos y los grupos del concurso, y luego importa a los
   concursantes: [importing-users.md](importing-users.md).
4. Para cada problema que use `depends_on`, sigue "Before the contest" en
   [subtask-dependencies.md](subtask-dependencies.md).
5. Para cada problema, envía una solución correcta y una incorrecta desde un
   usuario de prueba, en cada lenguaje que usarán los concursantes, y revisa
   los puntajes.

### Ensayar

- Haz un ensayo en un despliegue con la misma versión y el mismo `.env` que
  el real, con los mismos pasos que el día del concurso.
- Anota el commit desplegado y el último que funcionó bien, por si hay que
  regresar (ver "Rollback" en [multi-contest.md](multi-contest.md)).
- Saca un respaldo con `./export.sh` cuando los concursos estén listos.

## Antes del inicio

- No reconstruyas ni actualices el despliegue el día del concurso.
- `./status.sh`: todos los contenedores están `Up` y todos los programas de
  la lista de supervisor están `RUNNING`.
- En el Admin Web Server, abre **Overview**. El título de **Workers status**
  dice "(N connected, M configured)": N debe ser igual a M.
- En **Ranking groups**, la columna **State** dice lo que esperas de cada
  grupo, y sus horas "next:" coinciden con el concurso (ver "Checking a
  schedule" en [multi-contest.md](multi-contest.md)).
- Pide a los concursantes que **inicien sesión antes del inicio**. Revisar
  una contraseña cuesta unos 0.2 segundos de CPU, y cada proceso del Contest
  Web Server revisa a lo más 4 a la vez, así que si todo un salón entra justo
  a la hora de inicio, todos esperan.

## Durante el concurso

Deja abierta la página **Overview** del concurso en el Admin Web Server. Se
actualiza cada 5 segundos:

- **Submissions status** (solo en el Overview de un concurso): cuántos
  envíos de este concurso están puntuados, fallaron al compilar, o se siguen
  compilando, evaluando o puntuando. **Scored** siempre aparece; cualquier
  otra fila se oculta mientras está en 0. Pon atención a **Cannot compile
  (please check)** y **Cannot evaluate (please check)**.
- **Queue status:** los trabajos que esperan un worker, de todos los
  concursos juntos; un envío puede tener más de uno. Los trabajos que ya
  corren en un worker no aparecen aquí.
- **Workers status:** si cada worker está conectado y qué está haciendo.
- **Logs:** las últimas 100 advertencias y errores de los servicios de CMS
  (no del Ranking Web Server).

Para los logs completos, `./logs.sh -f cms` sigue los servicios de CMS (sal
con `Ctrl+C`) y `./logs.sh --tail 2000 cms` muestra las últimas 2000
líneas.

Problemas comunes:

- **Un scoreboard no coincide con la base de datos:** presiona
  **Regenerate** en su grupo de ranking (ver "When a scoreboard is wrong" en
  [multi-contest.md](multi-contest.md)).
- **Un worker aparece con Connected: No:** su trabajo regresa solo a la cola
  en segundos. Reinicia ese worker, numerado desde 0 como en la columna
  **Shard**:

      docker compose -f docker/docker-compose.prod.yml --env-file .env -p <project> \
          exec cms supervisorctl -c /home/cmsuser/cms/etc/supervisord.conf \
          restart cmsworker<shard>

- **Un worker quedó deshabilitado:** un worker que pasa más de 10 minutos
  ocupado con un solo trabajo se deshabilita y su trabajo regresa a la cola
  (`./logs.sh cms` dice "put again in the queue because of worker timeout";
  este mensaje no sale en la tabla **Logs** del Overview). No recibe más
  trabajos, ni siquiera tras reiniciarlo, hasta que alguien presione
  **Enable** en su fila de **Workers status**.

- **`QueuePool limit … reached` en los logs:** el Contest Web Server se
  quedó sin conexiones a la base de datos (ver
  [Troubleshooting](Troubleshooting.rst)).
- **Extender un concurso:** las horas de ocultar y congelar de un grupo de
  ranking son absolutas. Muévelas a mano para que coincidan con el nuevo
  final.

## Al terminar

1. **Envíos de último segundo.** No hay periodo de gracia. Un envío cuenta
   con la hora en que el servidor lo procesa, no con la hora en que el
   concursante presionó el botón: un servidor ocupado puede considerar tardío
   un envío mandado justo antes del final. Un envío tardío no se acepta
   (salvo que el concurso permita envíos no oficiales antes del modo de
   análisis), y el formulario solo regresa al concursante a la página del
   concurso, que dice "La competencia ya finalizó." ("The contest has
   already ended." con la interfaz en inglés). Avisa a los concursantes
   desde antes que no dejen sus envíos para los últimos segundos.
2. **Espera a que termine la evaluación.** Los envíos hechos antes del final
   se siguen evaluando después. En la prueba de carga la cola tardó de 1 a 5
   minutos en vaciarse con 8 workers. Terminó cuando, en el **Overview** de
   cada concurso, **Queue status** dice "Queue empty." y en **Submissions
   status** no queda ninguna fila **Compiling...**, **Evaluating...** ni
   **Scoring...**. **Scored** y **Compilation failed** son finales. Resuelve
   antes cualquier fila **Cannot compile** o **Cannot evaluate**.
3. **Solo entonces publica el ranking final:** presiona **Descongelar
   ahora** o **Mostrar ahora** en el grupo de ranking (ver 'The "now" buttons'
   en [multi-contest.md](multi-contest.md)). Si la hora de descongelar o de
   mostrar está programada, ponla lo bastante después del final para que la
   cola se vacíe.
4. Compara el scoreboard publicado con los puntajes del Admin Web Server. Si
   no coinciden, presiona **Regenerate**.
5. Desmarca **Active** en cada concurso para cerrarlo a los concursantes.
   Su scoreboard sigue publicado.

## Después del concurso

- Saca un respaldo con `./export.sh` y copia el archivo de `dumps/` fuera
  del servidor.
- Si se renombró o borró un grupo de ranking, quita su scoreboard viejo (ver
  "Removing an old scoreboard" en [multi-contest.md](multi-contest.md)).

## Cuando algo sale mal

| Síntoma | Qué hacer | Detalles |
|---------|-----------|----------|
| A un scoreboard le faltan puntajes o muestra un concurso que no debería | **Regenerate** en su grupo de ranking | [multi-contest.md](multi-contest.md) |
| Un scoreboard dejó de actualizarse y ProxyService registra "rejected the visibility" | Sigue "Failure mode" | [multi-contest.md](multi-contest.md) |
| Una página no carga | `./status.sh`, luego `./logs.sh` | [docker-scripts.md](docker-scripts.md) |
| Un envío se queda en **Cannot compile** o **Cannot evaluate** | Lee la tabla **Logs** en **Overview** y la página del envío | [Troubleshooting](Troubleshooting.rst) |
| En un problema con `depends_on`, las subtareas dependientes no quedan en 0 como se esperaba, o los envíos tardan en terminar | Busca en el log del Evaluation Service "cannot be used" y "gates are holding each other" | [subtask-dependencies.md](subtask-dependencies.md) |
| Un concurso no aparece para los concursantes | Marca **Active** en él | [multi-contest.md](multi-contest.md) |
