import asyncio
import logging
import os
import re
import smtplib
import threading
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from nicegui import app, ui
import requests
from supabase import Client, create_client

# Silencia logs de aviso
logging.getLogger("asyncio").setLevel(logging.CRITICAL)

# Carrega configurações externadas
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL")
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASS = os.getenv("GMAIL_APP_PASS")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# --- FUNÇÕES AUXILIARES ---
def obter_hora_brasilia():
    return datetime.now(ZoneInfo("America/Sao_Paulo"))


def email_valido(email_str: str) -> bool:
    return bool(re.match(r"^[\w\.-]+@[\w\.-]+\.\w+$", email_str.strip()))


def formatar_br(valor) -> str:
    try:
        val = float(valor or 0)
        return (
            f"{val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        )
    except (ValueError, TypeError):
        return "0,00"


# --- NOTIFICAÇÕES (TELEGRAM & E-MAIL) ---
def enviar_notificacao_telegram(
    email_solicitante: str, dispositivo: str, localizacao: str
):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    mensagem = (
        f"🚨 <b>SOLICITAÇÃO DE ACESSO - AGENDAMENTOS</b>\n\n"
        f"📧 <b>E-mail:</b> {email_solicitante}\n"
        f"📱 <b>Dispositivo:</b> {dispositivo[:60]}\n"
        f"📍 <b>Localização:</b> {localizacao}"
    )
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": mensagem,
                "parse_mode": "HTML",
            },
            timeout=5,
        )
    except Exception as e:
        print(f"Erro Telegram: {e}")


def _enviar_email_worker(solicitante_email, dispositivo, localizacao):
    try:
        data_hora_br = obter_hora_brasilia().strftime("%d/%m/%Y às %H:%M:%S")
        msg = MIMEMultipart("alternative")
        msg["Subject"] = (
            f"Agendamentos Pessoais - Solicitação: {solicitante_email}"
        )
        msg["From"] = f"Agendamentos Pessoais <{GMAIL_USER}>"
        msg["To"] = ADMIN_EMAIL

        corpo_html = f"""
        <html>
          <body style="font-family: Arial, sans-serif; padding: 20px;">
            <h2>🔔 Nova Solicitação de Acesso</h2>
            <ul>
              <li><b>E-mail:</b> {solicitante_email}</li>
              <li><b>Data/Hora:</b> {data_hora_br}</li>
              <li><b>Localização:</b> {localizacao}</li>
              <li><b>Dispositivo:</b> {dispositivo}</li>
            </ul>
          </body>
        </html>
        """
        msg.attach(MIMEText(corpo_html, "html"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_USER, GMAIL_APP_PASS)
            server.sendmail(GMAIL_USER, ADMIN_EMAIL, msg.as_string())
    except Exception as e:
        print(f"Erro E-mail: {e}")


def enviar_notificacao_email(solicitante_email, dispositivo, localizacao):
    threading.Thread(
        target=_enviar_email_worker,
        args=(solicitante_email, dispositivo, localizacao),
        daemon=True,
    ).start()


# --- LAYOUT BASE & NAVEGAÇÃO ---
def menu_drawer():
    user_email = app.storage.user.get("email", "")

    with ui.left_drawer(value=False).classes(
        "bg-slate-50 text-slate-800 p-0 flex flex-col justify-between w-64 border-r shadow-lg"
    ) as drawer:

        def navegar(rota):
            drawer.hide()
            ui.navigate.to(rota)

        with ui.column().classes("w-full p-5 border-b bg-white gap-1"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("event_note", size="28px").classes(
                    "text-blue-700 font-bold"
                )
                ui.label("Agendamentos").classes("text-xl font-black")
            ui.label(user_email if user_email else "Minha Conta").classes(
                "text-xs text-slate-500 truncate"
            )

        with ui.column().classes("w-full p-4 gap-2 flex-1"):
            with ui.button(on_click=lambda: navegar("/")).props(
                "flat no-caps align=left"
            ).classes("w-full hover:bg-slate-200 rounded-lg py-2 px-3"):
                ui.label("📅 Meus Boletos e Alertas").classes(
                    "font-bold text-sm"
                )

            # MENU EXCLUSIVO DO ADMIN
            if app.storage.user.get("is_admin", False) or user_email == ADMIN_EMAIL:
                ui.separator().classes("my-2")
                ui.label("ADMINISTRAÇÃO").classes(
                    "text-[10px] font-bold text-amber-600 px-3"
                )
                with ui.button(on_click=lambda: navegar("/admin")).props(
                    "flat no-caps align=left"
                ).classes("w-full hover:bg-amber-100/50 rounded-lg py-2 px-3"):
                    ui.label("⚙️ Painel de Manutenção").classes(
                        "font-bold text-sm text-amber-950"
                    )

        with ui.column().classes("w-full p-4 border-t bg-white gap-2"):
            with ui.button(
                on_click=lambda: (
                    drawer.hide(),
                    app.storage.user.clear(),
                    ui.navigate.to("/login"),
                )
            ).props("flat no-caps align=left").classes(
                "w-full hover:bg-red-50 rounded-lg py-2 px-3"
            ):
                ui.label("Sair da Conta").classes(
                    "font-bold text-sm text-red-600"
                )

    return drawer


def cabecalho_app(drawer):
    user_email = app.storage.user.get("email", "Usuário")
    with ui.header().classes(
        "bg-blue-900 text-white justify-between items-center p-3 w-full"
    ):
        ui.button(icon="menu", on_click=drawer.toggle).props("flat color=white")
        ui.label("Agendamentos Pessoais").classes("text-lg font-bold")
        ui.label(user_email.split("@")[0]).classes(
            "text-xs bg-blue-700 px-2 py-1 rounded"
        )


# --- TELA DE LOGIN & SOLICITAÇÃO ---
@ui.page("/login")
def login_page():
    def abrir_modal_solicitacao():
        with ui.dialog() as dialog, ui.card().classes("w-full max-w-sm p-4"):
            ui.label("Solicitar Acesso").classes(
                "text-xl font-bold text-gray-800 mb-2"
            )
            solicita_email = (
                ui.input("E-mail").props("outlined").classes("w-full mb-2")
            )
            solicita_senha = (
                ui.input(
                    "Senha desejada", password=True, password_toggle_button=True
                )
                .props("outlined")
                .classes("w-full mb-4")
            )

            async def processar_solicitacao():
                email_txt = (solicita_email.value or "").strip().lower()
                senha_txt = (solicita_senha.value or "").strip()

                if not email_valido(email_txt) or not senha_txt:
                    ui.notify(
                        "Preencha os campos corretamente!", color="warning"
                    )
                    return

                user_agent = str(
                    ui.context.client.environ.get(
                        "HTTP_USER_AGENT", "Dispositivo Móvel"
                    )
                )[:150]
                loc_text = "Não informada"

                try:
                    ip_cliente = ui.context.client.environ.get(
                        "REMOTE_ADDR", ""
                    )
                    ip_data = requests.get(
                        f"https://ipapi.co/{ip_cliente}/json/", timeout=2
                    ).json()
                    loc_text = (
                        f"{ip_data.get('city')}, {ip_data.get('region')}"
                    )
                except Exception:
                    pass

                dialog.close()

                # Salva solicitação no Supabase
                try:
                    supabase.table("solicitacoes_acesso").insert({
                        "created_at": obter_hora_brasilia().isoformat(),
                        "email": email_txt,
                        "senha_temporaria": senha_txt,
                        "dispositivo": user_agent,
                        "localizacao": loc_text,
                    }).execute()
                except Exception as e:
                    print(f"Erro Supabase: {e}")

                # Dispara Notificações
                asyncio.create_task(
                    asyncio.to_thread(
                        enviar_notificacao_email,
                        email_txt,
                        user_agent,
                        loc_text,
                    )
                )
                asyncio.create_task(
                    asyncio.to_thread(
                        enviar_notificacao_telegram,
                        email_txt,
                        user_agent,
                        loc_text,
                    )
                )

                ui.notify(
                    "Solicitação enviada com sucesso ao Administrador!",
                    color="positive",
                )

            ui.button("ENVIAR SOLICITAÇÃO", on_click=processar_solicitacao).classes(
                "w-full bg-blue-600 text-white font-bold mb-2"
            )
            ui.button("CANCELAR", on_click=dialog.close).props("flat").classes(
                "w-full text-gray-600"
            )

        dialog.open()

    with ui.card().classes(
        "w-11/12 max-w-sm absolute-center p-6 shadow-xl rounded-xl"
    ):
        ui.label("Agendamentos Pessoais").classes(
            "text-2xl font-bold text-blue-800 text-center w-full mb-4"
        )
        email = ui.input("E-mail").props("outlined").classes("w-full mb-2")
        password = (
            ui.input("Senha", password=True, password_toggle_button=True)
            .props("outlined")
            .classes("w-full mb-4")
        )

        def try_login():
            email_val = email.value.strip().lower() if email.value else ""
            pwd_val = password.value.strip() if password.value else ""

            res = (
                supabase.table("perfis_usuarios")
                .select("*")
                .eq("email", email_val)
                .execute()
            )
            users = res.data or []

            if users and users[0].get("senha") == pwd_val:
                if not users[0].get("ativo", True):
                    ui.notify(
                        "Usuário inativo! Fale com o administrador.",
                        color="negative",
                    )
                    return
                app.storage.user["user_id"] = users[0]["id"]
                app.storage.user["email"] = users[0]["email"]
                app.storage.user["is_admin"] = users[0].get("is_admin", False) # <--- ADICIONE ESTA LINHA
                ui.navigate.to("/")
            else:
                ui.notify("E-mail ou senha incorretos!", color="negative")

        ui.button("ENTRAR", on_click=try_login).classes(
            "w-full bg-blue-600 text-white font-bold mb-3"
        )
        ui.separator().classes("my-2")
        ui.button(
            "SOLICITAR ACESSO", on_click=abrir_modal_solicitacao
        ).props("flat dense").classes(
            "w-full text-blue-500 font-medium text-xs mt-2"
        )


# --- DASHBOARD DO USUÁRIO REESTRUTURADO ---
import io
import re
from datetime import datetime, timedelta
from nicegui import app, ui
from pypdf import PdfReader

# ==========================================
# 1. FUNÇÕES AUXILIARES E EXTRAÇÃO DE BOLETO
# ==========================================

def formatar_data_br(data_str):
    if not data_str:
        return ""
    try:
        partes = str(data_str).split("-")
        if len(partes) == 3:
            return f"{partes[2]}/{partes[1]}/{partes[0]}"
    except Exception:
        pass
    return data_str


def formatar_br(valor):
    try:
        return f"{float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "0,00"


def decodificar_boleto(codigo: str):
    codigo_limpo = re.sub(r"\D", "", codigo)
    valor = None
    vencimento = None

    fator_venc = None
    valor_centavos = None

    if len(codigo_limpo) == 47:
        fator_venc = int(codigo_limpo[33:37])
        valor_centavos = int(codigo_limpo[37:47])
    elif len(codigo_limpo) == 44:
        fator_venc = int(codigo_limpo[5:9])
        valor_centavos = int(codigo_limpo[9:19])

    if fator_venc and fator_venc > 0:
        if fator_venc >= 1000:
            data_base = datetime(2025, 2, 22) - timedelta(days=1000)
        else:
            data_base = datetime(1997, 10, 7)

        vencimento = (data_base + timedelta(days=fator_venc)).strftime("%Y-%m-%d")

    if valor_centavos and valor_centavos > 0:
        valor = valor_centavos / 100.0

    return valor, vencimento, codigo_limpo


def extrair_dados_pdf_boleto(stream_pdf):
    reader = PdfReader(stream_pdf)
    texto_completo = ""
    for page in reader.pages:
        texto_completo += (page.extract_text() or "") + "\n"

    empresa = None
    valor = None
    vencimento = None

    texto_limpo = re.sub(r"[ \t]+", " ", texto_completo)

    if re.search(r"BOTICÁRIO|BOTICARIO", texto_completo, re.IGNORECASE):
        if re.search(r"PRODUTOS DE BELEZA", texto_completo, re.IGNORECASE):
            empresa = "BOTICÁRIO PRODUTOS DE BELEZA LTDA"
        else:
            empresa = "O BOTICÁRIO"

    if not empresa:
        match_sufixo = re.search(
            r"([A-Z0-9\.\,\-\s\&]{4,50}\s(?:LTDA|S\.?A\.?|ME|EPP|EIRELI))\b",
            texto_limpo,
            re.IGNORECASE,
        )
        if match_sufixo:
            cand = match_sufixo.group(1).strip()
            cand = re.sub(
                r".*?\b(?:CNPJ|CPF|Beneficiário|Cedente)[:\s\d\.\/\-\(\)]*",
                "",
                cand,
                flags=re.IGNORECASE,
            ).strip()
            cand = re.sub(r"^\)+", "", cand).strip()
            if len(cand) > 3 and not re.match(r"^\d+/\d+$", cand):
                empresa = cand

    if not empresa:
        linhas = [l.strip() for l in texto_completo.split("\n") if l.strip()]
        for i, linha in enumerate(linhas):
            if re.search(r"Beneficiário|Cedente", linha, re.IGNORECASE):
                for offset in range(1, 5):
                    if i + offset < len(linhas):
                        candidato = linhas[i + offset]
                        candidato_limpo = re.sub(
                            r"\(?CNPJ[^\)]+\)?", "", candidato, flags=re.IGNORECASE
                        ).strip()
                        candidato_limpo = re.sub(
                            r"^\d{3,}-\d.*$", "", candidato_limpo
                        ).strip()

                        if (
                            candidato_limpo
                            and len(candidato_limpo) > 3
                            and not re.search(
                                r"^\d+/\d+$|Pagador|Vencimento",
                                candidato_limpo,
                                re.IGNORECASE,
                            )
                        ):
                            empresa = candidato_limpo
                            break
                if empresa:
                    break

    codigos_encontrados = re.findall(
        r"\b\d{5}\.\d{5}\s\d{5}\.\d{6}\s\d{5}\.\d{6}\s\d\s\d{14}\b|\b\d{47}\b|\b\d{44}\b",
        texto_completo,
    )

    if codigos_encontrados:
        codigo_limpo = re.sub(r"\D", "", codigos_encontrados[0])
        val, venc, _ = decodificar_boleto(codigo_limpo)
        valor = val
        vencimento = venc

    if not valor:
        match_valor = re.search(
            r"Valor do Documento[:\s]+(?:R\$\s*)?([\d\.,]+)",
            texto_completo,
            re.IGNORECASE,
        )
        if match_valor:
            try:
                v_str = (
                    match_valor.group(1).replace(".", "").replace(",", ".")
                )
                valor = float(v_str)
            except ValueError:
                pass

    if not vencimento:
        match_venc = re.search(
            r"Vencimento[:\s]+(\d{2}/\d{2}/\d{4})",
            texto_completo,
            re.IGNORECASE,
        )
        if match_venc:
            try:
                d, m, a = match_venc.group(1).split("/")
                vencimento = f"{a}-{m}-{d}"
            except Exception:
                pass

    return empresa, valor, vencimento


import io
from datetime import datetime, date
from nicegui import app, ui

# Assumindo que decodificar_boleto, extrair_dados_pdf_boleto, menu_drawer,
# cabecalho_app e supabase já estejam importados/definidos globalmente no seu projeto.


import io
from datetime import datetime, date
from nicegui import app, ui

# Assumindo que decodificar_boleto, extrair_dados_pdf_boleto, menu_drawer,
# cabecalho_app e supabase já estejam importados/definidos globalmente no seu projeto.


# ==========================================
# 2. PÁGINA PRINCIPAL DE BOLETOS
# ==========================================

@ui.page("/")
def home_page():
    if not app.storage.user.get("user_id"):
        ui.navigate.to("/login")
        return

    # Injeção segura no HEAD (Bibliotecas e Estilos)
    ui.add_head_html('''
        <script src="https://unpkg.com/@zxing/library@latest"></script>
        <style>
            .scanner-modal-container {
                position: fixed;
                top: 0; left: 0; right: 0; bottom: 0;
                width: 100vw; height: 100vh;
                background-color: #000;
                z-index: 9999;
                display: flex;
                flex-direction: column;
                justify-content: center;
                align-items: center;
            }
            .scanner-video-preview {
                width: 100%; height: 100%;
                object-fit: cover;
            }
            .scanner-overlay {
                position: absolute;
                top: 50%; left: 50%;
                transform: translate(-50%, -50%);
                width: 85%; max-width: 400px; height: 160px;
                border: 3px dashed #38bdf8;
                border-radius: 12px;
                box-shadow: 0 0 0 9999px rgba(0, 0, 0, 0.65);
                pointer-events: none;
            }
            .scanner-line {
                width: 100%; height: 3px;
                background: #ef4444;
                position: absolute; top: 10%;
                animation: scan-anim 2s infinite linear;
            }
            @keyframes scan-anim {
                0% { top: 10%; }
                50% { top: 90%; }
                100% { top: 10%; }
            }
        </style>
    ''')

    # Injeção segura no BODY (Modal e Script de fechamento)
    ui.add_body_html('''
        <div id="camera-box" class="scanner-modal-container" style="display: none;">
            <video id="webcam-preview" class="scanner-video-preview"></video>
            <div class="scanner-overlay">
                <div class="scanner-line"></div>
            </div>
            
            <div id="cam-status" style="position: absolute; top: 20px; left: 50%; transform: translateX(-50%); color: #fff; background: rgba(0,0,0,0.8); padding: 8px 16px; border-radius: 20px; font-size: 16px; font-weight: bold; text-align: center; width: 90%; max-width: 350px;">
                Aponte para o código de barras
            </div>

            <button onclick="fecharCamera()" style="position: absolute; bottom: 30px; left: 50%; transform: translateX(-50%); background: #ef4444; color: white; border: none; padding: 14px 28px; border-radius: 50px; font-size: 18px; font-weight: bold; box-shadow: 0 4px 10px rgba(0,0,0,0.3); cursor: pointer;">
                ✖ Fechar Câmera
            </button>
        </div>

        <script>
            function fecharCamera() {
                if (window.codeReader) window.codeReader.reset();
                const box = document.getElementById("camera-box");
                if (box) box.style.display = "none";
            }
        </script>
    ''')

    drawer = menu_drawer()
    cabecalho_app(drawer)
    user_id = app.storage.user.get("user_id")

    res_perfil = supabase.table("perfis_usuarios").select("*").eq("id", user_id).execute()
    perfil_usr = res_perfil.data[0] if res_perfil.data else {}
    email_cadastrado = perfil_usr.get("email_notificacao") or perfil_usr.get("email", "")
    whatsapp_cadastrado = perfil_usr.get("whatsapp", "")

    cats_res = supabase.table("dim_categorias").select("*").execute()
    categorias_list = {c["id"]: c["nome"] for c in (cats_res.data or [])}

    with ui.column().classes("w-full max-w-4xl mx-auto p-3 sm:p-6 gap-6 font-sans pb-32"):
        
        # Cabeçalho da seção
        with ui.card().classes("w-full p-4 bg-purple-50 border border-purple-200 rounded-xl shadow-sm"):
            with ui.column().classes("w-full sm:flex-row justify-between items-center gap-3"):
                with ui.row().classes("items-center gap-2"):
                    ui.icon("receipt_long", size="32px", color="purple-9")
                    ui.label("Meus Boletos").classes("text-2xl sm:text-3xl font-extrabold text-slate-800")
                
                with ui.row().classes("items-center gap-2 bg-white p-2 px-3 rounded-lg border border-purple-200 w-full sm:w-auto justify-between"):
                    with ui.row().classes("items-center gap-1"):
                        ui.icon("notifications", color="purple").classes("text-base")
                        ui.label(f"Contato: {whatsapp_cadastrado or email_cadastrado or 'Não configurado'}").classes("text-sm text-purple-900 font-semibold")
                    ui.button("Alterar", on_click=lambda: ui.navigate.to("/perfil")).props("flat dense size=md color=purple")

        # Form de Cadastro
        with ui.card().classes("w-full p-4 sm:p-6 border border-slate-200 bg-white shadow-md rounded-2xl gap-4"):
            ui.label("➕ Cadastrar Novo Boleto").classes("text-xl sm:text-2xl font-bold text-slate-800 border-b pb-2 w-full")

            modo = (
                ui.radio(
                    {
                        "manual": "Entrada Manual",
                        "camera": "Importar PDF / Escanear Câmera",
                    },
                    value="manual",
                )
                .props("inline size=lg")
                .classes("text-base font-semibold text-slate-700 my-1")
            )

            with ui.column().classes("w-full gap-4"):
                input_empresa = (
                    ui.input("Empresa / Nome do Boleto", placeholder="Ex: Conta de Luz, Internet...")
                    .props("outlined size=lg bg-slate-50")
                    .classes("w-full text-lg")
                )
                
                with ui.grid().classes("w-full grid-cols-1 sm:grid-cols-2 gap-4"):
                    input_valor = (
                        ui.number("Valor (R$)", format="%.2f", placeholder="0,00")
                        .props("outlined size=lg bg-slate-50 input-class=text-lg")
                        .classes("w-full")
                    )
                    input_vencimento = (
                        ui.input("Data de Vencimento")
                        .props("type=date outlined size=lg bg-slate-50")
                        .classes("w-full")
                    )

                with ui.grid().classes("w-full grid-cols-1 sm:grid-cols-2 gap-4"):
                    select_categoria = (
                        ui.select(categorias_list, label="Categoria")
                        .props("outlined size=lg bg-slate-50")
                        .classes("w-full")
                    )
                    select_status = (
                        ui.select(
                            ["PENDENTE", "PAGO", "ATRASADO", "CANCELADO"],
                            value="PENDENTE",
                            label="Status Inicial",
                        )
                        .props("outlined size=lg bg-slate-50")
                        .classes("w-full")
                    )

            check_lembrete = ui.checkbox(
                "🔔 Desejo receber um lembrete antes do vencimento"
            ).classes("mt-2 text-base sm:text-lg text-slate-800 font-bold")

            container_lembrete = ui.column().classes(
                "w-full p-4 bg-purple-50 border-2 border-purple-200 rounded-xl gap-4"
            )
            container_lembrete.bind_visibility_from(check_lembrete, "value")

            with container_lembrete:
                ui.label("Configuração do Lembrete").classes("text-sm font-bold text-purple-800 uppercase tracking-wide")
                with ui.grid().classes("w-full grid-cols-1 sm:grid-cols-3 gap-3"):
                    select_canal = (
                        ui.select(
                            ["SMS", "WhatsApp", "E-mail", "Todos"],
                            value="WhatsApp",
                            label="Canal",
                        )
                        .props("outlined bg-white size=lg")
                        .classes("w-full")
                    )
                    select_antecedencia = (
                        ui.select(
                            {
                                0: "No dia do vencimento",
                                1: "1 dia antes",
                                2: "2 dias antes",
                                3: "3 dias antes",
                                5: "5 dias antes",
                            },
                            value=1,
                            label="Aviso",
                        )
                        .props("outlined bg-white size=lg")
                        .classes("w-full")
                    )
                    input_horario = (
                        ui.input("Horário", value="09:00")
                        .props("type=time outlined bg-white size=lg")
                        .classes("w-full")
                    )

            def aplicar_dados_boleto(codigo_raw):
                val, venc, limpo = decodificar_boleto(codigo_raw)
                if val is not None:
                    input_valor.value = val
                if venc is not None:
                    input_vencimento.value = venc

                if val or venc:
                    ui.notify("Dados extraídos com sucesso!", color="positive", size="lg")
                else:
                    ui.notify(
                        f"Código lido ({limpo}), verifique os valores.",
                        color="warning", size="lg"
                    )

            def processar_codigo_escaneado(e):
                codigo = e.args.get("codigo", "") if isinstance(e.args, dict) else ""
                aplicar_dados_boleto(codigo)

            ui.on("boleto_escaneado", processar_codigo_escaneado)

            container_camera = ui.column().classes(
                "w-full p-4 bg-slate-100 border border-slate-300 rounded-xl gap-4"
            )
            with container_camera:
                ui.label("📄 Opção 1: Anexar PDF do Boleto").classes("font-bold text-slate-800 text-base")

                async def handle_upload(e):
                    try:
                        content_obj = getattr(e, "content", None) or getattr(e, "file", None)
                        if hasattr(content_obj, "read"):
                            res = content_obj.read()
                            file_bytes = await res if hasattr(res, "__await__") else res
                        else:
                            file_bytes = content_obj

                        pdf_stream = io.BytesIO(file_bytes)
                        empresa, valor, vencimento = extrair_dados_pdf_boleto(pdf_stream)

                        preencheu = False
                        if empresa:
                            input_empresa.value = empresa
                            preencheu = True
                        if valor:
                            input_valor.value = valor
                            preencheu = True
                        if vencimento:
                            input_vencimento.value = vencimento
                            preencheu = True

                        if preencheu:
                            ui.notify("Dados lidos do PDF!", color="positive", size="lg")
                        else:
                            ui.notify("Não foi possível ler os dados do PDF automaticamente.", color="warning")

                    except Exception as err:
                        ui.notify(f"Erro ao processar PDF: {err}", color="negative")

                ui.upload(on_upload=handle_upload, auto_upload=True).props("accept=.pdf flat").classes("w-full bg-white border-2 border-dashed border-slate-300 p-2 rounded-lg")
                
                ui.separator()

                ui.label("📷 Opção 2: Escanear com a Câmera Traseira").classes("font-bold text-slate-800 text-base")

                ui.button("📷 ABRIR CÂMERA EM TELA CHEIA", on_click=lambda: ui.run_javascript("""
                    (async () => {
                        const box = document.getElementById("camera-box");
                        const status = document.getElementById("cam-status");
                        box.style.display = "flex";
                        status.innerHTML = "Iniciando câmera traseira...";

                        if (!window.codeReader) window.codeReader = new ZXing.BrowserMultiFormatReader();
                        try {
                            const constraints = { video: { facingMode: { exact: "environment" } } };
                            
                            window.codeReader.decodeFromConstraints(constraints, 'webcam-preview', (result, err) => {
                                if (result) {
                                    status.innerHTML = "✅ Código Lido!";
                                    fecharCamera();
                                    emitEvent('boleto_escaneado', { codigo: result.text });
                                }
                            }).catch(err => {
                                window.codeReader.decodeFromVideoDevice(null, 'webcam-preview', (result) => {
                                    if (result) {
                                        fecharCamera();
                                        emitEvent('boleto_escaneado', { codigo: result.text });
                                    }
                                });
                            });
                        } catch (err) { 
                            status.innerHTML = "Erro ao acessar a câmera"; 
                        }
                    })();
                """)).classes("w-full bg-purple-700 hover:bg-purple-800 text-white font-bold py-3 text-lg rounded-xl shadow-md")

                ui.separator()

                ui.label("✏️ Opção 3: Digitar Código").classes("font-bold text-slate-800 text-base")
                with ui.column().classes("w-full gap-2"):
                    input_linha = ui.input("Cole ou digite a linha digitável", placeholder="00000.00000...").props("outlined bg-white size=lg").classes("w-full")
                    ui.button("Processar Linha", on_click=lambda: aplicar_dados_boleto(input_linha.value)).classes("w-full bg-slate-700 text-white font-bold py-2 rounded-lg")

            container_camera.bind_visibility_from(modo, "value", backward=lambda v: v == "camera")

            def limpar_formulario():
                input_empresa.value = ""
                input_valor.value = None
                input_vencimento.value = None
                select_categoria.value = None
                select_status.value = "PENDENTE"
                check_lembrete.value = False
                select_canal.value = "WhatsApp"
                select_antecedencia.value = 1
                input_horario.value = "09:00"
                input_linha.value = ""

            def salvar_boleto():
                empresa_val = input_empresa.value.strip() if input_empresa.value else ""
                valor_val = float(input_valor.value) if input_valor.value else 0.0
                vencimento_val = input_vencimento.value

                if not empresa_val or not valor_val or not vencimento_val:
                    ui.notify("Por favor, preencha Empresa, Valor e Vencimento!", color="warning", size="lg")
                    return

                payload = {
                    "user_id": user_id,
                    "empresa": empresa_val,
                    "valor": valor_val,
                    "data_vencimento": vencimento_val,
                    "categoria_id": select_categoria.value if select_categoria.value else None,
                    "status": select_status.value,
                    "tem_lembrete": check_lembrete.value,
                }

                if check_lembrete.value:
                    payload["canal_lembrete"] = select_canal.value
                    payload["antecedencia_dias"] = select_antecedencia.value
                    payload["horario_lembrete"] = input_horario.value

                try:
                    supabase.table("boletos").insert(payload).execute()
                    ui.notify("Boleto cadastrado com sucesso!", color="positive", size="lg")
                    limpar_formulario()
                    renderizar_boletos_filtrados()
                except Exception as err:
                    ui.notify(f"Erro ao salvar boleto: {err}", color="negative", size="lg")

            ui.button("💾 CONFIRMAR E SALVAR BOLETO", on_click=salvar_boleto).classes(
                "bg-green-600 hover:bg-green-700 text-white font-extrabold text-lg mt-2 w-full py-4 rounded-xl shadow-lg"
            )

        # ==========================================
        # 3. LISTA E FILTROS DE BOLETOS
        # ==========================================
        
        ui.label("📋 Meus Boletos Cadastrados").classes("text-xl sm:text-2xl font-bold text-slate-800 mt-4")

        # Filtros Adicionais em Sanfona
        with ui.expansion("🔍 Filtros Avançados de Pesquisa", icon="filter_alt").classes("w-full bg-slate-100 border border-slate-300 rounded-xl font-bold text-slate-700 text-base"):
            with ui.column().classes("w-full p-3 gap-3 bg-white rounded-b-xl"):
                input_busca = ui.input("Empresa/Descrição", placeholder="Buscar por nome...").props("outlined dense bg-white").classes("w-full")
                
                with ui.grid().classes("w-full grid-cols-1 sm:grid-cols-2 gap-3"):
                    opcoes_cat = {"TODAS": "Todas as Categorias"}
                    opcoes_cat.update(categorias_list)
                    select_filtro_cat = ui.select(opcoes_cat, value="TODAS", label="Categoria").props("outlined dense").classes("w-full")
                    
                    select_filtro_status = ui.select(
                        {"TODOS": "Todos os Status", "PENDENTE": "PENDENTE", "PAGO": "PAGO", "ATRASADO": "ATRASADO", "CANCELADO": "CANCELADO"},
                        value="TODOS", label="Filtrar por Status no Filtro"
                    ).props("outlined dense").classes("w-full")

                ui.label("Período de Vencimento:").classes("text-sm font-semibold text-slate-600 mt-1")
                with ui.grid().classes("w-full grid-cols-2 gap-3"):
                    dt_inicio = ui.input("De").props("type=date outlined dense").classes("w-full")
                    dt_fim = ui.input("Até").props("type=date outlined dense").classes("w-full")

                ui.label("Faixa de Valor (R$):").classes("text-sm font-semibold text-slate-600 mt-1")
                with ui.grid().classes("w-full grid-cols-2 gap-3"):
                    val_min = ui.number("Valor Mínimo").props("outlined dense").classes("w-full")
                    val_max = ui.number("Valor Máximo").props("outlined dense").classes("w-full")

        # Vincular atualização dos filtros à renderização
        for element in [input_busca, select_filtro_cat, select_filtro_status, dt_inicio, dt_fim, val_min, val_max]:
            element.on("update:model-value", lambda: renderizar_boletos_filtrados())

        # ==========================================
        # SEPARAÇÃO POR ABAS (STATUS)
        # ==========================================
        with ui.tabs().classes("w-full text-purple-900 font-bold") as tabs:
            tab_pendentes = ui.tab("pendentes", label="A Vencer")
            tab_atrasados = ui.tab("atrasados", label="Atrasados")
            tab_pagos = ui.tab("pagos", label="Pagos")
            tab_todos = ui.tab("todos", label="Todos")

        container_boletos = ui.column().classes("w-full gap-3 mt-2")

        # ==========================================
        # MODAL DE EDIÇÃO COMPLETA DO BOLETO
        # ==========================================
        def abrir_modal_edicao(b):
            dialog = ui.dialog()
            with dialog, ui.card().classes("w-full max-w-lg p-5 gap-4 bg-white rounded-2xl"):
                ui.label("✏️ Editar Boleto").classes("text-xl font-bold text-slate-800 border-b pb-2 w-full")

                edit_empresa = ui.input("Empresa / Nome", value=b.get("empresa", "")).props("outlined bg-slate-50").classes("w-full")
                
                with ui.grid().classes("w-full grid-cols-1 sm:grid-cols-2 gap-3"):
                    edit_valor = ui.number("Valor (R$)", value=float(b.get("valor", 0)), format="%.2f").props("outlined bg-slate-50").classes("w-full")
                    edit_vencimento = ui.input("Vencimento", value=b.get("data_vencimento", "")).props("type=date outlined bg-slate-50").classes("w-full")

                with ui.grid().classes("w-full grid-cols-1 sm:grid-cols-2 gap-3"):
                    edit_categoria = ui.select(categorias_list, label="Categoria", value=b.get("categoria_id")).props("outlined bg-slate-50").classes("w-full")
                    edit_status = ui.select(["PENDENTE", "PAGO", "ATRASADO", "CANCELADO"], label="Status", value=b.get("status", "PENDENTE")).props("outlined bg-slate-50").classes("w-full")

                ui.separator()

                edit_check_lembrete = ui.checkbox("🔔 Lembrete configurado", value=bool(b.get("tem_lembrete"))).classes("text-base font-bold text-slate-800")
                
                box_edit_lembrete = ui.column().classes("w-full p-3 bg-purple-50 border border-purple-200 rounded-xl gap-3")
                box_edit_lembrete.bind_visibility_from(edit_check_lembrete, "value")

                with box_edit_lembrete:
                    edit_select_canal = ui.select(["SMS", "WhatsApp", "E-mail", "Todos"], label="Canal", value=b.get("canal_lembrete", "WhatsApp")).props("outlined bg-white").classes("w-full")
                    
                    antecedencia_map = {0: "No dia do vencimento", 1: "1 dia antes", 2: "2 dias antes", 3: "3 dias antes", 5: "5 dias antes"}
                    val_antecedencia = b.get("antecedencia_dias", 1)
                    edit_select_antecedencia = ui.select(antecedencia_map, label="Aviso", value=val_antecedencia if val_antecedencia in antecedencia_map else 1).props("outlined bg-white").classes("w-full")
                    
                    edit_input_horario = ui.input("Horário", value=b.get("horario_lembrete", "09:00")).props("type=time outlined bg-white").classes("w-full")

                def salvar_edicao():
                    payload_update = {
                        "empresa": edit_empresa.value.strip() if edit_empresa.value else "",
                        "valor": float(edit_valor.value) if edit_valor.value else 0.0,
                        "data_vencimento": edit_vencimento.value,
                        "categoria_id": edit_categoria.value if edit_categoria.value else None,
                        "status": edit_status.value,
                        "tem_lembrete": edit_check_lembrete.value,
                    }

                    if edit_check_lembrete.value:
                        payload_update["canal_lembrete"] = edit_select_canal.value
                        payload_update["antecedencia_dias"] = edit_select_antecedencia.value
                        payload_update["horario_lembrete"] = edit_input_horario.value
                    else:
                        payload_update["canal_lembrete"] = None
                        payload_update["antecedencia_dias"] = None
                        payload_update["horario_lembrete"] = None

                    try:
                        supabase.table("boletos").update(payload_update).eq("id", b["id"]).execute()
                        ui.notify("Boleto atualizado com sucesso!", color="positive")
                        dialog.close()
                        renderizar_boletos_filtrados()
                    except Exception as err:
                        ui.notify(f"Erro ao atualizar: {err}", color="negative")

                with ui.row().classes("w-full justify-end gap-3 mt-2"):
                    ui.button("Cancelar", on_click=dialog.close).props("flat color=grey")
                    ui.button("Salvar Alterações", on_click=salvar_edicao).classes("bg-purple-700 text-white font-bold px-4 py-2 rounded-lg")

            dialog.open()

        def alternar_status_pago(b):
            novo_status = "PENDENTE" if b.get("status") == "PAGO" else "PAGO"
            try:
                supabase.table("boletos").update({"status": novo_status}).eq("id", b["id"]).execute()
                ui.notify(f"Status alterado para {novo_status}!", color="positive")
                renderizar_boletos_filtrados()
            except Exception as err:
                ui.notify(f"Erro ao alterar status: {err}", color="negative")

        def deletar_boleto(b_id):
            try:
                supabase.table("boletos").delete().eq("id", b_id).execute()
                ui.notify("Boleto excluído com sucesso!", color="info")
                renderizar_boletos_filtrados()
            except Exception as err:
                ui.notify(f"Erro ao excluir: {err}", color="negative")

        # ==========================================
        # RENDERIZAÇÃO DE CARDS POR STATUS/ABA
        # ==========================================
        def renderizar_boletos_filtrados():
            container_boletos.clear()
            
            res = supabase.table("boletos").select("*").eq("user_id", user_id).order("data_vencimento", desc=False).execute()
            boletos_dados = res.data or []

            hoje = datetime.now().date()

            boletos_filtrados = []
            
            cnt_pendentes = 0
            cnt_atrasados = 0
            cnt_pagos = 0
            cnt_todos = 0

            for b in boletos_dados:
                dt_venc = None
                if b.get("data_vencimento"):
                    try:
                        dt_venc = datetime.strptime(b["data_vencimento"], "%Y-%m-%d").date()
                    except ValueError:
                        pass

                status_atual = b.get("status", "PENDENTE")
                if status_atual == "PENDENTE" and dt_venc and dt_venc < hoje:
                    status_atual = "ATRASADO"

                # Aplicação dos Filtros em Sanfona
                if input_busca.value and input_busca.value.lower() not in b.get("empresa", "").lower():
                    continue

                if select_filtro_cat.value != "TODAS" and b.get("categoria_id") != select_filtro_cat.value:
                    continue

                if select_filtro_status.value != "TODOS" and status_atual != select_filtro_status.value:
                    continue

                if dt_inicio.value and b.get("data_vencimento") and b["data_vencimento"] < dt_inicio.value:
                    continue

                if dt_fim.value and b.get("data_vencimento") and b["data_vencimento"] > dt_fim.value:
                    continue

                if val_min.value is not None and float(b.get("valor", 0)) < float(val_min.value):
                    continue

                if val_max.value is not None and float(b.get("valor", 0)) > float(val_max.value):
                    continue

                # Contagem totalizadora para os rótulos das Abas
                cnt_todos += 1
                if status_atual == "PENDENTE":
                    cnt_pendentes += 1
                elif status_atual == "ATRASADO":
                    cnt_atrasados += 1
                elif status_atual == "PAGO":
                    cnt_pagos += 1

                # Filtro da Aba Selecionada
                aba_ativa = tabs.value
                if aba_ativa == "pendentes" and status_atual != "PENDENTE":
                    continue
                elif aba_ativa == "atrasados" and status_atual != "ATRASADO":
                    continue
                elif aba_ativa == "pagos" and status_atual != "PAGO":
                    continue

                boletos_filtrados.append((b, dt_venc, status_atual))

            # Atualização do nome das abas com badges de contagem
            tab_pendentes.text = f"A Vencer ({cnt_pendentes})"
            tab_atrasados.text = f"Atrasados ({cnt_atrasados})"
            tab_pagos.text = f"Pagos ({cnt_pagos})"
            tab_todos.text = f"Todos ({cnt_todos})"

            if not boletos_filtrados:
                with container_boletos:
                    ui.label("Nenhum boleto nesta categoria.").classes("text-slate-500 italic p-4 text-center w-full bg-slate-50 rounded-xl border border-dashed")
                return

            # Renderização dos Cards
            with container_boletos:
                for b, dt_venc, status_efetivo in boletos_filtrados:
                    
                    vence_hoje_ou_atrasado = (status_efetivo != "PAGO" and dt_venc and dt_venc <= hoje) or status_efetivo == "ATRASADO"
                    
                    if vence_hoje_ou_atrasado:
                        card_classes = "w-full p-4 bg-red-50 border-2 border-red-500 rounded-xl shadow-md transition-all gap-2"
                        badge_classes = "bg-red-600 text-white px-2 py-1 rounded text-xs font-bold"
                        texto_venc_class = "text-red-700 font-extrabold"
                    else:
                        card_classes = "w-full p-4 bg-white border border-slate-200 rounded-xl shadow-sm hover:shadow-md transition-all gap-2"
                        badge_classes = "bg-purple-100 text-purple-800 px-2 py-1 rounded text-xs font-bold"
                        texto_venc_class = "text-slate-600 font-medium"

                    with ui.card().classes(card_classes):
                        with ui.row().classes("w-full justify-between items-center"):
                            with ui.column().classes("gap-0"):
                                ui.label(b.get("empresa", "Sem Nome")).classes("text-lg font-bold text-slate-800")
                                cat_nome = categorias_list.get(b.get("categoria_id"), "Sem Categoria")
                                ui.label(f"Categoria: {cat_nome}").classes("text-xs text-slate-500")

                            with ui.row().classes("items-center gap-2"):
                                if status_efetivo == "PAGO":
                                    ui.label("PAGO").classes("bg-green-100 text-green-800 px-2 py-1 rounded text-xs font-bold")
                                elif vence_hoje_ou_atrasado:
                                    tag_texto = "VENCE HOJE" if dt_venc == hoje else "ATRASADO"
                                    ui.label(tag_texto).classes(badge_classes)
                                else:
                                    ui.label(status_efetivo).classes(badge_classes)

                        ui.separator().classes("my-1")

                        with ui.row().classes("w-full justify-between items-center"):
                            val_fmt = f"R$ {float(b.get('valor', 0)):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                            ui.label(val_fmt).classes("text-xl font-black text-slate-800")
                            
                            venc_str = dt_venc.strftime("%d/%m/%Y") if dt_venc else b.get("data_vencimento", "N/A")
                            ui.label(f"Vencimento: {venc_str}").classes(f"text-sm {texto_venc_class}")

                        # Botões de Ação
                        with ui.row().classes("w-full justify-end items-center gap-2 mt-2 pt-2 border-t border-slate-100"):
                            btn_pago_cor = "positive" if status_efetivo != "PAGO" else "warning"
                            btn_pago_lbl = "Marcar como Pago" if status_efetivo != "PAGO" else "Marcar Pendente"
                            btn_pago_ico = "check_circle" if status_efetivo != "PAGO" else "undo"

                            ui.button(btn_pago_lbl, icon=btn_pago_ico, on_click=lambda b=b: alternar_status_pago(b)).props(f"outline size=sm color={btn_pago_cor}")
                            ui.button("Editar", icon="edit", on_click=lambda b=b: abrir_modal_edicao(b)).props("flat size=sm color=grey-8")
                            ui.button(icon="delete", on_click=lambda b_id=b["id"]: deletar_boleto(b_id)).props("flat size=sm color=red")

        tabs.on("update:model-value", renderizar_boletos_filtrados)
        renderizar_boletos_filtrados()

    # ==========================================
    # 4. RODAPÉ FIXO DE NAVEGAÇÃO / AÇÕES
    # ==========================================
   # with ui.footer().classes("bg-slate-900 text-white p-2 border-t border-slate-800 flex justify-around items-center shadow-lg"):
   #     ui.button("Boletos", icon="receipt", on_click=lambda: ui.navigate.to("/")).props("flat color=white text-color=purple-3").classes("flex-1")
   #     ui.button("Perfil", icon="person", on_click=lambda: ui.navigate.to("/perfil")).props("flat color=white").classes("flex-1")
   #     ui.button("Notificações", icon="notifications", on_click=lambda: ui.notify("Notificações ativas!", color="info")).props("flat color=white").classes("flex-1")
   #     ui.button("Sair", icon="logout", on_click=lambda: (app.storage.user.clear(), ui.navigate.to("/login"))).props("flat color=red-4").classes("flex-1")


# ==========================================
# 4. PAINEL EXCLUSIVO DO ADMIN
# ==========================================

@ui.page("/admin")
def admin_page():
    user_email = app.storage.user.get("email", "")
    if not app.storage.user.get("user_id") or user_email != ADMIN_EMAIL:
        ui.navigate.to("/")
        return

    drawer = menu_drawer()
    cabecalho_app(drawer)

    with ui.column().classes("w-full max-w-5xl mx-auto p-4 gap-6"):
        ui.label("⚙️ Painel do Administrador").classes(
            "text-2xl font-bold text-amber-900"
        )

        # 1. SOLICITAÇÕES PENDENTES
        with ui.card().classes("w-full p-4 border border-amber-200 bg-white"):
            ui.label("Solicitações Pendentes de Acesso").classes(
                "text-lg font-bold mb-2"
            )

            solicitacoes = (
                supabase.table("solicitacoes_acesso")
                .select("*")
                .eq("status", "PENDENTE")
                .execute()
                .data
                or []
            )

            if not solicitacoes:
                ui.label("Nenhuma solicitação pendente.").classes(
                    "text-sm text-gray-500"
                )

            for sol in solicitacoes:

                def aprovar(s=sol):
                    supabase.table("perfis_usuarios").insert({
                        "email": s["email"],
                        "senha": s["senha_temporaria"],
                        "ativo": True,
                    }).execute()
                    supabase.table("solicitacoes_acesso").update(
                        {"status": "APROVADO"}
                    ).eq("id", s["id"]).execute()
                    ui.notify(
                        f"Acesso concedido para {s['email']}!", color="positive"
                    )
                    ui.navigate.reload()

                with ui.row().classes(
                    "w-full justify-between items-center border-b py-2"
                ):
                    ui.label(f"{sol['email']} ({sol['localizacao']})").classes(
                        "text-sm"
                    )
                    ui.button("Aprovar", on_click=aprovar).classes(
                        "bg-green-600 text-white text-xs"
                    )

        # 2. GERENCIAMENTO DE USUÁRIOS (ATIVAR, INATIVAR E EXCLUIR)
        with ui.card().classes("w-full p-4 border border-amber-200 bg-white"):
            ui.label("👥 Usuários Cadastrados").classes(
                "text-lg font-bold mb-2"
            )

            usuarios = (
                supabase.table("perfis_usuarios")
                .select("*")
                .order("email")
                .execute()
                .data
                or []
            )

            for usr in usuarios:

                def alternar_status(u=usr):
                    novo_status = not u.get("ativo", True)
                    supabase.table("perfis_usuarios").update(
                        {"ativo": novo_status}
                    ).eq("id", u["id"]).execute()
                    ui.notify(
                        f"Status de {u['email']} alterado!", color="info"
                    )
                    ui.navigate.reload()

                def confirmar_exclusao(u=usr):
                    with ui.dialog() as dialog, ui.card().classes(
                        "w-full max-w-sm p-4"
                    ):
                        ui.label("⚠️ Confirmar Exclusão").classes(
                            "text-lg font-bold text-red-600 mb-2"
                        )
                        ui.label(
                            f"Tem certeza que deseja excluir o usuário '{u['email']}'? "
                            "Esta ação apagará todos os agendamentos vinculados a esta conta e não poderá ser desfeita."
                        ).classes("text-sm text-gray-700 mb-4")

                        def executar_exclusao():
                            dialog.close()
                            supabase.table("boletos").delete().eq(
                                "user_id", u["id"]
                            ).execute()
                            supabase.table("perfis_usuarios").delete().eq(
                                "id", u["id"]
                            ).execute()
                            ui.notify(
                                f"Usuário {u['email']} excluído com sucesso!",
                                color="negative",
                            )
                            ui.navigate.reload()

                        with ui.row().classes("w-full justify-end gap-2"):
                            ui.button(
                                "CANCELAR", on_click=dialog.close
                            ).props("flat text-color=gray")
                            ui.button(
                                "EXCLUIR", on_click=executar_exclusao
                            ).classes("bg-red-600 text-white font-bold")

                    dialog.open()

                with ui.row().classes(
                    "w-full justify-between items-center border-b py-2"
                ):
                    with ui.column().classes("gap-0"):
                        ui.label(usr["email"]).classes("font-bold text-sm")
                        status_label = (
                            "Ativo" if usr.get("ativo", True) else "Inativo"
                        )
                        cor_status = (
                            "text-green-600"
                            if usr.get("ativo", True)
                            else "text-red-600"
                        )
                        ui.label(f"Status: {status_label}").classes(
                            f"text-xs {cor_status}"
                        )

                    if usr["email"] != ADMIN_EMAIL:
                        with ui.row().classes("gap-2"):
                            btn_label = (
                                "Inativar"
                                if usr.get("ativo", True)
                                else "Ativar"
                            )
                            btn_color = (
                                "warning"
                                if usr.get("ativo", True)
                                else "positive"
                            )

                            ui.button(
                                btn_label, on_click=alternar_status
                            ).props(f"color={btn_color} dense size=sm")
                            ui.button(
                                "Excluir", on_click=confirmar_exclusao
                            ).props("color=negative dense size=sm")

        # 3. GERENCIAMENTO DE CATEGORIAS (LISTA, EDIÇÃO E EXCLUSÃO)
        with ui.card().classes("w-full p-4 border border-amber-200 bg-white"):
            ui.label("🏷️ Categorias de Contas").classes(
                "text-lg font-bold mb-2"
            )

            with ui.row().classes("w-full items-center gap-2 mb-4"):
                nova_cat = (
                    ui.input(placeholder="Nova Categoria")
                    .props("outlined bg-white dense")
                    .classes("flex-1")
                )

                def add_categoria():
                    if nova_cat.value and nova_cat.value.strip():
                        supabase.table("dim_categorias").insert(
                            {"nome": nova_cat.value.strip()}
                        ).execute()
                        ui.notify(
                            "Categoria criada com sucesso!", color="positive"
                        )
                        ui.navigate.reload()

                ui.button("ADICIONAR CATEGORIA", on_click=add_categoria).classes(
                    "bg-blue-600 text-white font-bold"
                )

            ui.separator().classes("my-2")

            categorias = (
                supabase.table("dim_categorias")
                .select("*")
                .order("nome")
                .execute()
                .data
                or []
            )

            if not categorias:
                ui.label("Nenhuma categoria cadastrada.").classes(
                    "text-sm text-gray-500 italic"
                )

            for cat in categorias:

                def editar_categoria(c=cat):
                    with ui.dialog() as dialog, ui.card().classes(
                        "w-full max-w-sm p-4"
                    ):
                        ui.label("✏️ Editar Categoria").classes(
                            "text-lg font-bold text-slate-800 mb-2"
                        )
                        campo_nome = (
                            ui.input("Nome da Categoria", value=c["nome"])
                            .props("outlined dense")
                            .classes("w-full mb-4")
                        )

                        def salvar_edicao():
                            if (
                                campo_nome.value
                                and campo_nome.value.strip()
                            ):
                                supabase.table("dim_categorias").update(
                                    {"nome": campo_nome.value.strip()}
                                ).eq("id", c["id"]).execute()
                                dialog.close()
                                ui.notify(
                                    "Categoria atualizada!", color="positive"
                                )
                                ui.navigate.reload()

                        with ui.row().classes("w-full justify-end gap-2"):
                            ui.button(
                                "CANCELAR", on_click=dialog.close
                            ).props("flat text-color=gray")
                            ui.button(
                                "SALVAR", on_click=salvar_edicao
                            ).classes("bg-green-600 text-white font-bold")

                    dialog.open()

                def confirmar_exclusao_categoria(c=cat):
                    with ui.dialog() as dialog, ui.card().classes(
                        "w-full max-w-sm p-4"
                    ):
                        ui.label("⚠️ Confirmar Exclusão").classes(
                            "text-lg font-bold text-red-600 mb-2"
                        )
                        ui.label(
                            f"Tem certeza que deseja excluir a categoria '{c['nome']}'?"
                        ).classes("text-sm text-gray-700 mb-4")

                        def executar_exclusao():
                            dialog.close()
                            supabase.table("dim_categorias").delete().eq(
                                "id", c["id"]
                            ).execute()
                            ui.notify(
                                f"Categoria '{c['nome']}' excluída!",
                                color="negative",
                            )
                            ui.navigate.reload()

                        with ui.row().classes("w-full justify-end gap-2"):
                            ui.button(
                                "CANCELAR", on_click=dialog.close
                            ).props("flat text-color=gray")
                            ui.button(
                                "EXCLUIR", on_click=executar_exclusao
                            ).classes("bg-red-600 text-white font-bold")

                    dialog.open()

                with ui.row().classes(
                    "w-full justify-between items-center border-b py-2"
                ):
                    ui.label(cat["nome"]).classes(
                        "text-sm font-medium text-gray-800"
                    )

                    with ui.row().classes("gap-2"):
                        ui.button("Editar", on_click=editar_categoria).props(
                            "color=amber dense size=sm"
                        )
                        ui.button(
                            "Excluir", on_click=confirmar_exclusao_categoria
                        ).props("color=negative dense size=sm")


from nicegui import app

# Rota leve apenas para o bot de ping responder status 200
@app.get('/ping')
def ping():
    return {'status': 'ok'}                        


import os
import subprocess
from nicegui import ui

# ... (todo o seu código existente acima permanece igual) ...

# Inicia o agendador em segundo plano de forma assíncrona
try:
    subprocess.Popen(["python", "scheduler.py"])
    print("Scheduler iniciado com sucesso em segundo plano!")
except Exception as e:
    print(f"Erro ao iniciar o scheduler: {e}")

# Configuração do servidor NiceGUI para o Render
port = int(os.environ.get("PORT", 8080))
ui.run(
    host="0.0.0.0",
    port=port,
    storage_secret="sua_chave_secreta_aqui"
)