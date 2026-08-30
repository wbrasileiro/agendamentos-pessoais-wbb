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


# --- DASHBOARD DO USUÁRIO ---
import io
import re
from datetime import datetime, timedelta
from nicegui import app, ui
from pypdf import PdfReader

# Assuma que 'supabase', 'ADMIN_EMAIL', 'menu_drawer', 'cabecalho_app' e 'formatar_br' 
# estão importados ou definidos no escopo principal do seu projeto.


# ==========================================
# 1. FUNÇÕES AUXILIARES E EXTRAÇÃO DE BOLETO
# ==========================================

def formatar_data_br(data_str):
    """Converte data YYYY-MM-DD para DD/MM/AAAA."""
    if not data_str:
        return ""
    try:
        partes = str(data_str).split("-")
        if len(partes) == 3:
            return f"{partes[2]}/{partes[1]}/{partes[0]}"
    except Exception:
        pass
    return data_str


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
    """Extrai linha digitável, valor, vencimento e beneficiário do PDF."""
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


# ==========================================
# 2. PÁGINA PRINCIPAL DE BOLETOS
# ==========================================

@ui.page("/")
def home_page():
    if not app.storage.user.get("user_id"):
        ui.navigate.to("/login")
        return

    ui.add_head_html(
        '<script src="https://unpkg.com/@zxing/library@latest"></script>'
    )

    drawer = menu_drawer()
    cabecalho_app(drawer)
    user_id = app.storage.user.get("user_id")

    # Busca perfil do usuário para validar contatos
    res_perfil = supabase.table("perfis_usuarios").select("*").eq("id", user_id).execute()
    perfil_usr = res_perfil.data[0] if res_perfil.data else {}
    email_cadastrado = perfil_usr.get("email_notificacao") or perfil_usr.get("email", "")
    whatsapp_cadastrado = perfil_usr.get("whatsapp", "")

    with ui.column().classes("w-full max-w-5xl mx-auto p-4 gap-6"):
        with ui.row().classes("w-full justify-between items-center"):
            ui.label("📋 Meus Boletos Agendados").classes("text-2xl font-bold text-slate-800")
            
            with ui.row().classes("items-center gap-2 bg-purple-50 p-2 rounded-lg border border-purple-200"):
                ui.icon("notifications", color="purple").classes("text-lg")
                ui.label(f"Contato: {whatsapp_cadastrado or email_cadastrado or 'Não configurado'}").classes("text-xs text-purple-900 font-medium")
                ui.button("Alterar Contatos", on_click=lambda: ui.navigate.to("/perfil")).props("flat dense size=sm color=purple")

        # FORMULÁRIO DE CADASTRO
        with ui.card().classes("w-full p-5 border border-slate-200 bg-white shadow-sm rounded-xl"):
            ui.label("➕ Cadastrar Novo Boleto").classes("text-lg font-bold text-slate-700 mb-2")

            modo = (
                ui.radio(
                    {
                        "manual": "Entrada Manual",
                        "camera": "Importar PDF / Escanear Código",
                    },
                    value="manual",
                )
                .props("inline")
                .classes("mb-4 font-medium text-slate-600")
            )

            cats_res = supabase.table("dim_categorias").select("*").execute()
            categorias_list = {c["id"]: c["nome"] for c in (cats_res.data or [])}

            with ui.row().classes("w-full gap-4 items-center"):
                input_empresa = (
                    ui.input("Empresa / Descrição")
                    .props("outlined dense")
                    .classes("flex-1")
                )
                input_valor = (
                    ui.number("Valor (R$)", format="%.2f")
                    .props("outlined dense")
                    .classes("w-36")
                )
                input_vencimento = (
                    ui.input("Vencimento")
                    .props("type=date outlined dense")
                    .classes("w-40")
                )

            with ui.row().classes("w-full gap-4 items-center mt-2"):
                select_categoria = (
                    ui.select(categorias_list, label="Categoria")
                    .props("outlined dense")
                    .classes("flex-1")
                )
                select_status = (
                    ui.select(
                        ["PENDENTE", "PAGO", "ATRASADO", "CANCELADO"],
                        value="PENDENTE",
                        label="Status",
                    )
                    .props("outlined dense")
                    .classes("w-40")
                )

            # LEMBRETES
            check_lembrete = ui.checkbox(
                "Definir lembrete de vencimento"
            ).classes("mt-4 text-slate-700 font-medium")

            container_lembrete = ui.column().classes(
                "w-full p-4 bg-purple-50/50 border border-purple-100 rounded-lg mt-2 gap-3"
            )
            container_lembrete.bind_visibility_from(check_lembrete, "value")

            with container_lembrete:
                ui.label("🔔 Configurações do Alerta").classes(
                    "text-xs font-bold text-purple-700 uppercase tracking-wider"
                )
                with ui.row().classes("w-full gap-4 items-center"):
                    select_canal = (
                        ui.select(
                            ["SMS", "WhatsApp", "E-mail", "Todos"],
                            value="WhatsApp",
                            label="Canal de Notificação",
                        )
                        .props("outlined dense bg-white")
                        .classes("flex-1")
                    )
                    select_antecedencia = (
                        ui.select(
                            {
                                0: "No dia do vencimento",
                                1: "1 dia antes",
                                2: "2 dias antes",
                                3: "3 dias antes",
                                4: "4 dias antes",
                                5: "5 dias antes",
                            },
                            value=1,
                            label="Antecedência",
                        )
                        .props("outlined dense bg-white")
                        .classes("flex-1")
                    )
                    input_horario = (
                        ui.input("Horário", value="09:00")
                        .props("type=time outlined dense bg-white")
                        .classes("w-32")
                    )

            # CÂMERA E UPLOAD DE PDF
            def aplicar_dados_boleto(codigo_raw):
                val, venc, limpo = decodificar_boleto(codigo_raw)
                if val is not None:
                    input_valor.value = val
                if venc is not None:
                    input_vencimento.value = venc

                if val or venc:
                    ui.notify("Dados extraídos com sucesso!", color="positive")
                else:
                    ui.notify(
                        f"Código ({limpo}) lido, mas não possui formato padrão.",
                        color="warning",
                    )

            def processar_codigo_escaneado(e):
                aplicar_dados_boleto(e.args.get("codigo", ""))

            ui.on("boleto_escaneado", processar_codigo_escaneado)

            container_camera = ui.column().classes(
                "w-full mb-4 gap-4 p-4 bg-slate-50 border border-slate-200 rounded-lg"
            )
            with container_camera:
                ui.label("📄 Upload do PDF do Boleto").classes("font-bold text-slate-700")

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
                            ui.notify("PDF lido com sucesso!", color="positive")
                        else:
                            ui.notify("Não foi possível extrair dados automáticos deste PDF.", color="warning")

                    except Exception as err:
                        ui.notify(f"Erro ao processar PDF: {err}", color="negative")

                ui.upload(on_upload=handle_upload, auto_upload=True).props("accept=.pdf flat").classes("w-full bg-white border border-dashed border-slate-300 p-2")
                ui.separator()

                with ui.row().classes("w-full gap-2 items-center"):
                    input_linha = ui.input("Cole a Linha Digitável", placeholder="Ex: 23793...").props("outlined dense").classes("flex-1 bg-white")
                    ui.button("Processar", on_click=lambda: aplicar_dados_boleto(input_linha.value)).classes("bg-purple-700 text-white font-bold")

                ui.separator()

                ui.html("""
                    <div id="camera-box" style="display: none; position: relative; width: 100%; max-width: 480px; margin: 0 auto; overflow: hidden; border-radius: 12px; border: 2px solid #7e22ce; background-color: #000;">
                        <video id="webcam-preview" style="width: 100%; height: 260px; object-fit: cover;"></video>
                        <div style="position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); width: 85%; height: 90px; border: 2px dashed #38bdf8; border-radius: 8px; box-shadow: 0 0 0 9999px rgba(0,0,0,0.55); pointer-events: none;">
                            <div style="width: 100%; height: 2px; background: red; position: absolute; top: 50%; transform: translateY(-50%); animation: scan 2s infinite linear;"></div>
                        </div>
                        <style>
                            @keyframes scan { 0% { top: 10%; } 50% { top: 90%; } 100% { top: 10%; } }
                        </style>
                        <div id="cam-status" style="position: absolute; bottom: 8px; left: 0; right: 0; text-align: center; color: #fff; background: rgba(0,0,0,0.7); padding: 4px; font-size: 13px; font-weight: bold;">
                            Alinhe o código dentro do retângulo
                        </div>
                    </div>
                """)

                with ui.row().classes("w-full justify-center gap-2 mt-2"):
                    ui.button("📷 Abrir Câmera", on_click=lambda: ui.run_javascript("""
                        (async () => {
                            const box = document.getElementById("camera-box");
                            const status = document.getElementById("cam-status");
                            box.style.display = "block";
                            status.innerHTML = "Iniciando Câmera...";
                            if (!window.codeReader) window.codeReader = new ZXing.BrowserMultiFormatReader();
                            try {
                                const videoDevices = await window.codeReader.listVideoInputDevices();
                                if (videoDevices.length === 0) return alert("Câmera não encontrada.");
                                let selectedId = videoDevices[0].deviceId;
                                for (let dev of videoDevices) {
                                    if (dev.label.toLowerCase().includes('back') || dev.label.toLowerCase().includes('traseira')) {
                                        selectedId = dev.deviceId;
                                        break;
                                    }
                                }
                                status.innerHTML = "Aproxime devagar...";
                                window.codeReader.decodeFromVideoDevice(selectedId, 'webcam-preview', (result, err) => {
                                    if (result) {
                                        status.innerHTML = "✅ Sucesso!";
                                        window.codeReader.reset();
                                        box.style.display = "none";
                                        emitEvent('boleto_escaneado', { codigo: result.text });
                                    }
                                });
                            } catch (err) { console.error(err); }
                        })();
                    """)).classes("bg-purple-700 text-white font-bold")

                    ui.button("🛑 Fechar Câmera", on_click=lambda: ui.run_javascript("""
                        if (window.codeReader) window.codeReader.reset();
                        document.getElementById("camera-box").style.display = "none";
                    """)).classes("bg-gray-600 text-white font-bold")

            container_camera.bind_visibility_from(modo, "value", backward=lambda v: v == "camera")

            modo.on_value_change(lambda e: ui.run_javascript("""
                if (window.codeReader) window.codeReader.reset();
                document.getElementById("camera-box").style.display = "none";
            """) if e.value == "manual" else None)

            # SALVAR NOVO BOLETO
            def salvar_boleto():
                empresa_val = input_empresa.value.strip() if input_empresa.value else ""
                valor_val = float(input_valor.value) if input_valor.value else 0.0
                vencimento_val = input_vencimento.value

                if not empresa_val or not valor_val or not vencimento_val:
                    ui.notify("Preencha a Empresa, Valor e Vencimento!", color="warning")
                    return

                # Verifica duplicados
                dup_res = (
                    supabase.table("boletos")
                    .select("id")
                    .eq("user_id", user_id)
                    .ilike("empresa", empresa_val)
                    .eq("valor", valor_val)
                    .eq("data_vencimento", vencimento_val)
                    .execute()
                )
                if dup_res.data:
                    ui.notify("⚠️ Já existe um boleto cadastrado com a mesma empresa, valor e vencimento!", color="warning")
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

                tentativas = 0
                while tentativas < 5:
                    try:
                        supabase.table("boletos").insert(payload).execute()
                        ui.notify("Boleto cadastrado com sucesso!", color="positive")
                        ui.navigate.reload()
                        break
                    except Exception as err:
                        err_str = str(err)
                        tentativas += 1
                        if "tem_lembrete" in err_str:
                            payload.pop("tem_lembrete", None)
                        elif "antecedencia_dias" in err_str:
                            payload.pop("antecedencia_dias", None)
                            payload["dias_antecedencia"] = select_antecedencia.value
                        elif "dias_antecedencia" in err_str:
                            payload.pop("dias_antecedencia", None)
                        elif "canal_lembrete" in err_str:
                            payload.pop("canal_lembrete", None)
                        elif "horario_lembrete" in err_str:
                            payload.pop("horario_lembrete", None)
                        else:
                            ui.notify(f"Erro ao salvar boleto: {err}", color="negative")
                            break

            ui.button("CONFIRMAR E SALVAR BOLETO", on_click=salvar_boleto).classes(
                "bg-green-600 hover:bg-green-700 text-white font-bold mt-4 w-full py-3 rounded-lg shadow"
            )

        # ==========================================
        # TABELA DE BOLETOS COM FILTROS E AGRUPAMENTO
        # ==========================================

        # BARRA DE FILTROS (Empresa e Categoria)
        with ui.row().classes("w-full gap-4 items-center bg-slate-100 p-4 rounded-xl border border-slate-200 mb-2"):
            ui.icon("filter_alt", color="purple").classes("text-xl")
            ui.label("Filtros:").classes("font-bold text-slate-700")

            filtro_empresa = (
                ui.input("Buscar por Empresa", placeholder="Digite o nome...")
                .props("outlined dense clearable bg-white")
                .classes("flex-1")
            )

            opcoes_cat_filtro = {"TODAS": "Todas as Categorias"}
            opcoes_cat_filtro.update(categorias_list)

            filtro_categoria = (
                ui.select(opcoes_cat_filtro, value="TODAS", label="Filtrar Categoria")
                .props("outlined dense bg-white")
                .classes("w-56")
            )

        # BUSCA DADOS NO BANCO
        res = (
            supabase.table("boletos")
            .select("*, dim_categorias(nome)")
            .eq("user_id", user_id)
            .order("data_vencimento")
            .execute()
        )
        boletos_todos = res.data or []

        def montar_rows_filtrados():
            emp_busca = (filtro_empresa.value or "").strip().lower()
            cat_busca = filtro_categoria.value

            rows_filtrados = []
            for b in boletos_todos:
                nome_empresa = b.get("empresa", "")
                cat_id = b.get("categoria_id")
                cat_nome = b["dim_categorias"]["nome"] if b.get("dim_categorias") else "Geral"

                # Filtro por Empresa
                if emp_busca and emp_busca not in nome_empresa.lower():
                    continue

                # Filtro por Categoria
                if cat_busca != "TODAS" and cat_id != cat_busca:
                    continue

                # Lembretes
                if b.get("tem_lembrete"):
                    antecedencia = b.get("antecedencia_dias") if b.get("antecedencia_dias") is not None else b.get("dias_antecedencia", 0)
                    ant_str = "No dia" if antecedencia == 0 else f"{antecedencia}d antes"
                    canal_str = b.get("canal_lembrete") or "WhatsApp"
                    horario_str = b.get("horario_lembrete") or "09:00"
                    lembrete_info = f"{canal_str} • {ant_str} às {horario_str}"
                else:
                    lembrete_info = "Desativado"

                rows_filtrados.append({
                    "id": b["id"],
                    "empresa": nome_empresa,
                    "valor": f"R$ {formatar_br(b['valor'])}",
                    "vencimento": formatar_data_br(b["data_vencimento"]),
                    "categoria": cat_nome,
                    "lembrete": lembrete_info,
                    "tem_lembrete": b.get("tem_lembrete", False),
                    "status": b["status"],
                })
            return rows_filtrados

        cols = [
            {"name": "categoria", "label": "Categoria", "field": "categoria", "align": "left", "sortable": True},
            {"name": "empresa", "label": "Empresa", "field": "empresa", "align": "left", "sortable": True},
            {"name": "vencimento", "label": "Vencimento", "field": "vencimento", "align": "center", "sortable": True},
            {"name": "valor", "label": "Valor", "field": "valor", "align": "right", "sortable": True},
            {"name": "lembrete", "label": "Lembrete / Alerta", "field": "lembrete", "align": "center"},
            {"name": "status", "label": "Status Atual", "field": "status", "align": "center", "sortable": True},
            {"name": "acoes", "label": "Ações", "field": "acoes", "align": "center"},
        ]

        tabela = ui.table(
            columns=cols, 
            rows=montar_rows_filtrados(), 
            row_key="id"
        ).classes("w-full bg-white shadow-sm border border-slate-200 rounded-xl overflow-hidden")

        def aplicar_filtros():
            tabela.rows = montar_rows_filtrados()
            tabela.update()

        filtro_empresa.on_value_change(aplicar_filtros)
        filtro_categoria.on_value_change(aplicar_filtros)

        # SLOTS DE ESTILIZAÇÃO DA TABELA
        tabela.add_slot(
            "body-cell-categoria",
            """
            <q-td :props="props">
                <q-chip dense outline color="purple" icon="folder">
                    {{ props.row.categoria }}
                </q-chip>
            </q-td>
            """,
        )

        tabela.add_slot(
            "body-cell-lembrete",
            """
            <q-td :props="props">
                <template v-if="props.row.tem_lembrete">
                    <q-chip 
                        dense 
                        class="bg-purple-100 text-purple-900 font-bold" 
                        icon="notifications_active"
                    >
                        {{ props.row.lembrete }}
                    </q-chip>
                </template>
                <template v-else>
                    <span class="text-xs text-slate-400 italic">Desativado</span>
                </template>
            </q-td>
            """,
        )

        tabela.add_slot(
            "body-cell-status",
            """
            <q-td :props="props">
                <q-chip 
                    dense 
                    square
                    :color="props.row.status === 'PAGO' ? 'positive' : props.row.status === 'ATRASADO' ? 'negative' : props.row.status === 'CANCELADO' ? 'grey-7' : 'warning'"
                    text-color="white"
                    :icon="props.row.status === 'PAGO' ? 'check_circle' : props.row.status === 'ATRASADO' ? 'warning' : props.row.status === 'CANCELADO' ? 'block' : 'schedule'"
                >
                    {{ props.row.status }}
                </q-chip>
            </q-td>
            """,
        )

        # COLUNA DE AÇÕES COM EDIÇÃO COMPLETA
        tabela.add_slot(
            "body-cell-acoes",
            """
            <q-td :props="props" class="q-gutter-x-xs">
                <q-btn 
                    icon="edit" 
                    color="primary" 
                    flat 
                    round 
                    dense 
                    size="sm"
                    @click="$parent.$emit('abrir_edicao_boleto', props.row)"
                >
                    <q-tooltip>Editar Dados Completos</q-tooltip>
                </q-btn>

                <q-btn-dropdown size="sm" color="purple" label="Status" dense flat icon="swap_horiz">
                    <q-list>
                        <q-item clickable v-close-popup @click="$parent.$emit('atualizar_status', {id: props.row.id, status: 'PENDENTE'})">
                            <q-item-section avatar><q-icon name="schedule" color="warning" /></q-item-section>
                            <q-item-section><q-item-label>PENDENTE</q-item-label></q-item-section>
                        </q-item>
                        <q-item clickable v-close-popup @click="$parent.$emit('atualizar_status', {id: props.row.id, status: 'PAGO'})">
                            <q-item-section avatar><q-icon name="check_circle" color="positive" /></q-item-section>
                            <q-item-section><q-item-label>PAGO</q-item-label></q-item-section>
                        </q-item>
                        <q-item clickable v-close-popup @click="$parent.$emit('atualizar_status', {id: props.row.id, status: 'ATRASADO'})">
                            <q-item-section avatar><q-icon name="warning" color="negative" /></q-item-section>
                            <q-item-section><q-item-label>ATRASADO</q-item-label></q-item-section>
                        </q-item>
                        <q-item clickable v-close-popup @click="$parent.$emit('atualizar_status', {id: props.row.id, status: 'CANCELADO'})">
                            <q-item-section avatar><q-icon name="block" color="grey" /></q-item-section>
                            <q-item-section><q-item-label>CANCELADO</q-item-label></q-item-section>
                        </q-item>
                    </q-list>
                </q-btn-dropdown>

                <q-btn 
                    icon="delete" 
                    color="negative" 
                    flat 
                    round 
                    dense 
                    size="sm"
                    @click="$parent.$emit('deletar_boleto', {id: props.row.id})"
                >
                    <q-tooltip>Excluir Boleto</q-tooltip>
                </q-btn>
            </q-td>
            """,
        )

        # MODAL DE EDIÇÃO COMPLETA (EMPRESA, VALOR, VENCIMENTO E ALERTAS)
        def abrir_modal_edicao(e):
            row_data = e.args
            boleto_id = row_data.get("id")

            res_b = supabase.table("boletos").select("*").eq("id", boleto_id).execute()
            if not res_b.data:
                ui.notify("Boleto não encontrado.", color="negative")
                return
            
            b_atual = res_b.data[0]

            with ui.dialog() as dialog, ui.card().classes("w-full max-w-xl p-6 rounded-xl"):
                ui.label("✏️ Editar Boleto").classes("text-xl font-bold text-slate-800 mb-2")

                edit_empresa = ui.input("Empresa / Descrição", value=b_atual.get("empresa", "")).props("outlined dense").classes("w-full")
                
                with ui.row().classes("w-full gap-4 items-center"):
                    edit_valor = ui.number("Valor (R$)", value=float(b_atual.get("valor", 0.0)), format="%.2f").props("outlined dense").classes("flex-1")
                    edit_vencimento = ui.input("Vencimento", value=b_atual.get("data_vencimento", "")).props("type=date outlined dense").classes("flex-1")

                with ui.row().classes("w-full gap-4 items-center"):
                    edit_categoria = ui.select(categorias_list, value=b_atual.get("categoria_id"), label="Categoria").props("outlined dense").classes("flex-1")
                    edit_status = ui.select(["PENDENTE", "PAGO", "ATRASADO", "CANCELADO"], value=b_atual.get("status", "PENDENTE"), label="Status").props("outlined dense").classes("flex-1")

                edit_check_lembrete = ui.checkbox("Definir lembrete de vencimento", value=b_atual.get("tem_lembrete", False)).classes("mt-2 text-slate-700 font-medium")

                container_edit_lembrete = ui.column().classes("w-full p-4 bg-purple-50/50 border border-purple-100 rounded-lg gap-3 mt-2")
                container_edit_lembrete.bind_visibility_from(edit_check_lembrete, "value")

                with container_edit_lembrete:
                    ui.label("🔔 Configurações do Alerta").classes("text-xs font-bold text-purple-700 uppercase tracking-wider")
                    with ui.row().classes("w-full gap-4 items-center"):
                        edit_canal = ui.select(
                            ["SMS", "WhatsApp", "E-mail", "Todos"], 
                            value=b_atual.get("canal_lembrete") or "WhatsApp", 
                            label="Canal"
                        ).props("outlined dense bg-white").classes("flex-1")
                        
                        antecedencia_atual = b_atual.get("antecedencia_dias") if b_atual.get("antecedencia_dias") is not None else b_atual.get("dias_antecedencia", 1)
                        edit_antecedencia = ui.select(
                            {0: "No dia do vencimento", 1: "1 dia antes", 2: "2 dias antes", 3: "3 dias antes", 4: "4 dias antes", 5: "5 dias antes"},
                            value=antecedencia_atual,
                            label="Antecedência"
                        ).props("outlined dense bg-white").classes("flex-1")
                        
                        edit_horario = ui.input("Horário", value=b_atual.get("horario_lembrete") or "09:00").props("type=time outlined dense bg-white").classes("w-32")

                def salvar_alteracoes():
                    if not edit_empresa.value or not edit_valor.value or not edit_vencimento.value:
                        ui.notify("Preencha Empresa, Valor e Vencimento!", color="warning")
                        return

                    payload_update = {
                        "empresa": edit_empresa.value.strip(),
                        "valor": float(edit_valor.value),
                        "data_vencimento": edit_vencimento.value,
                        "categoria_id": edit_categoria.value,
                        "status": edit_status.value,
                        "tem_lembrete": edit_check_lembrete.value,
                    }

                    if edit_check_lembrete.value:
                        payload_update["canal_lembrete"] = edit_canal.value
                        payload_update["antecedencia_dias"] = edit_antecedencia.value
                        payload_update["horario_lembrete"] = edit_horario.value

                    try:
                        supabase.table("boletos").update(payload_update).eq("id", boleto_id).execute()
                        ui.notify("Boleto atualizado com sucesso!", color="positive")
                        dialog.close()
                        ui.navigate.reload()
                    except Exception as err:
                        if "antecedencia_dias" in str(err):
                            payload_update.pop("antecedencia_dias", None)
                            payload_update["dias_antecedencia"] = edit_antecedencia.value
                            try:
                                supabase.table("boletos").update(payload_update).eq("id", boleto_id).execute()
                                ui.notify("Boleto atualizado com sucesso!", color="positive")
                                dialog.close()
                                ui.navigate.reload()
                                return
                            except Exception as inner_err:
                                ui.notify(f"Erro ao atualizar: {inner_err}", color="negative")
                        else:
                            ui.notify(f"Erro ao atualizar boleto: {err}", color="negative")

                with ui.row().classes("w-full justify-end gap-2 mt-4"):
                    ui.button("Cancelar", on_click=dialog.close).props("flat color=grey")
                    ui.button("SALVAR ALTERAÇÕES", on_click=salvar_alteracoes).classes("bg-purple-700 text-white font-bold px-4 py-2 rounded-lg")

            dialog.open()

        def atualizar_status_boleto(e):
            boleto_id = e.args.get("id")
            novo_status = e.args.get("status")
            if boleto_id and novo_status:
                supabase.table("boletos").update({"status": novo_status}).eq("id", boleto_id).execute()
                ui.notify(f"Status alterado para {novo_status}!", color="positive")
                ui.navigate.reload()

        def deletar_boleto(e):
            boleto_id = e.args.get("id")
            if boleto_id:
                try:
                    supabase.table("boletos").delete().eq("id", boleto_id).execute()
                    ui.notify("Boleto excluído com sucesso!", color="positive")
                    ui.navigate.reload()
                except Exception as err:
                    ui.notify(f"Erro ao excluir boleto: {err}", color="negative")

        tabela.on("abrir_edicao_boleto", abrir_modal_edicao)
        tabela.on("atualizar_status", atualizar_status_boleto)
        tabela.on("deletar_boleto", deletar_boleto)


# ==========================================
# 3. PÁGINA DE PERFIL / CONFIGURAÇÃO DE CONTATO
# ==========================================

@ui.page("/perfil")
def perfil_page():
    if not app.storage.user.get("user_id"):
        ui.navigate.to("/login")
        return

    drawer = menu_drawer()
    cabecalho_app(drawer)
    user_id = app.storage.user.get("user_id")

    res = (
        supabase.table("perfis_usuarios")
        .select("*")
        .eq("id", user_id)
        .execute()
    )
    perfil = res.data[0] if res.data else {}

    with ui.column().classes("w-full max-w-xl mx-auto p-4 gap-6"):
        ui.label("👤 Meu Perfil e Contatos de Notificação").classes(
            "text-2xl font-bold text-slate-800"
        )

        with ui.card().classes("w-full p-6 border border-slate-200 bg-white shadow-sm rounded-xl gap-4"):
            ui.label("📱 Onde deseja receber seus alertas?").classes(
                "text-lg font-bold text-slate-700"
            )
            ui.label(
                "Estes dados serão utilizados para o envio automático de avisos antes do vencimento dos seus boletos."
            ).classes("text-sm text-slate-500 mb-2")

            input_email = (
                ui.input("E-mail para Alertas", value=perfil.get("email_notificacao") or perfil.get("email", ""))
                .props("outlined dense icon=email")
                .classes("w-full")
            )
            input_whatsapp = (
                ui.input("Celular / WhatsApp", value=perfil.get("whatsapp", ""))
                .props("outlined dense placeholder='(11) 99999-9999' icon=phone")
                .classes("w-full")
            )

            def salvar_perfil():
                supabase.table("perfis_usuarios").update({
                    "email_notificacao": input_email.value.strip() if input_email.value else "",
                    "whatsapp": input_whatsapp.value.strip() if input_whatsapp.value else "",
                }).eq("id", user_id).execute()
                ui.notify("Dados de contato atualizados com sucesso!", color="positive")
                ui.navigate.to("/")

            with ui.row().classes("w-full justify-end gap-2 mt-4"):
                ui.button("Voltar", on_click=lambda: ui.navigate.to("/")).props("flat color=grey")
                ui.button("SALVAR DADOS DE CONTATO", on_click=salvar_perfil).classes(
                    "bg-purple-700 hover:bg-purple-800 text-white font-bold px-6 py-2 rounded-lg"
                )


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