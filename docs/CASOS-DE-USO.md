# Casos de uso — Nuevas secciones (Comunidad)

> Estado: **propuesta para validar**. Usuarios y roles, espacios y solicitudes de espacio
> ya están en desarrollo, y las notas de socios (Módulo F), el pizarrón semanal (Módulo G)
> y el registro básico de actividades (E·1) están hechos; el resto no está implementado
> todavía.
> Documento previo al desarrollo, pedido para acordar alcance antes de escribir código.

## 1. Contexto

Hoy la app es una **herramienta interna de las bibliotecarias**: todo lo que hay
(Préstamos, Socios, Cuotas, Mails, Automáticos) se apoya en Koha y el login es
pass-through contra Koha. Quien no tiene usuario de Koha, no entra.

Lo que se pide ahora cambia eso: la app pasa a ser también una **herramienta de la
organización** — comisión directiva y subcomisiones. Eso implica tres cosas nuevas:

1. Un **sistema de usuarios y roles propio** (hoy no existe).
2. Tres secciones nuevas: **Calendario + solicitudes**, **Inventario**, **Talleres**.
3. Una **versión de celular** usable de verdad (hoy es una adaptación mínima).

---

## 2. Actores

| Actor | Cómo entra | Quiénes son |
|---|---|---|
| **Bibliotecaria** | Usuario de Koha (como hoy) | Personal de la biblioteca |
| **Comisión Directiva** | Usuario propio de la app | Presidencia, secretaría, tesorería, vocales |
| **Subcomisión** | Usuario propio de la app | Cultura, Prensa, Infantil, Huerta, etc. Cada usuario pertenece a una subcomisión |
| *(futuro)* **Tallerista** | Usuario propio de la app | Solo ve/edita su propio taller |

### 2.1 Matriz de permisos

| Sección | Bibliotecaria | Comisión Directiva | Subcomisión |
|---|---|---|---|
| Inicio / Estadísticas | ✅ | ✅ | ❌ |
| Préstamos · Socios · Cuotas | ✅ | ✅ | ❌ |
| Mails · Automáticos · Historial | ✅ | ✅ | ❌ |
| **Calendario** | ✅ ver + editar | ✅ ver + editar | 👁️ solo ver |
| **Solicitudes de espacio** | ✅ ver todas + resolver | ✅ ver todas + resolver | ✅ crear y ver **las suyas** |
| **Inventario** | ✅ ver + editar | ✅ ver + editar | 👁️ ver + pedir prestado |
| **Talleres y actividades** | ✅ ver + editar | ✅ ver + editar | 👁️ solo ver |
| **Registro de actividades** | ✅ links, validar, estadísticas | ✅ links, validar, estadísticas | 👁️ las de sus actividades *(a confirmar)* |
| **Notas de socios** (dentro de Socios) | ✅ | ✅ | ❌ |
| **Pizarrón semanal** | ✅ | ❌ | ❌ |
| **Usuarios de la app** | ✅ | ✅ | ❌ |

> El **formulario público de registro** (Módulo E) no pide usuario: entra cualquiera que
> tenga el link.

> **Decidido:** la Comisión Directiva ve **lo mismo que las bibliotecarias**, incluidos
> socios, deudas y cuotas. Las subcomisiones **no**: solo lo institucional (calendario,
> inventario, talleres) y sus propias solicitudes.
>
> Implicancia técnica: los usuarios de Comisión no tienen usuario de Koha propio, así que
> sus consultas a Koha salen por una **cuenta de servicio** (`KOHA_USER` / `KOHA_PASSWORD`,
> que ya existe en el `.env`). Queda registrado en el log **qué usuario de la app** hizo
> cada consulta, para que la trazabilidad no se pierda.

---

## 3. Módulo A — Calendario y solicitudes de espacio

### CU-A1 · Ver el calendario de actividades
**Actor:** cualquiera con usuario · **Frecuencia:** diaria

1. Entra a **Calendario** y ve el mes en curso.
2. El calendario muestra, unificado:
   - eventos de los Google Calendar ya integrados (Talleres, Bayer Experimental,
     Bayer Band, Vencimientos) — *ya funciona hoy en la pestaña Agenda*;
   - actividades cargadas en la app (módulo Talleres);
   - **reservas de espacio aprobadas**.
3. Puede filtrar por calendario, por **espacio** y por subcomisión.
4. Toca un día → ve el detalle de ese día (hora, título, espacio, responsable).

**Reglas:** cada origen tiene su color. Vista mes / semana / lista (en celular arranca en lista).

---

### CU-A2 · Solicitar un espacio y una fecha
**Actor:** Subcomisión (también pueden hacerlo bibliotecarias y CD) · **Frecuencia:** semanal

1. Desde el calendario toca **"Solicitar espacio"**.
2. Completa el formulario:
   - **Actividad**: título y descripción breve
   - **Solicitante**: se completa solo (usuario + subcomisión)
   - **Espacio**: sala, salón, patio, sala infantil… (lista configurable)
   - **Fecha y horario**: desde / hasta
   - **Repetición**: única vez · semanal · quincenal · mensual (+ hasta cuándo)
   - **Cantidad estimada de personas**
   - **Necesidades**: proyector, sillas, sonido, mesas, wifi… (checkboxes + texto libre)
   - **Abierta al público** sí/no · **Arancelada** sí/no
3. Al elegir fecha y espacio, la app **avisa si hay superposición** con algo ya
   agendado en ese espacio (no bloquea: avisa y deja mandar igual, marcado como conflicto).
4. Envía → la solicitud queda en estado **Pendiente** y se le confirma en pantalla.

**Alternativas:**
- *A3a* El espacio está ocupado → se muestra qué lo ocupa y se ofrecen huecos libres cercanos.
- *A3b* Falta un dato obligatorio → no deja enviar y marca el campo.

---

### CU-A3 · Resolver una solicitud
**Actor:** Bibliotecaria o Comisión Directiva · **Frecuencia:** semanal

1. Entra a **Solicitudes** y ve la bandeja: pendientes primero, luego resueltas.
   Cada fila: actividad · solicitante · espacio · fecha · estado · ⚠️ si hay conflicto.
2. Abre una solicitud y ve todo el detalle + el calendario de ese día en ese espacio.
3. **Ajusta lo que haga falta antes de aprobar**: fecha, horario, espacio. En la práctica
   la mayoría de las veces lo que se aprueba **no coincide exactamente** con lo pedido,
   así que aprobar y editar son el mismo paso, no dos.
   Lo que se modificó queda a la vista del solicitante (pedido original → aprobado).
4. Resuelve:
   - **Aprobar** → queda **Aprobada**, la reserva aparece en el calendario de la app y
     **se crea el evento en el Google Calendar de la biblioteca** (ver CU-A5).
   - **Rechazar** → pide motivo (obligatorio) → queda **Rechazada**.
   - **Pedir cambios** → deja un comentario → vuelve al solicitante como **Con observaciones**.
5. El solicitante ve el cambio de estado en su bandeja *(y opcionalmente recibe un mail —
   el módulo de envío ya existe)*.

**Reglas:**
- Solo bibliotecarias y CD resuelven. Una subcomisión **no** aprueba sus propias solicitudes.
- Toda resolución queda registrada: **quién**, **cuándo**, **qué decidió**, **con qué motivo**,
  y **qué cambió** respecto de lo pedido.
- Una solicitud aprobada se puede **cancelar** o **reprogramar** después (con motivo): sale o
  se mueve en el calendario de la app y también en Google Calendar.

---

### CU-A5 · Publicar la reserva en Google Calendar
**Actor:** el sistema, al aprobarse una solicitud · **Decidido:** sí, se escribe en Google

1. Al aprobar (CU-A3), la app crea el evento en el Google Calendar de la biblioteca con el
   título, horario, espacio y responsable definitivos.
2. Si después se reprograma o se cancela, el evento se **actualiza o se borra** en Google.
3. Si Google falla, la reserva **igual queda aprobada** en la app y se marca "pendiente de
   publicar" para reintentar. Nunca se pierde una aprobación por un error de Google.

**Qué hace falta para esto:**
- La cuenta de servicio que ya se usa para las cuotas (`google-service-account.json`)
  necesita permiso de **"Hacer cambios en los eventos"** sobre el calendario destino,
  y el scope `calendar.events` (hoy solo tiene `spreadsheets.readonly`).
- Hay que definir **a qué calendario van las reservas**. Hoy están configurados cuatro:
  Talleres, Bayer Experimental, Bayer Band y Vencimientos. → **A confirmar** cuál es el
  destino (se deja configurable por variable de entorno, así se cambia sin tocar código).

---

### CU-A4 · Seguir mis solicitudes
**Actor:** Subcomisión

1. Ve la lista de solicitudes que hizo su subcomisión, con estado y comentarios.
2. Puede **editar** una pendiente o con observaciones, y **cancelar** una propia.
3. No ve las solicitudes de otras subcomisiones.

---

## 4. Módulo B — Inventario

### CU-B1 · Ver y buscar el inventario
**Actor:** cualquiera con usuario · **Frecuencia:** semanal

1. Entra a **Inventario** y ve la lista completa de bienes de la biblioteca
   (equipamiento, mobiliario, herramientas, instrumentos, material de taller…).
2. Busca por nombre y filtra por **categoría**, **ubicación**, **estado** y **referente**.
3. Toca un ítem → ficha completa.

> Explícitamente **no** es el catálogo de libros: eso vive en Koha. Esto es el
> patrimonio y el equipamiento de la institución.

---

### CU-B2 · Ficha de un ítem
**Actor:** cualquiera con usuario

Muestra:
- **Qué es**: nombre, categoría, descripción, foto, cantidad
- **Identificación**: número de inventario, marca/modelo, nº de serie
- **Dónde está**: ubicación actual (y ubicación "de origen")
- **A quién pedirlo**: **referente** (persona + subcomisión + contacto)
- **Estado**: nuevo · bueno · regular · a reparar · fuera de servicio · dado de baja
- **Origen**: compra / donación · fecha de alta · valor · quién lo donó
- **Disponibilidad**: si se presta o no, y si está prestado ahora mismo
- **Historial**: todos los movimientos (préstamos, devoluciones, cambios de ubicación,
  cambios de estado, reparaciones), con fecha y quién lo registró
- **Notas**

---

### CU-B3 · Agregar o editar un ítem
**Actor:** Bibliotecaria o Comisión Directiva

1. **"Agregar ítem"** → formulario con los campos de CU-B2.
2. El **número de inventario** se sugiere solo (correlativo por categoría) y se puede pisar.
3. Puede subir una **foto**.
4. Guarda → el ítem queda en la lista y se registra "alta" en su historial.
5. Editar funciona igual; cada cambio relevante (estado, ubicación, referente) queda en el historial.

**Alternativa:** *Dar de baja* — no se borra, pasa a estado "dado de baja" con motivo y fecha,
y deja de aparecer en la lista por defecto (se ve activando "incluir dados de baja").

---

### CU-B4 · Registrar un préstamo interno y su devolución
**Actor:** Bibliotecaria o Comisión Directiva · **Frecuencia:** semanal

1. Desde la ficha del ítem → **"Prestar"**.
2. Completa: a quién (persona / subcomisión / institución externa), fecha de salida,
   **fecha de devolución prevista**, para qué, y una nota de en qué estado sale.
3. Guarda → el ítem figura **Prestado**, con a quién y hasta cuándo.
4. Cuando vuelve → **"Registrar devolución"**: fecha real, estado en que volvió, observaciones.
5. Todo queda en el historial del ítem.

**Reglas:**
- Un ítem prestado no se puede volver a prestar sin devolverlo primero.
- La lista de inventario resalta lo **prestado vencido** (pasó la fecha prevista).

---

### CU-B5 · Pedir prestado un ítem
**Actor:** Subcomisión

1. Desde la ficha toca **"Pedirlo"** → ve **a quién dirigirse** (el referente y su contacto)
   y puede dejar un pedido registrado (para qué y para cuándo).
2. El pedido aparece en la bandeja de bibliotecarias/CD, que lo convierten en préstamo (CU-B4).

---

## 5. Módulo C — Talleres y actividades

### CU-C1 · Ver la cartelera de talleres y actividades
**Actor:** cualquiera con usuario · **Frecuencia:** semanal

1. Entra a **Talleres** y ve las actividades **vigentes** en formato tarjetas.
2. Filtra por: estado (vigente / próximo / finalizado), tipo (taller · ciclo · evento único ·
   grupo estable), día de la semana, público (infantil / adolescente / adulto / todo público),
   subcomisión responsable.
3. Toca una tarjeta → ficha del taller.

---

### CU-C2 · Ficha de un taller
**Actor:** cualquiera con usuario

Muestra:
- Nombre, descripción, **imagen/flyer**
- **Tallerista / responsable** y contacto
- **Subcomisión** a cargo
- **Cuándo**: día(s) y horario, fecha de inicio y de fin
- **Dónde**: espacio
- **Público** y **cupo** (y cuántos lugares quedan, si se lleva inscripción)
- **Arancel**: gratuito · a la gorra · arancelado (monto) · requiere ser socio/a
- **Estado**: en curso · por comenzar · finalizado · suspendido
- **Cómo anotarse** (texto libre: mail, WhatsApp, presencial)

---

### CU-C3 · Crear o editar un taller
**Actor:** Bibliotecaria o Comisión Directiva

1. **"Nuevo taller"** → formulario con los campos de CU-C2.
2. Al guardar, si tiene día y horario fijos, **ofrece generar las fechas en el calendario**
   (y, si corresponde, reservar el espacio automáticamente).
3. Editar y **finalizar / suspender** un taller (queda en el histórico, no se borra).

---

### CU-C5 · Cargar quiénes participan de un taller
**Actor:** Bibliotecaria, Comisión Directiva o el tallerista · **Frecuencia:** al inicio de cada taller

No hay inscripción con cupo, lista de espera ni bajas automáticas: eso quedó afuera a
propósito. Lo que sí hay es **saber quiénes están participando**.

1. Desde la ficha del taller → **"Participantes"**: lista de personas anotadas
   (nombre, contacto, si es socia/o, fecha de alta, notas).
2. Se carga de dos maneras:
   - **A mano**, agregando personas una por una desde la app.
   - **Por formulario**: cada taller tiene un **enlace propio de inscripción** que se puede
     compartir (WhatsApp, redes, cartel con QR). Quien lo completa entra directo a la lista
     de participantes de ese taller, **sin necesidad de tener usuario**.
3. Quien administra el taller ve las altas que van entrando y puede editarlas o darlas de baja.
4. Se puede **exportar la lista** y **mandarle un mail al grupo** (reusa el módulo de envío).

**Reglas:**
- El enlace del formulario se puede **abrir y cerrar** (cuando el taller ya arrancó, se cierra).
- Si la persona ya figura (mismo mail o teléfono), se avisa en vez de duplicar.
- El formulario pide el **mínimo indispensable** y aclara para qué se usan los datos: es
  gente que puede no ser socia de la biblioteca.
- La lista de participantes la ven **solo** bibliotecarias, CD y quien tenga ese taller a
  cargo — **no** el resto de las subcomisiones.

---

### CU-C4 · Historial de talleres
**Actor:** Bibliotecaria o Comisión Directiva

1. Ver todos los talleres, incluidos los finalizados, filtrando por año.
2. Sirve para memoria institucional, para las **actas** y para rendiciones a CONABIP.

---

## 6. Módulo D — Versión de celular

No es una sección: es una **capa que atraviesa toda la app**. Hoy en pantalla chica el
menú lateral se apila arriba y las tablas se desbordan.

### CU-D1 · Usar la app desde el celular
1. Entra desde el teléfono y ve un **menú inferior fijo** con las 4–5 secciones de su rol
   (el resto en "Más").
2. Las **tablas se convierten en tarjetas** apiladas, legibles sin zoom ni scroll horizontal.
3. Los botones y campos tienen tamaño de dedo (≥ 44 px) y los inputs no provocan zoom en iOS.
4. Los formularios largos (solicitud, alta de ítem) se muestran en pasos.
5. Puede **agregar la app a la pantalla de inicio** y abrirla como una aplicación
   (PWA: ícono, nombre, sin barra del navegador).

**Reglas:** una sola base de código, responsive. No hay app nativa ni tiendas.

---

## 7. Módulo E — Registro de actividades realizadas

Hoy lo que pasa en la biblioteca queda en la memoria de las personas, en un grupo de
WhatsApp o en el Google Calendar, que dice lo que **iba** a pasar, no lo que **pasó**.
Este módulo junta lo que efectivamente sucedió: qué fue, quién estuvo a cargo, cuánta
gente vino, de qué edades y cómo salió. Con eso hay memoria institucional y **datos
para decidir**: qué convoca, a qué públicos no se llega, qué días y horarios funcionan,
qué espacios se usan. Es para **uso interno** de la biblioteca.

| | Qué guarda |
|---|---|
| Calendario / Solicitudes (A) | Lo **planificado** |
| Talleres (C) | La **propuesta**: de qué se trata, quién lo da, cuándo |
| Participantes (CU-C5) | **Nombres** de quienes se anotan |
| **Registro (E)** | Lo que **pasó**: cada actividad única, y **un resumen por mes** de cada taller. Con **números agregados**, sin datos personales del público |

**Actores nuevos:** *quien estuvo a cargo* (tallerista, integrante de subcomisión,
invitada/o externa/o) **entra por link, sin usuario**. A futuro, el *público asistente*
(encuesta por QR, CU-E6).

---

### CU-E1 · Generar un link de registro
**Actor:** Bibliotecaria o Comisión Directiva · **Frecuencia:** semanal

Hay tres clases de link:

- **Link general** — uno fijo, sirve para cualquier actividad única. Se comparte una vez
  (cartel con QR en la sala, grupo de WhatsApp) y el formulario viene vacío.
- **Link de una actividad** — se genera desde un evento del calendario o una reserva
  aprobada. El formulario viene **precargado** (nombre, fecha, horario, espacio,
  responsable) y la persona solo completa lo que pasó. El registro queda **vinculado a
  lo planificado**, lo que permite comparar lo previsto con lo realizado.
- **Link de un taller** — uno fijo por taller, para el **resumen mensual** (CU-E7). Se le
  pasa una vez al tallerista y lo usa todos los meses. Hasta que exista el módulo Talleres,
  al crearlo se cargan a mano nombre, tallerista, espacio, día y horario, tipo y temática.

1. Entra a **Registro de actividades** → **"Nuevo link"** → elige la clase de link.
2. Opcional: fecha de vencimiento.
3. Copia el link, lo manda por WhatsApp o descarga el QR.

**Reglas:**
- El link lleva un código largo imposible de adivinar, y se puede **cerrar** en cualquier momento.
- El link de una actividad se cierra solo al recibir el registro (se puede reabrir).
  El de un taller acepta **un resumen por mes**.

> **Recomendación:** priorizar el link de actividad. Es el que da datos completos y
> comparables; el general es el comodín para lo que no estaba agendado.

---

### CU-E2 · Registrar una actividad (formulario público)
**Actor:** quien estuvo a cargo, **sin usuario** · **Frecuencia:** después de cada actividad

Para **actividades únicas**. Los talleres regulares no se registran encuentro por
encuentro: usan el resumen mensual (CU-E7).

Pensado primero para celular, en **3 pasos cortos**, completable en menos de 3 minutos.

**Paso 1 — Qué fue**
- **Nombre** de la actividad *(obligatorio)*
- **Tipo** *(obligatorio)*: seminario · charla / conversatorio ·
  presentación de libro · club de lectura · proyección / cineclub · música / espectáculo ·
  muestra / exposición · visita escolar o institucional · feria / jornada ·
  reunión / asamblea · otra
- **Fecha, hora de inicio y de fin** *(obligatorio)*
- **Espacio** (la lista de espacios de la app) u "otro lugar / virtual"
- **Modalidad**: presencial · virtual · híbrida
- **Temática** (1 a 3): literatura · infancias · memoria y DDHH · géneros · ambiente / huerta ·
  música · artes visuales · teatro · ciencia y tecnología · oficios · salud · barrio y comunidad · otra
- **A cargo**: nombre(s) y rol (tallerista, subcomisión, invitada/o externa/o, bibliotecaria)
- **Organiza**: la biblioteca · una subcomisión (cuál) · junto con otra institución (cuál)

**Paso 2 — Quiénes vinieron**
- **Cantidad total de personas** *(obligatorio)*, con tilde "es aproximado"
- **Por franja etaria** (recomendado): 0–5 · 6–12 · 13–17 · 18–29 · 30–59 · 60+.
  Contadores con + y −; si se completan, el total se calcula solo.
- **Cuántas venían por primera vez** a la biblioteca (aproximado)
- **Cómo se enteraron** (varias): redes · WhatsApp · cartel · boca en boca ·
  escuela / institución · ya venían · no sé
- **Acceso**: gratuita · a la gorra · arancelada (monto) · recaudación total (opcional)

**Paso 3 — Cómo salió**
- **De qué se trató**: 2 o 3 líneas (es lo que después va a la memoria anual)
- **Valoración** de quien coordinó: 1 a 5
- **Qué funcionó bien** · **Qué mejorar o qué faltó**
- **Problemas o incidentes**: con el espacio, el equipamiento, algo roto o faltante,
  con tilde **"requiere atención"**
- **¿Continúa o se repetiría?** sí · no · a definir
- **Fotos** (hasta 3, opcional), con tilde "hay autorización para publicarlas"
- **Quién completa**: nombre y contacto *(obligatorio)*

4. Envía → pantalla de agradecimiento con el resumen de lo cargado.

**Alternativas:**
- *E2a* Ya existe un registro con la misma fecha, espacio y un nombre parecido → avisa
  y deja enviar igual, marcado como posible duplicado.
- *E2b* Se corta la conexión → lo escrito queda guardado en el teléfono y se reintenta.

**Reglas:**
- **Pocos obligatorios**: nombre, tipo, fecha y horario, total de personas y quién completa.
  El resto suma pero no bloquea; un formulario largo y rígido termina sin completarse.
- **Opciones cerradas** donde se va a querer sumar o comparar (tipo, temática, franja,
  difusión). Texto libre solo para la descripción y el feedback.
- La página pública **nunca muestra** otros registros. Protección contra spam: campo
  trampa invisible y límite de envíos por conexión.

---

### CU-E7 · Resumen mensual de un taller (formulario público)
**Actor:** tallerista o responsable del taller, **sin usuario** · **Frecuencia:** una vez por mes, al cerrar el mes

1. Abre el link de su taller. Ya vienen cargados el taller, el tallerista, el espacio,
   el día y horario, y el **mes a informar** (el que acaba de cerrar).
2. Completa:
   - **Encuentros realizados** *(obligatorio)* y **suspendidos**, con motivo
     (feriado · clima · ausencia del tallerista · otro)
   - **Participantes del mes**: personas distintas que vinieron *(obligatorio)*
   - **Asistencia promedio** por encuentro
   - **Por franja etaria** (de los participantes): 0–5 · 6–12 · 13–17 · 18–29 · 30–59 · 60+
   - **Altas y bajas**: cuántas personas se sumaron y cuántas dejaron en el mes
   - **Qué se trabajó** en el mes (2 o 3 líneas)
   - **Cómo viene el taller**: valoración 1 a 5 · qué mejorar o qué hace falta
   - **Problemas o incidentes**, con tilde "requiere atención"
   - **Fotos** (opcional) · **Quién completa** *(obligatorio)*
3. Envía → pantalla de agradecimiento con el resumen.

**Reglas:**
- **Un resumen por taller por mes.** Si ese mes ya se envió, avisa; las correcciones se
  hacen desde la bandeja (CU-E3).
- Tipo, temática, modalidad y acceso salen de los datos del taller: no se preguntan cada mes.

---

### CU-E3 · Revisar y validar los registros recibidos
**Actor:** Bibliotecaria o Comisión Directiva · **Frecuencia:** semanal

1. Bandeja **"Recibidos"**: nuevos primero, con ⚠️ si reporta un incidente o parece duplicado.
2. Abre uno, **corrige o completa** lo que haga falta (se guardan lo enviado y lo corregido).
3. Lo marca **Validado**, o **Descartado** con motivo (spam, duplicado, prueba).
4. También puede **cargar un registro directamente** desde la app, con el mismo formulario.

**Reglas:** **solo los registros validados cuentan en las estadísticas**. Los incidentes
marcados "requiere atención" aparecen además como aviso en el inicio.

---

### CU-E4 · Actividades que faltan registrar
**Actor:** el sistema · **Frecuencia:** diaria

1. Busca reservas aprobadas que **ya pasaron y no tienen registro**, y talleres activos
   cuyo **mes anterior no tiene resumen**.
2. Los muestra en una lista **"Faltan registrar"**, con un botón para mandarle al
   responsable el link (WhatsApp o mail).
3. Opcional: mail automático al responsable, a las 24 h de la actividad o al cerrar el mes
   en el caso de los talleres (reusa el módulo de Automáticos).

> **Por qué importa:** sin esto se registra lo que salió bien y lo que alguien se acordó
> de cargar, y los números terminan mostrando una biblioteca que no es la real.

---

### CU-E5 · Tablero de actividades y reportes
**Actor:** Bibliotecaria o Comisión Directiva · **Frecuencia:** mensual / anual

Preguntas que tiene que poder responder:

| Pregunta | Para decidir… |
|---|---|
| ¿Cuántas actividades hicimos y cuánta gente vino? Por mes y contra el año anterior | Si la programación crece o se achica |
| ¿Qué tipos y temáticas convocan más? (**promedio** por actividad, no total) | Qué repetir, qué replantear |
| ¿A qué edades llegamos y a cuáles no? | A qué públicos apuntar la programación |
| ¿Qué días y horarios tienen más asistencia? | Cuándo programar |
| ¿Qué espacios se usan y cuántas horas? | Uso y mantenimiento de espacios |
| ¿Cuánto de lo planificado se hizo? (reservas aprobadas vs. registradas) | Qué se cae y por qué |
| ¿Por dónde se entera la gente? | Dónde poner el esfuerzo de difusión |
| ¿Cuánta gente nueva trae cada actividad? | Qué actividades acercan socias/os nuevas/os |
| ¿Cómo evoluciona cada taller mes a mes? (participantes, altas y bajas, suspensiones) | Detectar a tiempo un taller que se vacía |
| ¿Con qué instituciones hacemos actividades? | Alianzas |

Filtros: período, tipo, temática, espacio, organizador, subcomisión.

> Las actividades únicas cuentan **por evento** y los talleres **por mes** (encuentros y
> participantes). El tablero los muestra por separado para no sumar cosas que no se comparan.

**Exportaciones:**
- **Excel / CSV** con todos los registros validados.
- **Memoria anual** armada: listado de actividades, totales y gráficos. Base para la
  asamblea y las actas.
- **Ficha de una actividad** en PDF, con fotos, para prensa o difusión.

---

### CU-E6 · *(fase 2)* Encuesta al público
**Actor:** público asistente, sin usuario

1. Cada actividad tiene un QR que se muestra al cierre.
2. Encuesta anónima de 30 segundos: ¿qué te pareció? (1 a 5) · ¿cómo te enteraste? ·
   ¿es tu primera vez en la biblioteca? · ¿qué te gustaría que haya?
3. Las respuestas se suman al registro de esa actividad.

Suma la mirada del público a la de quien coordinó, que naturalmente tiende a ser optimista.

---

### Criterios para que los datos sirvan a largo plazo
1. **Listas estables.** Tipos, temáticas y franjas se editan desde la app, pero renombrar
   no crea una categoría nueva: "Charla" pasa a "Conversatorio" sin partir el histórico en dos.
2. **Registrar cerca de cuando pasó.** Las actividades únicas, en los días siguientes; los
   talleres, apenas cierra el mes. Link precargado + recordatorio (CU-E4): un registro
   hecho mucho después es una estimación.
3. **Público en números, nunca en nombres.** El registro no guarda datos personales de
   asistentes (los nombres viven en Participantes, CU-C5). Fotos con menores, solo con
   autorización marcada.
4. **Validación humana** antes de que algo cuente en las estadísticas.
5. **No empezar de cero.** Los eventos pasados de los Google Calendar se pueden importar
   como registros "sin datos de asistencia", y si hay planillas o actas de años anteriores
   se pueden cargar para tener con qué comparar.

### Entregas dentro del módulo
| # | Entrega | Qué incluye |
|---|---|---|
| E·1 | **Registro básico** *(hecho)* | Links (general, de actividad y de taller), formulario de actividad, resumen mensual de taller, bandeja de validación, export Excel |
| E·2 | **Tablero** | Estadísticas de CU-E5 y memoria anual |
| E·3 | **Completitud y público** | "Faltan registrar" con recordatorio, fotos, encuesta al público |

---

## 8. Módulo F — Notas de socios (desde Koha)

Las bibliotecarias escriben notas en la ficha de cada socio para dejar novedades y
recordatorios entre ellas. Hoy esas notas **solo se ven abriendo el socio en Koha, de a uno**.

### Qué hay en Koha
*Relevado el 15/09/2026 contra la base real, con consultas de solo lectura. Koha 3.18.*

- Las notas son los **mensajes internos** de circulación: tabla `messages`, tipo `L`
  (los ve solo el personal, no el socio). Los campos de nota de la ficha
  (`borrowernotes`, `opacnote`) están vacíos: no se usan.
- **7.258 notas** de 1.367 socios distintos, desde mayo de 2016. Uso en aumento:
  1.256 en 2024, 1.741 en 2025 y 1.344 en lo que va de 2026.
- De qué son (clasificación aproximada por palabras clave):

| Tipo | Cantidad | Ejemplos |
|---|---|---|
| Cuotas y pagos | ~5.600 (77 %) | "cuotas hasta DICIEMBRE", "inscripción y cuotas…", "DEBITO AUTOMATICO" |
| Reclamos de libros | ~830 (11 %) | "Reclamé libros x wp" |
| Novedades y recordatorios | ~810 (11 %) | libro roto o duplicado, acuerdo de pago, baja, préstamo de Kindle, "hay que cambiar la etiqueta", "dice que lo devuelve en noviembre" |

**Limitaciones de Koha 3.18:** la nota **no guarda quién la escribió** (solo fecha y sede),
no tiene estado (pendiente / resuelta) y no hay forma de ver o buscar en todas a la vez.

> **Oportunidad:** las notas "cuotas hasta…" son un historial de pagos desde 2016. Se
> pueden cruzar con el módulo de Cuotas para detectar diferencias con la planilla.

---

### CU-F1 · Ver las notas en la ficha del socio
**Actor:** Bibliotecaria o Comisión Directiva · **Frecuencia:** diaria

1. En la ficha del socio aparece la sección **Notas**: línea de tiempo, más nuevas primero,
   con fecha y etiqueta (**Cuotas** · **Reclamo** · **Novedad**).
2. Un filtro oculta las de cuotas, que son la mayoría y tapan las novedades.
3. Si hay una novedad de los últimos 30 días, la ficha la muestra arriba, destacada.

---

### CU-F2 · Notas recientes de todos los socios
**Actor:** Bibliotecaria o Comisión Directiva · **Frecuencia:** diaria

1. Dentro de Socios, nueva vista **"Notas recientes"**: las notas de los últimos días de
   **todos** los socios, con nombre y carnet. Tocar una abre la ficha.
2. Filtra por tipo y **busca texto en todas las notas** (ej.: "Kindle", "duplicado", "roto").
3. Una novedad se puede marcar **"resuelta"** (se guarda en la app, Koha no lo permite),
   así la vista muestra lo que sigue pendiente.

> Hoy un recordatorio entre bibliotecarias solo se ve si alguien abre justo esa ficha.

---

### CU-F3 · Ver la nota antes de reclamar
**Actor:** Bibliotecaria o Comisión Directiva

- En **Préstamos vencidos**, **Mails** y la vista previa de **Automáticos**, los socios con
  una novedad reciente muestran 📝 y el texto al tocarlo.
- Ejemplo: "dice que lo devuelve en noviembre" → evita mandar un reclamo que ya estaba acordado.

---

> **Decidido: solo ver.** Las notas se siguen escribiendo, editando y borrando en Koha,
> como hoy. La app no escribe en Koha.

---

## 9. Módulo G — Pizarrón semanal de las bibliotecarias

Un pizarrón compartido, organizado por semana, para los avisos, tareas y recordatorios
del equipo que **no son de un socio en particular** (para eso están las notas del Módulo F):
"falta papel para la impresora", "el jueves viene una escuela", "revisar la planilla de
cuotas", "el sábado se abre más tarde".

### CU-G1 · Ver el pizarrón de la semana
**Actor:** Bibliotecaria · **Frecuencia:** diaria

1. Entra a **Pizarrón** y ve la semana en curso (lunes a domingo).
2. Las notas se ven como tarjetas, agrupadas en **Fijadas** · **Toda la semana** ·
   **un grupo por día** · **Hechas**. En celular, una lista por día.
3. Cada nota muestra: texto, **quién la escribió** (sale de su usuario) y cuándo, y
   opcionalmente tipo (aviso · tarea · recordatorio), día, destinataria (todas o una
   compañera) y **socio vinculado** (abre su ficha).
4. En el menú, junto a **Pizarrón**, un contador con las notas y respuestas que dejaron
   las demás desde su última visita.

### CU-G2 · Dejar una nota
1. **"Nueva nota"** → escribe el texto (lo único obligatorio) → opcional: tipo, día,
   destinataria, socio, color, fijarla.
2. Aparece para las demás sin recargar (la vista se actualiza sola cada minuto).
3. Se puede responder con un comentario corto ("ya lo compré").

### CU-G3 · Tareas y arrastre de semana
- Una **tarea** se tilda como **hecha** (queda quién y cuándo) y pasa al grupo Hechas.
- **Las tareas no hechas pasan solas a la semana siguiente**, con la marca
  "viene de la semana del 8/9". Los avisos y recordatorios quedan en su semana.
- Un **recordatorio con día** aparece destacado ese día como "hoy".

### CU-G4 · Semanas anteriores
- Flechas para ir a semanas pasadas (solo lectura) y un buscador en todo el pizarrón.
- Funciona como **bitácora del equipo**.

**Reglas:**
- **Solo lo ven las bibliotecarias.** Ni la Comisión Directiva ni las subcomisiones: es una
  excepción a "la Comisión ve lo mismo que las bibliotecarias", con un permiso propio.
- Solo quien escribió una nota la edita o la borra; **cualquiera** puede marcar una tarea como hecha.
- Nada se pierde al cambiar de semana: todo queda en el histórico.

---

## 10. Qué hay que construir por debajo

### 10.1 Usuarios y roles (lo más importante; en desarrollo)
- Login doble: **Koha** (bibliotecarias, como hoy) **+ usuario/contraseña propio de la app**
  (comisión y subcomisiones), resueltos por el mismo endpoint y el mismo token.
- ABM de usuarios: alta, rol, subcomisión, activar/desactivar, resetear contraseña.
- El token lleva el **rol**; cada endpoint nuevo valida permisos; el menú muestra
  solo lo que el rol puede ver.

### 10.2 Datos
Se reutiliza `app/storage.py` (archivo JSON en local, Postgres `app_kv` en producción).
Claves nuevas: `usuarios`, `espacios`, `solicitudes`, `inventario`, `inventario_movs`, `talleres`,
`registro_links`, `registros_actividad`, `notas_resueltas`, `pizarron`.

Las **notas de socios** (Módulo F) no se copian: se leen de Koha con informes guardados,
como el resto de los datos de socios. En la app solo se guarda qué novedades están "resueltas".

> ⚠️ Riesgo conocido: el guardado es leer-todo / escribir-todo. Con el volumen de una
> biblioteca popular alcanza, pero si dos personas guardan a la vez se puede pisar un
> cambio. Se mitiga con un bloqueo simple por clave; si el uso crece, tablas propias.
> Los registros de actividades y el pizarrón crecen año a año: conviene **una clave por año**
> (`registros_actividad_2026`, `pizarron_2026`) para que no se vuelvan pesados.

### 10.3 Fotos e imágenes
Ítems de inventario, flyers de talleres y fotos de actividades registradas necesitan
imágenes. Hoy no hay dónde guardarlas. Lo más simple: guardar en el volumen del servidor
(`APP_DATA_DIR`) con un límite de tamaño.

### 10.4 Páginas públicas
El formulario de registro (Módulo E), el de inscripción a talleres (CU-C5) y la encuesta al
público (CU-E6) son las únicas páginas **sin login**. Van por rutas separadas, con acceso
solo mediante el código del link, límite de envíos y sin mostrar nunca datos guardados.

---

## 11. Decisiones

### 11.1 Ya tomadas

| # | Tema | Decisión |
|---|---|---|
| 1 | **Orden de trabajo** | Arrancar por **usuarios y roles**. Sin eso no hay nada que mostrarle a una subcomisión |
| 2 | **Aprobación de solicitudes** | Se puede **editar fecha/hora/espacio al aprobar**: casi nunca coincide con lo pedido |
| 3 | **Google Calendar** | **Sí**, la reserva aprobada se publica automáticamente en el Google Calendar de la biblioteca |
| 4 | **Comisión Directiva** | Ve **todo**, igual que las bibliotecarias (socios, cuotas, mails incluidos) |
| 5 | **Talleres** | **Sin** inscripción con cupo, **pero sí** lista de participantes, cargable a mano o por formulario público (CU-C5) |
| 6 | **Talleres en el registro** | **Resumen mensual** por taller, no encuentro por encuentro (CU-E7) |
| 7 | **Notas de socios** | **Solo ver.** Se siguen escribiendo en Koha; la app no escribe ahí |
| 8 | **Pizarrón** | **Solo bibliotecarias.** La Comisión Directiva no lo ve (excepción a la decisión 4) |
| 9 | **Categorías del registro** | La lista propuesta de tipos, temáticas y franjas etarias **sirve**. El registro es de uso interno, no para CONABIP |

### 11.2 Todavía abiertas

1. **A qué Google Calendar** van las reservas aprobadas: hoy hay cuatro configurados
   (Talleres, Bayer Experimental, Bayer Band, Vencimientos). Se deja **configurable**, pero
   hay que decir cuál es el destino por defecto.
2. **Aviso por mail** al solicitante cuando se resuelve su pedido: ¿sí o no? El módulo de
   envío ya está hecho, es sumarlo.
3. **Inventario**: ¿editan **solo** bibliotecarias y CD, o también el referente de cada ítem
   puede actualizar el suyo?
4. **Espacios**: hace falta la lista real de espacios de la biblioteca con su capacidad.
5. **Subcomisiones**: hace falta la lista real y quiénes las integran (para dar de alta usuarios).
6. **Encuesta al público** (CU-E6): ¿ahora o más adelante? *Recomendación:* más adelante,
   cuando el registro básico ya se use.
7. **Subcomisiones y registros**: ¿ven los registros y estadísticas de sus propias
   actividades? *Recomendación:* sí, solo las suyas.

---

## 12. Orden de construcción propuesto

| # | Entrega | Estado | Por qué en este lugar |
|---|---|---|---|
| 1 | **Usuarios y roles** + menú por rol | En desarrollo | Sin esto ninguna sección nueva se puede mostrar a nadie |
| 2 | **Calendario + solicitudes de espacio** | En desarrollo | Es el pedido más concreto y el que más se usa |
| 3 | **Notas de socios** (ver, CU-F1 a F3) | Hecho | Chico, uso diario, reusa el acceso a Koha que ya existe |
| 4 | **Pizarrón semanal** | Hecho | Chico y autónomo, uso diario del equipo |
| 5 | **Registro de actividades** — E·1 | Hecho | Cuanto antes arranca, antes hay histórico; el formulario público se hace para celular desde el inicio |
| 6 | **Versión celular** (menú inferior, tarjetas, PWA) | — | Transversal: conviene antes de sumar más pantallas |
| 7 | **Talleres y actividades** | — | Alimenta el calendario y el registro |
| 8 | **Registro** — E·2 y E·3 | — | El tablero rinde con algunos meses de datos; "faltan registrar" necesita talleres |
| 9 | **Inventario** | — | El más autónomo, se puede hacer sin depender del resto |
