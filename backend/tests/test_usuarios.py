"""Tests de los usuarios propios de la app y de los permisos por rol."""
import pytest

from app import permisos, usuarios


# ── Contraseñas ─────────────────────────────────────────────────────────────
def test_hash_no_guarda_la_contrasena_en_claro():
    h = usuarios.hashear("secreta-123")
    assert "secreta-123" not in h
    assert h.startswith("pbkdf2_sha256$")


def test_verificar_hash():
    h = usuarios.hashear("secreta-123")
    assert usuarios.verificar_hash("secreta-123", h)
    assert not usuarios.verificar_hash("otra", h)


def test_dos_hashes_de_la_misma_clave_son_distintos():
    """Salt aleatorio: dos personas con la misma contraseña no comparten hash."""
    assert usuarios.hashear("igual") != usuarios.hashear("igual")


@pytest.mark.parametrize("guardado", ["", "cualquier-cosa", "md5$1$aa$bb", None])
def test_verificar_hash_tolera_basura(guardado):
    assert not usuarios.verificar_hash("x", guardado)


# ── Alta ────────────────────────────────────────────────────────────────────
def test_crear_devuelve_la_clave_una_vez_y_no_la_guarda(store):
    u, clave = usuarios.crear(usuario="prensa", nombre="Ana", rol="subcomision",
                              subcomision="Prensa")
    assert clave and len(clave) >= 8
    assert "password" not in u                       # nunca sale por la API
    assert store["usuarios"][0]["password"] != clave  # en la base va hasheada


def test_crear_con_clave_elegida(store):
    usuarios.crear(usuario="tesoreria", nombre="Bruno", rol="comision", password="mi-clave-larga")
    assert usuarios.verificar("tesoreria", "mi-clave-larga")


def test_no_se_repite_el_usuario(store):
    usuarios.crear(usuario="prensa", nombre="Ana", rol="subcomision", subcomision="Prensa")
    with pytest.raises(usuarios.ErrorUsuario, match="Ya existe"):
        usuarios.crear(usuario="PRENSA", nombre="Otra", rol="subcomision", subcomision="Prensa")


def test_no_se_repite_el_email(store):
    usuarios.crear(usuario="a", nombre="Ana", rol="comision", email="ana@bib.org")
    with pytest.raises(usuarios.ErrorUsuario, match="Ya existe"):
        usuarios.crear(usuario="b", nombre="Beto", rol="comision", email="ANA@bib.org")


def test_rol_invalido(store):
    with pytest.raises(usuarios.ErrorUsuario, match="Rol desconocido"):
        usuarios.crear(usuario="x", nombre="X", rol="presidenta")


def test_subcomision_sin_nombre_no_va(store):
    with pytest.raises(usuarios.ErrorUsuario, match="subcomisión"):
        usuarios.crear(usuario="x", nombre="X", rol="subcomision")


@pytest.mark.parametrize("campo", ["usuario", "nombre"])
def test_faltan_datos_obligatorios(store, campo):
    datos = {"usuario": "x", "nombre": "X", "rol": "comision"}
    datos[campo] = "   "
    with pytest.raises(usuarios.ErrorUsuario):
        usuarios.crear(**datos)


# ── Login ───────────────────────────────────────────────────────────────────
def test_login_por_usuario_o_por_email(store):
    usuarios.crear(usuario="cultura", nombre="Ana", rol="subcomision",
                   subcomision="Cultura", email="ana@bib.org", password="clave-larga-1")
    assert usuarios.verificar("cultura", "clave-larga-1")
    assert usuarios.verificar("CULTURA", "clave-larga-1")     # no distingue mayúsculas
    assert usuarios.verificar("ana@bib.org", "clave-larga-1")


def test_login_con_clave_mala(store):
    usuarios.crear(usuario="cultura", nombre="Ana", rol="subcomision",
                   subcomision="Cultura", password="clave-larga-1")
    assert usuarios.verificar("cultura", "otra") is None


def test_usuario_inexistente(store):
    assert usuarios.verificar("fantasma", "x") is None


def test_usuario_desactivado_no_entra(store):
    u, clave = usuarios.crear(usuario="cultura", nombre="Ana", rol="subcomision",
                              subcomision="Cultura")
    usuarios.actualizar(u["id"], {"activo": False})
    assert usuarios.verificar("cultura", clave) is None


# ── Edición ─────────────────────────────────────────────────────────────────
def test_actualizar_solo_toca_lo_editable(store):
    u, clave = usuarios.crear(usuario="cultura", nombre="Ana", rol="subcomision",
                              subcomision="Cultura")
    usuarios.actualizar(u["id"], {"nombre": "Ana Paz", "rol": "comision",
                                  "usuario": "hackeado", "password": "pisada"})
    guardado = store["usuarios"][0]
    assert guardado["nombre"] == "Ana Paz"
    assert guardado["rol"] == "comision"
    assert guardado["usuario"] == "cultura"          # no se puede cambiar el login
    assert usuarios.verificar_hash(clave, guardado["password"])  # ni la contraseña


def test_actualizar_usuario_inexistente(store):
    with pytest.raises(usuarios.ErrorUsuario, match="no existe"):
        usuarios.actualizar("nada", {"nombre": "X"})


def test_resetear_password(store):
    u, vieja = usuarios.crear(usuario="cultura", nombre="Ana", rol="comision")
    nueva = usuarios.resetear_password(u["id"])
    assert nueva != vieja
    assert usuarios.verificar("cultura", nueva)
    assert usuarios.verificar("cultura", vieja) is None


def test_cambiar_password_propia(store):
    u, vieja = usuarios.crear(usuario="cultura", nombre="Ana", rol="comision")
    usuarios.cambiar_password(u["id"], vieja, "nueva-clave-larga")
    assert usuarios.verificar("cultura", "nueva-clave-larga")


def test_cambiar_password_exige_la_actual(store):
    u, _ = usuarios.crear(usuario="cultura", nombre="Ana", rol="comision")
    with pytest.raises(usuarios.ErrorUsuario, match="no es correcta"):
        usuarios.cambiar_password(u["id"], "equivocada", "nueva-clave-larga")


def test_cambiar_password_exige_largo_minimo(store):
    u, vieja = usuarios.crear(usuario="cultura", nombre="Ana", rol="comision")
    with pytest.raises(usuarios.ErrorUsuario, match="8 caracteres"):
        usuarios.cambiar_password(u["id"], vieja, "corta")


def test_borrar(store):
    u, _ = usuarios.crear(usuario="cultura", nombre="Ana", rol="comision")
    usuarios.borrar(u["id"])
    assert usuarios.listar() == []
    with pytest.raises(usuarios.ErrorUsuario, match="no existe"):
        usuarios.borrar(u["id"])


# ── Listados ────────────────────────────────────────────────────────────────
def test_listar_nunca_expone_el_hash(store):
    usuarios.crear(usuario="cultura", nombre="Ana", rol="comision")
    assert all("password" not in u for u in usuarios.listar())


def test_listar_sin_inactivos(store):
    u, _ = usuarios.crear(usuario="cultura", nombre="Ana", rol="comision")
    usuarios.crear(usuario="prensa", nombre="Beto", rol="subcomision", subcomision="Prensa")
    usuarios.actualizar(u["id"], {"activo": False})
    assert [x["usuario"] for x in usuarios.listar(incluir_inactivos=False)] == ["prensa"]


def test_subcomisiones_junta_env_y_uso(store, monkeypatch):
    monkeypatch.setenv("SUBCOMISIONES", "Cultura, Huerta")
    usuarios.crear(usuario="prensa", nombre="Ana", rol="subcomision", subcomision="Prensa")
    assert usuarios.subcomisiones() == ["Cultura", "Huerta", "Prensa"]


def test_registrar_acceso(store):
    u, _ = usuarios.crear(usuario="cultura", nombre="Ana", rol="comision")
    assert store["usuarios"][0]["ultimo_acceso"] is None
    usuarios.registrar_acceso(u["id"])
    assert store["usuarios"][0]["ultimo_acceso"] is not None


# ── Permisos ────────────────────────────────────────────────────────────────
def test_bibliotecaria_y_comision_pueden_lo_mismo_salvo_el_pizarron():
    """Decisiones tomadas: la comisión directiva ve lo mismo que las bibliotecarias,
    con una sola excepción: el pizarrón semanal, que es del equipo de la biblioteca."""
    diferencia = permisos.permisos_de("bibliotecaria") ^ permisos.permisos_de("comision")
    assert diferencia == {permisos.PIZARRON}


@pytest.mark.parametrize("permiso", [permisos.KOHA, permisos.MAILS,
                                     permisos.INVENTARIO_EDITAR, permisos.USUARIOS_ADMIN,
                                     permisos.SOLICITUDES_RESOLVER])
def test_subcomision_no_puede_lo_reservado(permiso):
    assert not permisos.puede("subcomision", permiso)


@pytest.mark.parametrize("permiso", [permisos.CALENDARIO_VER, permisos.SOLICITUDES_CREAR,
                                     permisos.INVENTARIO_VER, permisos.TALLERES_VER])
def test_subcomision_si_puede_lo_institucional(permiso):
    assert permisos.puede("subcomision", permiso)


def test_rol_desconocido_no_puede_nada():
    """Falla cerrado: un rol que no existe no habilita nada."""
    assert permisos.permisos_de("intruso") == set()
    assert permisos.secciones_de("intruso") == []


def test_secciones_por_rol():
    ids = lambda rol: [s["id"] for s in permisos.secciones_de(rol)]
    assert ids("subcomision") == ["actualidad", "agenda", "solicitudes", "mias"]
    assert "usuarios" in ids("bibliotecaria")
    assert "mails" in ids("comision")
    assert "cuotas" not in ids("subcomision")


def test_exigir_corta_con_403():
    from fastapi import HTTPException
    permisos.exigir("bibliotecaria", permisos.KOHA)          # no levanta nada
    with pytest.raises(HTTPException) as e:
        permisos.exigir("subcomision", permisos.KOHA)
    assert e.value.status_code == 403


# ── Menú con grupos ─────────────────────────────────────────────────────────
def test_menu_agrupa_envios():
    """Mails, Automáticos e Historial van juntos bajo un solo botón."""
    menu = permisos.menu_de("bibliotecaria")
    envios = next(e for e in menu if e.get("titulo") == "Envíos")
    assert envios["tipo"] == "grupo"
    assert [i["id"] for i in envios["items"]] == ["mails", "auto", "envios"]


def test_menu_deja_sueltas_las_que_no_tienen_grupo():
    menu = permisos.menu_de("bibliotecaria")
    sueltas = [e["id"] for e in menu if e["tipo"] == "seccion"]
    assert sueltas == ["stats", "pizarron", "actualidad", "loans", "cuotas", "usuarios"]


def test_menu_agrupa_socios_y_notas():
    menu = permisos.menu_de("comision")
    socios = next(e for e in menu if e.get("titulo") == "Socios")
    assert [i["id"] for i in socios["items"]] == ["members", "notas"]


def test_menu_no_pierde_ninguna_seccion():
    """Lo que está en el menú es exactamente lo que el rol puede ver, ni más ni menos."""
    for rol in permisos.ROLES:
        en_menu = []
        for e in permisos.menu_de(rol):
            en_menu.extend([i["id"] for i in e["items"]] if e["tipo"] == "grupo" else [e["id"]])
        assert en_menu == [s["id"] for s in permisos.secciones_de(rol)], f"rol {rol}"


def test_menu_de_subcomision_no_tiene_grupos_vacios():
    """Si el rol no ve nada de un grupo, el grupo no aparece."""
    menu = permisos.menu_de("subcomision")
    assert [e["titulo"] for e in menu] == ["Lo que anda pasando", "Agenda"]   # ni Envíos, ni Reportes, ni Usuarios
    assert [i["id"] for i in menu[1]["items"]] == ["agenda", "solicitudes", "mias"]


def test_menu_de_rol_desconocido_es_vacio():
    assert permisos.menu_de("intruso") == []
