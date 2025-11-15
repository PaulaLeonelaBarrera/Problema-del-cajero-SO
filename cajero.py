import pygame
import threading
import time
import random
from queue import Queue, Empty

# =========================
# PARÁMETROS DE UI/TIMING
# =========================
ANCHO_PANTALLA, ALTO_PANTALLA = 1366, 768
FACTOR_ESCALA = ANCHO_PANTALLA / 800*0.9

# Paleta
BLANCO = (255, 255, 255)
NEGRO = (20, 20, 20)
GRIS = (60, 60, 60)
GRIS_SUAVE = (200, 205, 215)
GRIS_PANEL = (235, 238, 243)
VERDE = (30, 160, 60)
ROJO = (200, 50, 50)
AZUL = (70, 120, 220)
AMARILLO = (240, 210, 60)
ACERO = (150, 155, 170)
ACERO_OSC = (110, 115, 130)

FPS = 60
VEL_PASO = int(3 * FACTOR_ESCALA)  # <- velocidad horizontal
VEL_SUBIDA = max(
	1, int(3 * FACTOR_ESCALA)
)  # <- velocidad vertical para subir/bajar de carril
PAUSA_DECISION = 1.0
DISTANCIA_MIN = int(65 * FACTOR_ESCALA)
EVENTOS_POR_FRAME = 6

# Layout
VENTANILLA_X = int(ANCHO_PANTALLA * 0.75)
VENTANILLA_Y = int(ALTO_PANTALLA * 0.60)
ENTRADA_X = int(ANCHO_PANTALLA * 0.1)
ENTRADA_Y = int(ALTO_PANTALLA * 0.55)

# Carriles: superior (atendiendo/resultado/saliendo) e inferior (entrando/espera)
CARRIL_SUP_Y = ENTRADA_Y
CARRIL_INF_Y = ENTRADA_Y + int(130 * FACTOR_ESCALA)  # espera "debajo"

# =========================
# Buzones (base)
# =========================
NCLIENTES1 = 5
NCLIENTES2 = 5
SALDOINICIAL = 10000
puedoSacar = Queue()
ingresar = Queue()
ok = [Queue() for _ in range(NCLIENTES1 + NCLIENTES2)]
eventos_ui = Queue()
dinero = SALDOINICIAL

# NUEVO: cola unificada para el cajero (FIFO real)
fifo_banco = Queue()

_op_id = 0
_op_id_lock = threading.Lock()


def next_op_id():
	global _op_id
	with _op_id_lock:
		_op_id += 1
		return _op_id


def mux_buzones():
	"""Drena 'puedoSacar' e 'ingresar' y reempaqueta en una cola FIFO única 'fifo_banco'."""
	while True:
		empacados = 0
		try:
			pet = puedoSacar.get(timeout=0.01)
			fifo_banco.put({"canal": "sacar", **pet})
			empacados += 1
		except Empty:
			pass
		try:
			pet = ingresar.get(timeout=0.01)
			fifo_banco.put({"canal": "ingresar", **pet})
			empacados += 1
		except Empty:
			pass
		if empacados == 0:
			time.sleep(0.005)


def cajero():
	global dinero
	while True:
		pet = fifo_banco.get()
		op_id = pet["op_id"]
		cid = pet["id"]
		cant = pet["cantidad"]

		if pet["canal"] == "sacar":
			eventos_ui.put(
				("atendiendo", {"op_id": op_id, "tipo": "extraccion", "cantidad": cant})
			)
			time.sleep(0.35)
			if cant < dinero:
				dinero -= cant
				ok[cid].put({"ok": True, "op_id": op_id})
				eventos_ui.put(
					(
						"resultado",
						{"op_id": op_id, "ok": True, "saldo": dinero, "monto": cant},
					)
				)
			else:
				ok[cid].put({"ok": False, "op_id": op_id})
				eventos_ui.put(
					(
						"resultado",
						{"op_id": op_id, "ok": False, "saldo": dinero, "monto": cant},
					)
				)
			time.sleep(PAUSA_DECISION)

		else:  # depósito
			eventos_ui.put(
				("atendiendo", {"op_id": op_id, "tipo": "deposito", "cantidad": cant})
			)
			time.sleep(0.35)
			dinero += cant
			eventos_ui.put(
				("deposito_ok", {"op_id": op_id, "saldo": dinero, "monto": cant})
			)
			time.sleep(PAUSA_DECISION)


def cliente1(cid):
	while True:
		cant = random.randint(5123, 10000)
		op_id = next_op_id()
		eventos_ui.put(
			(
				"nueva_operacion",
				{
					"op_id": op_id,
					"cliente": cid,
					"tipo": "extraccion",
					"cantidad": cant,
				},
			)
		)
		puedoSacar.put({"id": cid, "cantidad": cant, "op_id": op_id})
		_resp = ok[cid].get()
		time.sleep(random.uniform(2.5, 4.0))


def cliente2(cid):
	while True:
		cant = random.randint(1, 10000)
		op_id = next_op_id()
		eventos_ui.put(
			(
				"nueva_operacion",
				{"op_id": op_id, "cliente": cid, "tipo": "deposito", "cantidad": cant},
			)
		)
		ingresar.put({"id": cid, "cantidad": cant, "op_id": op_id})
		time.sleep(random.uniform(2.2, 3.6))


# =========================
# Visualización
# =========================
class OperacionSprite:
	def __init__(self, op_id, cliente, tipo, cantidad):
		self.op_id = op_id
		self.cliente = cliente
		self.tipo = tipo
		self.cantidad = cantidad
		self.color = AZUL if tipo == "extraccion" else AMARILLO
		self.w = int(36 * FACTOR_ESCALA)
		self.h = int(36 * FACTOR_ESCALA)
		self.x = ENTRADA_X
		self.y = CARRIL_SUP_Y
		self.estado = "entrando"
		self.texto_estado = "Llegando..."
		self._timer = 0.0
		self.resultado_ok = None

	def target_y(self):
		# Los "esperando" se quedan debajo; los atendidos/resultado/saliendo van arriba
		if self.estado in ("entrando", "espera"):
			return CARRIL_SUP_Y
		else:  # atendiendo, resultado, saliendo
			return CARRIL_INF_Y

	def update(self, dt_ms, anterior):
		# Mantener distancia mínima respecto al de adelante (en el mismo carril visual)
		puede_avanzar = True
		if anterior:
			if anterior.x - self.x < DISTANCIA_MIN:
				puede_avanzar = False

		# Movimiento vertical suave hacia su carril objetivo
		ty = self.target_y()
		if self.y < ty:
			self.y = min(ty, self.y + VEL_SUBIDA)
		elif self.y > ty:
			self.y = max(ty, self.y - VEL_SUBIDA)

		# Lógica horizontal
		if self.estado == "entrando":
			self.x = ENTRADA_X

		elif self.estado == "atendiendo":
			# leve vibración
			self.y = self.target_y() + (int(time.time() * 8) % 4) - 2

		elif self.estado == "resultado":
			self._timer += dt_ms / 1000.0
			if self._timer > 0.8:
				self.estado = "saliendo"

		elif self.estado == "saliendo":
			self.x += VEL_PASO
			if self.x > ANCHO_PANTALLA + 200:
				self.estado = "fin"

	def draw(self, surf, font):
		# Dibujar la "persona" que representa la operación (cabeza, torso, brazos, piernas)
		cx = int(self.x)
		cy = int(self.y)
		scale = FACTOR_ESCALA

		# Tamaños relativos
		head_r = max(6, int(8 * scale))
		torso_w = max(10, int(14 * scale))
		torso_h = max(18, int(22 * scale))
		arm_len = max(12, int(16 * scale))
		leg_len = max(16, int(20 * scale))

		# Colores
		skin = (245, 205, 170)
		clothe = self.color  # usa el color según tipo (extracción/deposito)
		outline = NEGRO

		# Posiciones
		head_x = cx
		head_y = cy - torso_h // 2 - head_r
		torso_rect = pygame.Rect(
			cx - torso_w // 2, cy - torso_h // 2, torso_w, torso_h
		)

		# Cabeza
		pygame.draw.circle(surf, skin, (head_x, head_y), head_r)
		pygame.draw.circle(surf, outline, (head_x, head_y), head_r, width=1)

		# Torso (ropa)
		pygame.draw.rect(surf, clothe, torso_rect, border_radius=max(2, int(3 * scale)))
		pygame.draw.rect(surf, outline, torso_rect, width=1, border_radius=max(2, int(3 * scale)))

		# Brazos (líneas)
		arm_y = cy - int(torso_h * 0.2)
		pygame.draw.line(surf, outline, (torso_rect.left, arm_y), (torso_rect.left - arm_len, arm_y + int(arm_len * 0.2)), width=max(2, int(2 * scale)))
		pygame.draw.line(surf, outline, (torso_rect.right, arm_y), (torso_rect.right + arm_len, arm_y + int(arm_len * 0.2)), width=max(2, int(2 * scale)))
		# Manos (pequeños círculos)
		pygame.draw.circle(surf, skin, (torso_rect.left - arm_len, arm_y + int(arm_len * 0.2)), max(3, int(3 * scale)))
		pygame.draw.circle(surf, skin, (torso_rect.right + arm_len, arm_y + int(arm_len * 0.2)), max(3, int(3 * scale)))

		# Piernas
		leg_x_offset = int(torso_w * 0.28)
		pygame.draw.line(surf, outline, (cx - leg_x_offset, torso_rect.bottom), (cx - leg_x_offset, torso_rect.bottom + leg_len), width=max(2, int(2 * scale)))
		pygame.draw.line(surf, outline, (cx + leg_x_offset, torso_rect.bottom), (cx + leg_x_offset, torso_rect.bottom + leg_len), width=max(2, int(2 * scale)))
		# Pies
		pygame.draw.rect(surf, outline, pygame.Rect(cx - leg_x_offset - max(4, int(4 * scale)), torso_rect.bottom + leg_len, max(8, int(8 * scale)), max(4, int(4 * scale))))
		pygame.draw.rect(surf, outline, pygame.Rect(cx + leg_x_offset - max(4, int(4 * scale)), torso_rect.bottom + leg_len, max(8, int(8 * scale)), max(4, int(4 * scale))))

		# Texto de la operación (debajo de la persona)
		text_s = font.render(f"OP{self.op_id} C{self.cliente} {self.tipo[:4]} ${self.cantidad}", True, NEGRO)
		surf.blit(text_s, (cx - text_s.get_width() // 2, torso_rect.bottom + leg_len + int(6 * scale)))

		# Estado (más abajo)
		est = font.render(self.texto_estado, True, NEGRO)
		surf.blit(est, (cx - est.get_width() // 2, torso_rect.bottom + leg_len + int(26 * scale)))

	def on_atendiendo(self):
		self.estado = "atendiendo"
		self.texto_estado = "Atendiendo..."  # el carril cambia en update()

	def on_resultado(self, ok):
		self.estado = "resultado"
		self.resultado_ok = ok
		self._timer = 0.0
		if ok:
			self.texto_estado = "Extracción aprobada"
			self.color = VERDE
		else:
			self.texto_estado = "Extracción rechazada"
			self.color = ROJO

	def on_deposito_ok(self):
		self.estado = "resultado"
		self.resultado_ok = True
		self._timer = 0.0
		self.texto_estado = "Depósito acreditado"
		self.color = VERDE


def dibujar_fondo(screen, font):
	screen.fill((210, 230, 255))
	pygame.draw.rect(
		screen,
		(235, 235, 235),
		(0, int(ALTO_PANTALLA * 0.45), ANCHO_PANTALLA, int(ALTO_PANTALLA * 0.55)),
	)
	# referencias de carriles
	pygame.draw.line(
		screen,
		(220, 220, 225),
		(ENTRADA_X - int(80 * FACTOR_ESCALA), CARRIL_SUP_Y + int(22 * FACTOR_ESCALA)),
		(VENTANILLA_X, CARRIL_SUP_Y + int(22 * FACTOR_ESCALA)),
	)
	pygame.draw.line(
		screen,
		(220, 220, 225),
		(ENTRADA_X - int(80 * FACTOR_ESCALA), CARRIL_INF_Y + int(22 * FACTOR_ESCALA)),
		(VENTANILLA_X, CARRIL_INF_Y + int(22 * FACTOR_ESCALA)),
	)


def draw_atm(screen, font, font_big, atm_state):
	cuerpo = pygame.Rect(
		int(VENTANILLA_X - 180 * FACTOR_ESCALA),
		int(VENTANILLA_Y - 200 * FACTOR_ESCALA),
		int(360 * FACTOR_ESCALA),
		int(260 * FACTOR_ESCALA),
	)
	pygame.draw.rect(screen, ACERO, cuerpo, border_radius=int(18 * FACTOR_ESCALA))
	pygame.draw.rect(
		screen, ACERO_OSC, cuerpo, width=3, border_radius=int(18 * FACTOR_ESCALA)
	)
	header = pygame.Rect(
		cuerpo.x + int(12 * FACTOR_ESCALA),
		cuerpo.y + int(10 * FACTOR_ESCALA),
		cuerpo.width - int(24 * FACTOR_ESCALA),
		int(28 * FACTOR_ESCALA),
	)
	pygame.draw.rect(screen, ACERO_OSC, header, border_radius=int(8 * FACTOR_ESCALA))
	label = font.render("BANCO • CAJERO AUTOMÁTICO", True, BLANCO)
	screen.blit(
		label, (header.x + int(8 * FACTOR_ESCALA), header.y + int(6 * FACTOR_ESCALA))
	)
	screen_rect = pygame.Rect(
		cuerpo.x + int(16 * FACTOR_ESCALA),
		cuerpo.y + int(48 * FACTOR_ESCALA),
		int(280 * FACTOR_ESCALA),
		int(130 * FACTOR_ESCALA),
	)
	bisel = pygame.Rect(
		screen_rect.x - int(6 * FACTOR_ESCALA),
		screen_rect.y - int(6 * FACTOR_ESCALA),
		screen_rect.width + int(12 * FACTOR_ESCALA),
		screen_rect.height + int(12 * FACTOR_ESCALA),
	)
	pygame.draw.rect(screen, (60, 65, 80), bisel, border_radius=int(12 * FACTOR_ESCALA))
	pygame.draw.rect(
		screen, (25, 30, 40), screen_rect, border_radius=int(8 * FACTOR_ESCALA)
	)
	t1 = font_big.render(atm_state["line1"], True, (180, 220, 255))
	t2 = font.render(atm_state["line2"], True, (210, 240, 255))
	screen.blit(
		t1,
		(
			screen_rect.x + int(12 * FACTOR_ESCALA),
			screen_rect.y + int(16 * FACTOR_ESCALA),
		),
	)
	screen.blit(
		t2,
		(
			screen_rect.x + int(12 * FACTOR_ESCALA),
			screen_rect.y + int(54 * FACTOR_ESCALA),
		),
	)
	led_color = atm_state["color"]
	pygame.draw.circle(
		screen,
		led_color,
		(
			screen_rect.right - int(12 * FACTOR_ESCALA),
			screen_rect.y + int(12 * FACTOR_ESCALA),
		),
		int(6 * FACTOR_ESCALA),
	)
	tarjeta = pygame.Rect(
		cuerpo.right - int(60 * FACTOR_ESCALA),
		cuerpo.y + int(64 * FACTOR_ESCALA),
		int(60 * FACTOR_ESCALA),
		int(12 * FACTOR_ESCALA),
	)
	pygame.draw.rect(
		screen, (40, 40, 45), tarjeta, border_radius=int(5 * FACTOR_ESCALA)
	)
	pygame.draw.rect(
		screen,
		(20, 20, 25),
		tarjeta.inflate(0, -int(6 * FACTOR_ESCALA)),
		border_radius=int(3 * FACTOR_ESCALA),
	)
	billetes = pygame.Rect(
		cuerpo.right - int(60 * FACTOR_ESCALA),
		cuerpo.y + int(100 * FACTOR_ESCALA),
		int(60 * FACTOR_ESCALA),
		int(18 * FACTOR_ESCALA),
	)
	pygame.draw.rect(
		screen, (40, 40, 45), billetes, border_radius=int(6 * FACTOR_ESCALA)
	)
	pygame.draw.rect(
		screen,
		(20, 20, 25),
		billetes.inflate(-int(8 * FACTOR_ESCALA), -int(8 * FACTOR_ESCALA)),
		border_radius=int(4 * FACTOR_ESCALA),
	)
	key_w = int(26 * FACTOR_ESCALA)
	key_h = int(18 * FACTOR_ESCALA)
	gap = int(6 * FACTOR_ESCALA)
	base = pygame.Rect(
		cuerpo.right - int(120 * FACTOR_ESCALA),
		cuerpo.bottom - int(90 * FACTOR_ESCALA),
		int(100 * FACTOR_ESCALA),
		int(80 * FACTOR_ESCALA),
	)
	pygame.draw.rect(screen, ACERO_OSC, base, border_radius=int(8 * FACTOR_ESCALA))
	for r in range(3):
		for c in range(3):
			key = pygame.Rect(
				base.x + int(8 * FACTOR_ESCALA) + c * (key_w + gap),
				base.y + int(8 * FACTOR_ESCALA) + r * (key_h + gap),
				key_w,
				key_h,
			)
			if c == 2 and r == 2:
				pygame.draw.rect(
					screen, VERDE, key, border_radius=int(4 * FACTOR_ESCALA)
				)
				pygame.draw.rect(
					screen,
					VERDE,
					key,
					width=1,
					border_radius=int(4 * FACTOR_ESCALA),
				)
			elif c == 0 and r == 2:
				key0 = pygame.Rect(
					base.x + int(8 * FACTOR_ESCALA) + c * (key_w + gap),
					base.y + int(8 * FACTOR_ESCALA) + r * (key_h + gap),
					key_w * 2 + gap,
					key_h,
				)
				pygame.draw.rect(
					screen, GRIS_SUAVE, key0, border_radius=int(4 * FACTOR_ESCALA)
				)
				pygame.draw.rect(
					screen,
					GRIS_SUAVE,
					key0,
					width=1,
					border_radius=int(4 * FACTOR_ESCALA),
				)
			elif c == 1 and r == 2:
				continue  # ya dibujado como parte del "0"
			else:
				pygame.draw.rect(
					screen, GRIS_SUAVE, key, border_radius=int(4 * FACTOR_ESCALA)
				)
				pygame.draw.rect(
					screen,
					(160, 165, 175),
					key,
					width=1,
					border_radius=int(4 * FACTOR_ESCALA),
				)


def main():
	pygame.init()
	screen = pygame.display.set_mode((ANCHO_PANTALLA, ALTO_PANTALLA))
	pygame.display.set_caption(
		"Cajero – Cola en carril inferior, atención en carril superior"
	)
	clock = pygame.time.Clock()
	font = pygame.font.SysFont(
		"Consolas, monospace", int(14 * FACTOR_ESCALA)
	) or pygame.font.Font(None, int(14 * FACTOR_ESCALA))
	font_big = pygame.font.SysFont(
		"Consolas, monospace", int(22 * FACTOR_ESCALA)
	) or pygame.font.Font(None, int(22 * FACTOR_ESCALA))

	# Hilos (daemon)
	threading.Thread(target=mux_buzones, daemon=True).start()
	threading.Thread(target=cajero, daemon=True).start()
	for i in range(NCLIENTES1):
		threading.Thread(target=cliente1, args=(i,), daemon=True).start()
	for i in range(NCLIENTES2):
		threading.Thread(target=cliente2, args=(NCLIENTES1 + i,), daemon=True).start()

	operaciones = []
	saldo_visto = SALDOINICIAL

	BANNER_TTL = 1.2
	banner_text = "Listo"
	banner_color = GRIS
	banner_until = 0.0

	atm_state = {
		"line1": "Listo",
		"line2": "Espere su turno...",
		"color": (80, 180, 220),
	}

	running = True
	while running:
		dt = clock.tick(FPS)
		now = time.time()

		for e in pygame.event.get():
			if e.type == pygame.QUIT:
				running = False
			elif e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
				running = False

		for _ in range(EVENTOS_POR_FRAME):
			if eventos_ui.empty():
				break
			tipo, data = eventos_ui.get()
			if tipo == "nueva_operacion":
				operaciones.append(
					OperacionSprite(
						data["op_id"], data["cliente"], data["tipo"], data["cantidad"]
					)
				)
			elif tipo == "atendiendo":
				op = next((o for o in operaciones if o.op_id == data["op_id"]), None)
				if op:
					op.on_atendiendo()
				if data["tipo"] == "extraccion":
					atm_state["line1"] = "Procesando extracción"
					atm_state["line2"] = f"Monto: ${data['cantidad']}"
					atm_state["color"] = AZUL
				else:
					atm_state["line1"] = "Procesando depósito"
					atm_state["line2"] = f"Monto: ${data['cantidad']}"
					atm_state["color"] = AMARILLO
			elif tipo == "resultado":
				op = next((o for o in operaciones if o.op_id == data["op_id"]), None)
				if op:
					op.on_resultado(data.get("ok", False))
					saldo_visto = data.get("saldo", saldo_visto)
					if now >= banner_until:
						if data.get("ok", False):
							banner_text = f"Extracción exitosa: ${data.get('monto',0)}"
							banner_color = VERDE
							atm_state["line1"] = "Retire su dinero"
							atm_state["color"] = VERDE
						else:
							banner_text = (
								f"Extracción rechazada: ${data.get('monto',0)}"
							)
							banner_color = ROJO
							atm_state["line1"] = "Operación rechazada"
							atm_state["color"] = ROJO
						atm_state["line2"] = f"Saldo: ${saldo_visto}"
						banner_until = now + BANNER_TTL
			elif tipo == "deposito_ok":
				op = next((o for o in operaciones if o.op_id == data["op_id"]), None)
				if op:
					op.on_deposito_ok()
					saldo_visto = data.get("saldo", saldo_visto)
					if now >= banner_until:
						banner_text = f"Depósito: ${data.get('monto',0)}"
						banner_color = VERDE
						banner_until = now + BANNER_TTL
					atm_state["line1"] = "Depósito acreditado"
					atm_state["line2"] = f"Nuevo saldo: ${saldo_visto}"
					atm_state["color"] = VERDE

		# Update con separación: cada sprite ve al anterior (en su orden de creación)
		anterior = None
		for op in operaciones:
			op.update(dt, anterior)
			if op.estado != "fin":
				anterior = op
		operaciones = [op for op in operaciones if op.estado != "fin"]

		# Dibujo
		dibujar_fondo(screen, font)

		# Panel (saldo + mensaje)
		panel = pygame.Rect(
			int(20 * FACTOR_ESCALA),
			int(20 * FACTOR_ESCALA),
			int(700 * FACTOR_ESCALA),
			int(140 * FACTOR_ESCALA),
		)
		pygame.draw.rect(
			screen, GRIS_PANEL, panel, border_radius=int(12 * FACTOR_ESCALA)
		)
		pygame.draw.rect(
			screen, GRIS, panel, width=2, border_radius=int(12 * FACTOR_ESCALA)
		)
		screen.blit(
			font_big.render(f"Saldo del cajero: ${saldo_visto}", True, NEGRO),
			(panel.x + int(14 * FACTOR_ESCALA), panel.y + int(12 * FACTOR_ESCALA)),
		)
		estado_y = panel.y + int(52 * FACTOR_ESCALA)
		pygame.draw.line(
			screen,
			(210, 215, 225),
			(panel.x + int(10 * FACTOR_ESCALA), estado_y - int(8 * FACTOR_ESCALA)),
			(
				panel.x + panel.width - int(10 * FACTOR_ESCALA),
				estado_y - int(8 * FACTOR_ESCALA),
			),
			width=1,
		)
		pygame.draw.circle(
			screen,
			(banner_color),
			(panel.x + int(16 * FACTOR_ESCALA), estado_y),
			int(6 * FACTOR_ESCALA),
		)
		estado_text = font_big.render(banner_text, True, NEGRO)
		screen.blit(
			estado_text,
			(panel.x + int(30 * FACTOR_ESCALA), estado_y - int(10 * FACTOR_ESCALA)),
		)
		screen.blit(
			font.render(f"En fila: {len(operaciones)}", True, NEGRO),
			(panel.x + int(14 * FACTOR_ESCALA), panel.y + int(98 * FACTOR_ESCALA)),
		)

		# ATM
		draw_atm(screen, font, font_big, atm_state)

		# Z-ORDER: cola (debajo) primero; atención (encima) después
		estados_debajo = ("entrando", "espera")
		estados_encima = ("atendiendo", "resultado", "saliendo")
		for op in operaciones:
			if op.estado in estados_debajo:
				op.draw(screen, font)
				break
		for op in operaciones:
			if op.estado in estados_encima:
				op.draw(screen, font)

		pygame.display.flip()

	pygame.quit()


if __name__ == "__main__":
	main()
