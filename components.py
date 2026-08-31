from nicegui import app, ui


def menu_drawer():
    with ui.left_drawer().classes("bg-purple-900 text-white p-4") as drawer:
        ui.label("Menu Principal").classes("text-xl font-bold mb-4")
        ui.button(
            "Meus Boletos",
            on_click=lambda: ui.navigate.to("/"),
            icon="receipt",
        ).props("flat color=white").classes("w-full text-left")
        ui.button(
            "Meu Perfil",
            on_click=lambda: ui.navigate.to("/perfil"),
            icon="person",
        ).props("flat color=white").classes("w-full text-left")
        ui.button(
            "Sair",
            on_click=lambda: (app.storage.user.clear(), ui.navigate.to("/login")),
            icon="logout",
        ).props("flat color=white").classes("w-full text-left mt-auto")
    return drawer


def cabecalho_app(drawer):
    with ui.header().classes(
        "bg-purple-800 text-white justify-between items-center px-4"
    ):
        ui.button(on_click=drawer.toggle, icon="menu").props(
            "flat color=white"
        )
        ui.label("Gestão de Boletos").classes("text-lg font-bold")
        ui.label("").classes("w-8")