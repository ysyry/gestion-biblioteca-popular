# Roadmap / Próximas funcionalidades

## ✅ Hecho
1. **Préstamos vigentes y vencidos** — listados con datos reales (export TSV de Koha).
2. **Búsqueda de socios + ficha completa** — al buscar un socio y abrir su ficha se ve:
   datos de contacto, **deuda / saldo a favor**, **cuotas**, **préstamos vigentes**,
   **estado de cuenta** completo e **historial de préstamos** devueltos.
3. **Branding** — logo y paleta del Manual de Identidad (naranja/amarillo/magenta/rojo).

---

## 🔜 Próxima: Envío de mails a socios seleccionados

**Objetivo** (pedido del usuario): poder mandar un correo a un conjunto de socios
elegidos, con un mensaje común que además se pueda personalizar por persona, tomando
automáticamente el nombre de cada socio.

### Comportamiento esperado
- **Selección de destinatarios**: marcar socios (checkbox) desde un listado/búsqueda,
  o desde un grupo (ej: "todos los que deben", "vencidos", una categoría).
- **Vista general (mensaje para todos)**: un editor donde se escribe el cuerpo del
  mail con **variables de combinación**, ej: `Hola {{nombre}}, te recordamos…`.
  La app reemplaza `{{nombre}}` (y otros campos: `{{apellido}}`, `{{deuda}}`,
  `{{vence}}`) por los datos de cada socio.
- **Vista individual (personalizar)**: poder abrir el mail ya armado de un socio
  puntual y editarlo a mano antes de enviar, sin afectar a los demás.
- **Previsualización** por destinatario antes de enviar.
- **Envío**: por SMTP (cuenta de la biblioteca) o un proveedor (Resend / Amazon SES /
  Gmail API). Registrar a quién se envió y resultado (enviado / error / sin email).

### Notas técnicas
- El dato `email` ya lo trae la ficha del socio (tabla `borrowers.email`).
- Filtrar socios **sin email** y avisarlo (no romper el envío masivo).
- Empezar simple: backend con un endpoint `POST /api/mail/send` que recibe la lista de
  destinatarios + plantilla + overrides individuales, y envía. Frontend con las dos vistas.
- Cuidado legal/privacidad: enviar solo a socios de la biblioteca, con motivo legítimo
  (recordatorio de préstamo/cuota). Incluir pie con datos de la institución.

### Decisiones a tomar (cuando arranquemos)
- Proveedor de envío (SMTP propio vs. Resend/SES) y remitente verificado.
- Si se guarda historial de campañas enviadas.
- Variables de combinación disponibles.

---

## 🔮 Más adelante: WhatsApp (a investigar — riesgo de bloqueo)

El usuario quiere lo mismo pero por WhatsApp. **Hay que investigar bien porque WhatsApp
bloquea el envío masivo no solicitado.** Resumen del panorama:

- **NO usar automatización no oficial** (whatsapp-web.js, bots sobre WhatsApp Web) para
  envíos masivos: Meta detecta el patrón y **banea el número** (riesgo alto, sobre todo
  con mensajes iguales a muchos destinatarios que no contestan).
- **Vía oficial = WhatsApp Business Platform (Cloud API)** vía Meta o un BSP (Twilio,
  360dialog, etc.):
  - Requiere **mensajes de plantilla preaprobados** por Meta para iniciar conversación.
  - Requiere **opt-in** del socio (que haya aceptado recibir mensajes).
  - Tiene **costo por conversación** y un alta/verificación del número de empresa.
  - A cambio: es legal, escalable y no te banean.
- **Recomendación preliminar**: empezar por **mail** (gratis, sin riesgo). Para WhatsApp,
  evaluar el volumen real y, si se justifica, ir por la API oficial con plantillas y opt-in.
  Investigar costos del BSP y el proceso de aprobación de plantillas antes de comprometerse.
