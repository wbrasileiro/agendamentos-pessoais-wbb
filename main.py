import asyncio
import logging
import os
import re
import smtplib
import subprocess
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from fastapi import Request
from fastapi.responses import RedirectResponse
from nicegui import app, ui
import requests
from supabase import Client, create_client

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

# Habilita transporte inseguro para testes locais em HTTP
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

# Silencia logs de aviso
logging.getLogger("asyncio").setLevel(logging.CRITICAL)

# Carrega configurações do .env
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL")
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASS = os.getenv("GMAIL_APP_PASS")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Configuração OAuth 2.0 Web do Google Calendar via .env
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

PORT = int(os.environ.get("PORT", 8080))
REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", f"http://localhost:{PORT}/oauth2callback")


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


# ==========================================
# MENU - LAYOUT BASE & NAVEGAÇÃO REESTRUTURADA
# ==========================================
def menu_drawer():
    user_email = app.storage.user.get("email", "")

    with ui.left_drawer(value=False).classes(
        "bg-slate-50 text-slate-800 p-0 flex flex-col justify-between w-72 border-r border-slate-200 shadow-xl"
    ) as drawer:

        def navegar(rota):
            drawer.hide()
            ui.navigate.to(rota)

        # TOPO DO MENU - PERFIL E HEADER
        with ui.column().classes("w-full p-5 border-b border-slate-200 bg-white gap-2"):
            with ui.row().classes("items-center gap-3"):
                with ui.element("div").classes("p-2 bg-blue-100 rounded-xl text-blue-700 flex items-center justify-center"):
                    ui.icon("space_dashboard", size="24px")
                with ui.column().classes("gap-0"):
                    ui.label("Sistema de Gestão").classes("text-base font-black text-slate-900 leading-tight")
                    ui.label("Painel Pessoal").classes("text-xs font-semibold text-slate-500")

            with ui.row().classes("items-center gap-2 mt-1 bg-slate-100 px-3 py-1.5 rounded-lg border border-slate-200 w-full"):
                ui.icon("account_circle", size="18px").classes("text-slate-500")
                ui.label(user_email if user_email else "Minha Conta").classes(
                    "text-xs font-bold text-slate-700 truncate flex-1"
                )

        # CORPO DO MENU - SEÇÕES REESTRUTURADAS
        with ui.column().classes("w-full p-4 gap-4 flex-1 overflow-y-auto"):
            
            # --- SEÇÃO 1: LANÇAMENTOS ---
            with ui.column().classes("w-full gap-1"):
                ui.label("LANÇAMENTOS").classes("text-[11px] font-black tracking-wider text-slate-400 px-3 my-1 uppercase")
                
                with ui.button(on_click=lambda: navegar("/dashboard")).props("flat no-caps align=left").classes(
                    "w-full hover:bg-blue-50 text-slate-700 hover:text-blue-700 rounded-xl py-2 px-3 transition-all"
                ):
                    with ui.row().classes("items-center gap-3 w-full"):
                        ui.icon("payments", size="20px").classes("text-blue-600")
                        ui.label("Gestão Financeira").classes("font-bold text-sm")

                with ui.button(on_click=lambda: navegar("/")).props("flat no-caps align=left").classes(
                    "w-full hover:bg-blue-50 text-slate-700 hover:text-blue-700 rounded-xl py-2 px-3 transition-all"
                ):
                    with ui.row().classes("items-center gap-3 w-full"):
                        ui.icon("add_card", size="20px").classes("text-blue-600")
                        ui.label("Cadastro").classes("font-bold text-sm")

                # --- SEÇÃO 2: UTILITÁRIOS ---
                with ui.column().classes("w-full gap-1"):
                    ui.label("UTILITÁRIOS").classes("text-[11px] font-black tracking-wider text-slate-400 px-3 my-1 uppercase")
                    
                    # Lembretes
                    with ui.button(on_click=lambda: navegar("/lembretes")).props("flat no-caps align=left").classes(
                        "w-full hover:bg-purple-50 text-slate-700 hover:text-purple-700 rounded-xl py-2 px-3 transition-all"
                    ):
                        with ui.row().classes("items-center gap-3 w-full"):
                            ui.icon("notifications_active", size="20px").classes("text-purple-600")
                            ui.label("Lembretes").classes("font-bold text-sm")

                # Tarefas
                    with ui.button(on_click=lambda: navegar("/tarefas")).props("flat no-caps align=left").classes(
                        "w-full hover:bg-purple-50 text-slate-700 hover:text-purple-700 rounded-xl py-2 px-3 transition-all"
                    ):
                        with ui.row().classes("items-center gap-3 w-full"):
                            ui.icon("check_box", size="20px").classes("text-purple-600")
                            ui.label("Tarefas").classes("font-bold text-sm")

                # Anotações
                    with ui.button(on_click=lambda: navegar("/anotacoes")).props("flat no-caps align=left").classes(
                        "w-full hover:bg-purple-50 text-slate-700 hover:text-purple-700 rounded-xl py-2 px-3 transition-all"
                    ):
                        with ui.row().classes("items-center gap-3 w-full"):
                            ui.icon("sticky_note_2", size="20px").classes("text-purple-600")
                            ui.label("Anotações").classes("font-bold text-sm")

            # --- SEÇÃO 3: ADMINISTRAÇÃO ---
            if app.storage.user.get("is_admin", False) or user_email == ADMIN_EMAIL:
                with ui.column().classes("w-full gap-1"):
                    ui.label("ADMINISTRAÇÃO").classes("text-[11px] font-black tracking-wider text-amber-600 px-3 my-1 uppercase")
                    
                    with ui.button(on_click=lambda: navegar("/admin")).props("flat no-caps align=left").classes(
                        "w-full hover:bg-amber-100/60 text-slate-700 hover:text-amber-900 rounded-xl py-2 px-3 transition-all"
                    ):
                        with ui.row().classes("items-center gap-3 w-full"):
                            ui.icon("admin_panel_settings", size="20px").classes("text-amber-600")
                            ui.label("Manutenção (Admin)").classes("font-bold text-sm")

        # RODAPÉ DO MENU - SAIR DA CONTA
        with ui.column().classes("w-full p-4 border-t border-slate-200 bg-white"):
            with ui.button(
                on_click=lambda: (
                    drawer.hide(),
                    app.storage.user.clear(),
                    ui.navigate.to("/login"),
                )
            ).props("flat no-caps align=left").classes(
                "w-full bg-red-50 hover:bg-red-100 text-red-700 rounded-xl py-2.5 px-3 transition-all border border-red-200"
            ):
                with ui.row().classes("items-center gap-3 w-full justify-center"):
                    ui.icon("logout", size="20px").classes("text-red-600")
                    ui.label("Sair da conta").classes("font-black text-sm")

    return drawer


def cabecalho_app(drawer):
    user_email = app.storage.user.get("email", "Usuário")
    with ui.header().classes(
        "bg-slate-900 text-white justify-between items-center px-4 py-2.5 w-full shadow-md"
    ):
        with ui.row().classes("items-center gap-2"):
            ui.button(icon="menu", on_click=drawer.toggle).props("flat round color=white")
            ui.label("Gestão Integrada").classes("text-lg font-black tracking-tight")
        
        with ui.row().classes("items-center gap-2 bg-slate-800/80 px-3 py-1 rounded-xl border border-slate-700"):
            ui.icon("person", size="18px").classes("text-slate-300")
            ui.label(user_email.split("@")[0]).classes(
                "text-xs font-bold text-slate-200"
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
                #ui.navigate.to("/")
                ui.navigate.to("/dashboard")
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

# Dicionário em memória para armazenar os tokens das sessões
user_tokens = {}


def obter_flow(state=None):
    """Cria a instância do Flow OAuth 2.0 carregando credenciais diretamente do .env."""
    client_config = {
        "web": {
            "client_id": os.getenv("GOOGLE_CLIENT_ID"),
            "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [REDIRECT_URI],
        }
    }
    return Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        state=state,
        redirect_uri=REDIRECT_URI
    )


# --- ROTA CALLBACK DO OAUTH WEB ---
@app.get("/oauth2callback")
def oauth2callback(request: Request):
    """Endpoint chamado pelo Google após a autorização do usuário."""
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    
    if code:
        code_verifier = app.storage.user.get("code_verifier")
        flow = obter_flow(state=state)
        
        # Injeta o code_verifier salvo no objeto do flow e da sessão interna
        if code_verifier and hasattr(flow, 'code_verifier'):
            flow.code_verifier = code_verifier
            if hasattr(flow, 'oauth2session') and flow.oauth2session:
                flow.oauth2session.code_verifier = code_verifier

        try:
            # Troca o código pelo Token de acesso
            flow.fetch_token(code=code)
            creds = flow.credentials

            # Salva o token vinculado ao usuário atual na sessão
            user_id = app.storage.user.get("user_id", "default_user")
            user_tokens[user_id] = {
                "token": creds.token,
                "refresh_token": creds.refresh_token,
                "token_uri": creds.token_uri,
                "client_id": creds.client_id,
                "client_secret": creds.client_secret,
                "scopes": creds.scopes,
            }
        except Exception as e:
            print(f"❌ Erro ao autenticar no Google: {e}")
            return RedirectResponse(url="/?auth=error")

    # Recupera a rota para onde deve voltar (padrão '/' caso não exista)
        next_url = app.storage.user.get("next_url", "/")
        
        # Limpa a variável da sessão após o uso
        app.storage.user.pop("next_url", None)

        # Redireciona dinamicamente para a página de origem (ex: /lembretes)
        return RedirectResponse(url=f"{next_url}?auth=success")


def obter_credenciais_usuario(user_id: str = None):
    """Recupera e valida as credenciais salvas na sessão."""
    if not user_id:
        user_id = app.storage.user.get("user_id", "default_user")
    
    creds_data = user_tokens.get(user_id)

    if not creds_data:
        return None

    return Credentials(**creds_data)


def criar_evento_google_calendar_oauth(
    user_id: str,
    empresa: str,
    valor: float,
    data_vencimento: str,
    antecedencia_dias: int,
    horario: str,
):
    """Cria evento diretamente no Google Calendar na data/hora do lembrete."""
    try:
        creds = obter_credenciais_usuario(user_id)
        if not creds:
            raise Exception("Usuário não autenticado no Google Calendar. Clique no botão de conexão.")

        service = build("calendar", "v3", credentials=creds)

        data_venc = datetime.strptime(data_vencimento, "%Y-%m-%d")
        dt_lembrete = data_venc - timedelta(days=antecedencia_dias)
        hora, minuto = map(int, horario.split(":"))

        dt_inicio = dt_lembrete.replace(
            hour=hora, minute=minuto, tzinfo=ZoneInfo("America/Sao_Paulo")
        )
        dt_fim = dt_inicio + timedelta(hours=1)

        event = {
            "summary": f"⏰ Lembrete: Vencimento {empresa}",
            "description": f"Alerta do item cadastrado como {empresa} no valor de R$ {valor:.2f}.\nVencimento: {data_venc.strftime('%d/%m/%Y')}",
            "start": {
                "dateTime": dt_inicio.isoformat(),
                "timeZone": "America/Sao_Paulo",
            },
            "end": {
                "dateTime": dt_fim.isoformat(),
                "timeZone": "America/Sao_Paulo",
            },
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "popup", "minutes": 0},
                    {"method": "email", "minutes": 0},
                ],
            },
        }

        evento_criado = service.events().insert(calendarId="primary", body=event).execute()
        print(f"✅ Evento cadastrado via OAuth 2.0 Web! ID: {evento_criado.get('id')}")
        return True, evento_criado.get("id")
    except Exception as e:
        print(f"❌ Erro ao criar evento no Calendar: {e}")
        raise e


# --- FUNÇÕES AUXILIARES ---
def obter_hora_brasilia():
    return datetime.now(ZoneInfo("America/Sao_Paulo"))


def email_valido(email_str: str) -> bool:
    return bool(re.match(r"^[\w\.-]+@[\w\.-]+\.\w+$", email_str.strip()))


def formatar_br(valor) -> str:
    try:
        val = float(valor or 0)
        return f"{val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (ValueError, TypeError):
        return "0,00"


def formatar_data_br(data_str: str) -> str:
    if not data_str:
        return ""
    try:
        return datetime.strptime(data_str[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
    except Exception:
        return data_str


def validar_telefone(telefone: str) -> tuple[bool, str]:
    tel_limpo = telefone.strip() if telefone else ""
    if not tel_limpo:
        return False, "O número de telefone/WhatsApp é obrigatório."
    if not tel_limpo.isdigit():
        return False, "O telefone deve conter apenas números (sem traços ou parênteses)."
    if len(tel_limpo) < 10 or len(tel_limpo) > 11:
        return False, "O telefone deve conter DDD + número (10 ou 11 dígitos)."
    return True, ""


def _enviar_email_worker(solicitante_email, telefone, dispositivo, localizacao):
    try:
        data_hora_br = obter_hora_brasilia().strftime("%d/%m/%Y às %H:%M:%S")
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"Agendamentos Pessoais - Solicitação: {solicitante_email}"
        msg["From"] = f"Agendamentos Pessoais <{GMAIL_USER}>"
        msg["To"] = ADMIN_EMAIL

        corpo_html = f"""
        <html>
          <body style="font-family: Arial, sans-serif; padding: 20px;">
            <h2>🔔 Nova Solicitação de Acesso</h2>
            <ul>
              <li><b>E-mail:</b> {solicitante_email}</li>
              <li><b>Telefone:</b> {telefone}</li>
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


def enviar_notificacao_email(solicitante_email, telefone, dispositivo, localizacao):
    threading.Thread(
        target=_enviar_email_worker,
        args=(solicitante_email, telefone, dispositivo, localizacao),
        daemon=True,
    ).start()

# Função utilizada na tela de lembretes
def criar_evento_lembrete_google_calendar(
    user_id: str,
    titulo: str,
    descricao: str,
    data_compromisso: str,
    antecedencia_dias: int,
    horario: str,
):
    """Cria evento de lembrete no Google Calendar na data/hora configurada."""
    try:
        creds = obter_credenciais_usuario(user_id)
        if not creds:
            raise Exception("Usuário não autenticado no Google Calendar.")

        service = build("calendar", "v3", credentials=creds)

        dt_comp = datetime.strptime(data_compromisso, "%Y-%m-%d")
        dt_lembrete = dt_comp - timedelta(days=antecedencia_dias)
        hora, minuto = map(int, horario.split(":"))

        dt_inicio = dt_lembrete.replace(
            hour=hora, minute=minuto, tzinfo=ZoneInfo("America/Sao_Paulo")
        )
        dt_fim = dt_inicio + timedelta(hours=1)

        corpo_desc = descricao if descricao else "Lembrete agendado pelo sistema."

        event = {
            "summary": f"⏰ Lembrete: {titulo}",
            "description": f"{corpo_desc}\nData do compromisso: {dt_comp.strftime('%d/%m/%Y')}",
            "start": {
                "dateTime": dt_inicio.isoformat(),
                "timeZone": "America/Sao_Paulo",
            },
            "end": {
                "dateTime": dt_fim.isoformat(),
                "timeZone": "America/Sao_Paulo",
            },
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "popup", "minutes": 0},
                    {"method": "email", "minutes": 0},
                ],
            },
        }

        evento_criado = service.events().insert(calendarId="primary", body=event).execute()
        return True, evento_criado.get("id")
    except Exception as e:
        print(f"❌ Erro ao criar lembrete no Calendar: {e}")
        raise e    

# ==========================================
# 1. TELA DE GESTÃO FINANCEIRA E CONTAS (REATIVIDADE & VISUAL ACESSÍVEL)
# ==========================================
@ui.page("/dashboard")
def dashboard_page():
    if not app.storage.user.get("user_id"):
        ui.navigate.to("/login")
        return

    drawer = menu_drawer()
    cabecalho_app(drawer)
    user_id = app.storage.user.get("user_id")

    cats_res = supabase.table("dim_categorias").select("*").order("nome").execute()
    categorias_data = cats_res.data or []
    categorias_list = {c["id"]: c["nome"] for c in categorias_data}

    def carregar_dados():
        res = supabase.table("boletos").select("*").eq("user_id", user_id).order("data_vencimento").execute()
        dados = res.data or []
        hoje = datetime.now().date()

        for item in dados:
            st = str(item.get("status") or "PENDENTE").strip().upper()
            
            # Recálculo automático de atrasados ao carregar os dados
            if st == "PENDENTE" and item.get("data_vencimento"):
                try:
                    dt_venc = datetime.strptime(item["data_vencimento"], "%Y-%m-%d").date()
                    if dt_venc < hoje:
                        st = "ATRASADO"
                except Exception:
                    pass

            item["status_norm"] = st
        return dados

    # Variáveis Globais da Tela
    filtro_status = {"valor": "TODOS"}
    filtro_categoria = {"valor": "TODAS"}

    with ui.column().classes("w-full max-w-6xl mx-auto p-4 gap-6 pb-32"):
        ui.label("📊 Gestão Financeira").classes("text-3xl font-black text-slate-800 tracking-tight")

        # 1. Filtros por Categoria no Topo
        with ui.element("div").classes("w-full bg-slate-50 p-4 rounded-2xl border border-slate-200 shadow-sm flex flex-col gap-2"):
            ui.label("Filtrar por Categoria:").classes("text-xs font-bold text-slate-500 uppercase tracking-wider")
            container_botoes_cat = ui.row().classes("w-full gap-2 flex-wrap items-center")

        # Containers reativos para Big Numbers e Gráficos
        container_bignumbers = ui.row().classes("w-full grid grid-cols-1 sm:grid-cols-3 gap-4")
        container_graficos = ui.column().classes("w-full gap-4")

        # 2. Filtros de Pesquisa Recolhidos
        with ui.expansion("🔍 Clique aqui para filtrar registros", icon="filter_alt").classes("w-full border border-slate-300 bg-slate-50 rounded-2xl text-base font-bold text-slate-700 p-2 shadow-sm"):
            with ui.column().classes("w-full p-2 gap-3"):
                with ui.grid().classes("w-full grid-cols-1 sm:grid-cols-3 gap-3"):
                    input_busca = ui.input("Empresa / Descrição", placeholder="Ex: Boticário, Eudora...").props("outlined bg-white text-base")
                    input_v_min = ui.number("Valor Mínimo (R$)", format="%.2f").props("outlined bg-white text-base")
                    input_v_max = ui.number("Valor Máximo (R$)", format="%.2f").props("outlined bg-white text-base")

                with ui.grid().classes("w-full grid-cols-1 sm:grid-cols-2 gap-3"):
                    input_dt_ini = ui.input("Vencimento De").props("type=date outlined bg-white text-base")
                    input_dt_fim = ui.input("Vencimento Até").props("type=date outlined bg-white text-base")

        # 3. Botões de Filtro Congelados no Topo (Sticky Header) por Situação
        with ui.element("div").classes("w-full sticky top-0 z-20 bg-slate-100/90 backdrop-blur-md py-3 px-1 border-b border-slate-200"):
            ui.label("Filtrar por situação:").classes("text-xs font-bold text-slate-500 uppercase tracking-wider mb-1")
            container_botoes_status = ui.row().classes("w-full justify-start gap-2 flex-wrap items-center")
            container_feedback_filtro = ui.column().classes("w-full mt-2")

        container_cards = ui.column().classes("w-full gap-4 mt-2")

        # Renderização dos Botões de Categoria
        def renderizar_botoes_categoria():
            container_botoes_cat.clear()
            cat_ativa = filtro_categoria["valor"]

            with container_botoes_cat:
                def set_cat_filtro(cid):
                    filtro_categoria["valor"] = cid
                    renderizar_botoes_categoria()
                    renderizar_cards()

                bg_todas = "bg-indigo-600 text-white shadow-md font-extrabold" if cat_ativa == "TODAS" else "bg-white text-indigo border border-slate-300 hover:bg-slate-100"
                ui.button("Todas", on_click=lambda: set_cat_filtro("TODAS")).classes(f"text-sm px-4 py-2 rounded-xl transition-all {bg_todas}")

                for cat in categorias_data:
                    cid = cat["id"]
                    cnome = cat["nome"]
                    bg_cat = "bg-indigo-600 text-white shadow-md font-extrabold" if cat_ativa == cid else "bg-white text-indigo border border-slate-300 hover:bg-slate-100"
                    ui.button(cnome, on_click=lambda c=cid: set_cat_filtro(c)).classes(f"text-sm px-4 py-2 rounded-xl transition-all {bg_cat}")

        # Renderização dos Botões de Situação com Destaque Visual
        def renderizar_botoes_status(qtd_retornada=0):
            container_botoes_status.clear()
            container_feedback_filtro.clear()
            st_ativo = filtro_status["valor"]

            def set_status_filtro(st):
                filtro_status["valor"] = st
                renderizar_cards()

            status_config = [
                ("TODOS", "Todos", "bg-slate-800", "text-slate-800 bg-slate-100 border-slate-300"),
                ("PENDENTE", "Pendentes", "bg-amber-600", "text-amber-900 bg-amber-100 border-amber-300"),
                ("PAGO", "Pagos", "bg-green-700", "text-green-900 bg-green-100 border-green-300"),
                ("ATRASADO", "Atrasados", "bg-red-700", "text-red-900 bg-red-100 border-red-300"),
                ("CANCELADO", "Cancelados", "bg-gray-700", "text-gray-900 bg-gray-200 border-gray-300"),
            ]

            with container_botoes_status:
                for chave, label, cor_ativa, _ in status_config:
                    if st_ativo == chave:
                        estilo = f"{cor_ativa} text-white font-black shadow-md ring-2 ring-offset-1 ring-slate-400 scale-105"
                    else:
                        estilo = "bg-white text-indigo border border-slate-300 hover:bg-slate-100 font-semibold"
                    
                    ui.button(label, on_click=lambda s=chave: set_status_filtro(s)).classes(f"text-sm px-4 py-2 rounded-xl transition-all {estilo}")

            with container_feedback_filtro:
                nome_st = next((lbl for ch, lbl, _, _ in status_config if ch == st_ativo), st_ativo)
                cor_feedback = next((fdb for ch, _, _, fdb in status_config if ch == st_ativo), "text-slate-800 bg-slate-100 border-slate-300")
                
                cat_nome_feedback = ""
                if filtro_categoria["valor"] != "TODAS":
                    cat_nome_feedback = f" | Categoria: {categorias_list.get(filtro_categoria['valor'], '')}"

                ui.label(
                    f"📌 Filtro '{nome_st}' aplicado{cat_nome_feedback} • {qtd_retornada} item(ns) encontrado(s)."
                ).classes(f"text-xs sm:text-sm font-bold px-3 py-1.5 rounded-lg border w-fit shadow-xs {cor_feedback}")

        # Recálculo dos Big Numbers e Gráficos (Respeita Filtro de Categoria)
        def atualizar_dashboard(dados_base):
            container_bignumbers.clear()
            container_graficos.clear()
            hoje = datetime.now().date()

            # Filtra por categoria para os Big Numbers e Gráficos
            cat_f = filtro_categoria["valor"]
            boletos_dados = [b for b in dados_base if cat_f == "TODAS" or b.get("categoria_id") == cat_f]

            total_a_vencer = sum(float(b.get("valor", 0)) for b in boletos_dados if b["status_norm"] == "PENDENTE" and datetime.strptime(b["data_vencimento"], "%Y-%m-%d").date() >= hoje)
            cnt_a_vencer = sum(1 for b in boletos_dados if b["status_norm"] == "PENDENTE" and datetime.strptime(b["data_vencimento"], "%Y-%m-%d").date() >= hoje)

            total_atrasados = sum(float(b.get("valor", 0)) for b in boletos_dados if b["status_norm"] == "ATRASADO" or (b["status_norm"] == "PENDENTE" and datetime.strptime(b["data_vencimento"], "%Y-%m-%d").date() < hoje))
            cnt_atrasados = sum(1 for b in boletos_dados if b["status_norm"] == "ATRASADO" or (b["status_norm"] == "PENDENTE" and datetime.strptime(b["data_vencimento"], "%Y-%m-%d").date() < hoje))

            total_pagos = sum(float(b.get("valor", 0)) for b in boletos_dados if b["status_norm"] == "PAGO")
            cnt_pagos = sum(1 for b in boletos_dados if b["status_norm"] == "PAGO")

            # Renderiza Big Numbers
            with container_bignumbers:
                with ui.card().classes("p-5 bg-blue-50 border-2 border-blue-200 rounded-2xl shadow-sm w-full"):
                    ui.label("Contas a Vencer").classes("text-sm font-bold text-blue-900 uppercase tracking-wider")
                    ui.label(f"R$ {formatar_br(total_a_vencer)}").classes("text-3xl font-black text-blue-900 my-1")
                    ui.label(f"Quantidade: {cnt_a_vencer}").classes("text-sm text-blue-800 font-semibold")

                with ui.card().classes("p-5 bg-red-50 border-2 border-red-200 rounded-2xl shadow-sm w-full"):
                    ui.label("Contas Atrasadas").classes("text-sm font-bold text-red-900 uppercase tracking-wider")
                    ui.label(f"R$ {formatar_br(total_atrasados)}").classes("text-3xl font-black text-red-900 my-1")
                    ui.label(f"Quantidade: {cnt_atrasados}").classes("text-sm text-red-800 font-semibold")

                with ui.card().classes("p-5 bg-green-50 border-2 border-green-200 rounded-2xl shadow-sm w-full"):
                    ui.label("Contas Pagas").classes("text-sm font-bold text-green-900 uppercase tracking-wider")
                    ui.label(f"R$ {formatar_br(total_pagos)}").classes("text-3xl font-black text-green-900 my-1")
                    ui.label(f"Quantidade: {cnt_pagos}").classes("text-sm text-green-800 font-semibold")

            # Renderiza Gráficos Mês a Mês
            meses_dict = {}
            for b in boletos_dados:
                if b["status_norm"] == "PENDENTE":
                    try:
                        dt = datetime.strptime(b["data_vencimento"], "%Y-%m-%d")
                        chave_mes = dt.strftime("%m/%Y")
                        if chave_mes not in meses_dict:
                            meses_dict[chave_mes] = {"qtd": 0, "valor": 0.0}
                        meses_dict[chave_mes]["qtd"] += 1
                        meses_dict[chave_mes]["valor"] += float(b.get("valor", 0))
                    except Exception:
                        pass

            meses_ordenados = sorted(meses_dict.keys(), key=lambda x: datetime.strptime(x, "%m/%Y"))
            qtdes_pendentes = [meses_dict[m]["qtd"] for m in meses_ordenados]
            valores_pendentes = [round(meses_dict[m]["valor"], 2) for m in meses_ordenados]

            if meses_ordenados:
                with container_graficos:
                    ui.label("📈 Evolução Mensal de Pendências").classes("text-xl font-bold text-slate-800 mt-2")
                    with ui.grid().classes("w-full grid-cols-1 md:grid-cols-2 gap-4"):
                        with ui.card().classes("w-full p-4 border border-slate-200 rounded-2xl bg-white shadow-sm"):
                            ui.label("Quantidade de Pendências").classes("text-base font-bold text-slate-700")
                            ui.echart({
                                "xAxis": {"type": "category", "data": meses_ordenados},
                                "yAxis": {"type": "value"},
                                "series": [{"data": qtdes_pendentes, "type": "bar", "color": "#f59e0b", "barWidth": "40%"}],
                                "tooltip": {"trigger": "axis"}
                            }).classes("h-64 w-full")

                        with ui.card().classes("w-full p-4 border border-slate-200 rounded-2xl bg-white shadow-sm"):
                            ui.label("Valor Total Pendente (R$)").classes("text-base font-bold text-slate-700")
                            # Alterado de área para gráfico de barras
                            ui.echart({
                                "xAxis": {"type": "category", "data": meses_ordenados},
                                "yAxis": {"type": "value"},
                                "series": [{"data": valores_pendentes, "type": "bar", "color": "#d97706", "barWidth": "40%"}],
                                "tooltip": {"trigger": "axis"}
                            }).classes("h-64 w-full")

        # Função de Renderização Geral dos Cards e Atualização da Tela
        def renderizar_cards():
            container_cards.clear()
            dados = carregar_dados()

            # Recalcula e atualiza topo e gráficos
            atualizar_dashboard(dados)

            # Aplicação dos filtros
            txt = (input_busca.value or "").strip().lower()
            vmin = input_v_min.value
            vmax = input_v_max.value
            dt_i = input_dt_ini.value
            dt_f = input_dt_fim.value
            st_filtro = filtro_status["valor"]
            cat_filtro = filtro_categoria["valor"]

            filtrados = []
            for b in dados:
                match_txt = txt in b.get("empresa", "").lower()
                val = float(b.get("valor", 0))
                match_vmin = vmin is None or val >= float(vmin)
                match_vmax = vmax is None or val <= float(vmax)
                
                dt_b = b.get("data_vencimento", "")
                match_dti = not dt_i or dt_b >= dt_i
                match_dtf = not dt_f or dt_b <= dt_f

                st = b.get("status_norm", "PENDENTE")
                match_st = (st_filtro == "TODOS") or (st == st_filtro)

                match_cat = (cat_filtro == "TODAS") or (b.get("categoria_id") == cat_filtro)

                if match_txt and match_vmin and match_vmax and match_dti and match_dtf and match_st and match_cat:
                    filtrados.append(b)

            renderizar_botoes_status(len(filtrados))

            with container_cards:
                if not filtrados:
                    ui.label("Nenhum registro encontrado.").classes("text-slate-500 text-base italic py-6 text-center w-full bg-white rounded-2xl border border-slate-200")
                    return

                for b in filtrados:
                    boleto_id = b["id"]
                    status_norm = b.get("status_norm", "PENDENTE")
                    
                    # Garantir que a exibição padrão evite opções inválidas
                    status_exibicao = "Pendente" if status_norm == "ATRASADO" else status_norm.capitalize()

                    # Definição visual por status
                    if status_norm == "PAGO":
                        border_color = "border-l-8 border-l-green-600"
                        badge_bg = "bg-green-100 text-green-900 border-green-300"
                        status_icon = "check_circle"
                        status_texto = "Pago"
                    elif status_norm == "ATRASADO":
                        border_color = "border-l-8 border-l-red-600"
                        badge_bg = "bg-red-100 text-red-900 border-red-300"
                        status_icon = "warning"
                        status_texto = "Atrasado"
                    elif status_norm == "CANCELADO":
                        border_color = "border-l-8 border-l-gray-500"
                        badge_bg = "bg-gray-200 text-gray-800 border-gray-300"
                        status_icon = "cancel"
                        status_texto = "Cancelado"
                    else:
                        border_color = "border-l-8 border-l-amber-500"
                        badge_bg = "bg-amber-100 text-amber-900 border-amber-300"
                        status_icon = "schedule"
                        status_texto = "Pendente"

                    # Lembrete
                    info_alerta = "Sem lembrete ativo"
                    if b.get("tem_lembrete"):
                        ant = b.get("antecedencia_dias", 0)
                        hor = b.get("horario_lembrete", "00:00")
                        try:
                            dt_venc = datetime.strptime(b["data_vencimento"], "%Y-%m-%d")
                            dt_alerta = dt_venc - timedelta(days=ant)
                            info_alerta = f"🔔 Alerta em: {dt_alerta.strftime('%d/%m/%Y')} às {hor}"
                        except Exception:
                            info_alerta = f"🔔 Alerta: {ant} dia(s) antes às {hor}"

                    # Layout do Card
                    with ui.card().classes(f"w-full p-5 bg-white border border-slate-200 rounded-2xl shadow-sm flex-col gap-3 {border_color}"):
                        
                        # Topo do Card
                        with ui.row().classes("w-full justify-between items-center"):
                            with ui.row().classes(f"items-center gap-1.5 px-3 py-1 rounded-xl border text-sm font-black {badge_bg}"):
                                ui.icon(status_icon, size="20px")
                                ui.label(f"Situação: {status_texto}")

                            ui.label(info_alerta).classes("text-xs text-purple-900 font-bold bg-purple-50 px-3 py-1 rounded-lg border border-purple-200")

                        ui.separator().classes("my-0.5")

                        with ui.row().classes("w-full justify-between items-start gap-2"):
                            with ui.column().classes("gap-1 flex-1"):
                                ui.label(b.get("empresa")).classes("font-black text-2xl text-slate-900")
                                cat_nome = categorias_list.get(b.get("categoria_id"), "Sem Categoria")
                                ui.label(f"📁 Categoria: {cat_nome}").classes("text-base text-slate-700 font-semibold")

                            with ui.column().classes("items-end gap-1"):
                                ui.label(f"R$ {formatar_br(b.get('valor'))}").classes("font-black text-2xl text-slate-900")
                                ui.label(f"🗓 Vencimento: {formatar_data_br(b.get('data_vencimento'))}").classes("text-base font-extrabold text-slate-800")

                        ui.separator().classes("my-0.5")

                        # Rodapé do Card: Alteração de Status (Sem 'Atrasado' como opção manual)
                        with ui.row().classes("w-full justify-between items-center gap-3 flex-wrap"):
                            with ui.row().classes("items-center gap-2"):
                                ui.label("Alterar para:").classes("text-sm font-bold text-slate-700")

                                def atualizar_status(e, bid=boleto_id):
                                    val_salvar = str(e.value).upper()
                                    supabase.table("boletos").update({"status": val_salvar}).eq("id", bid).execute()
                                    ui.notify(f"Situação alterada para {e.value}!", color="positive")
                                    renderizar_cards()

                                # Opção 'Atrasado' removida das seleções manuais
                                ui.select(
                                    ["Pendente", "Pago", "Cancelado"],
                                    value=status_exibicao if status_exibicao in ["Pendente", "Pago", "Cancelado"] else "Pendente",
                                    on_change=atualizar_status
                                ).props("dense outlined text-base").classes("w-40 font-bold")

                            with ui.row().classes("items-center gap-1"):
                                def abrir_modal_edicao(boleto=b):
                                    with ui.dialog() as dlg, ui.card().classes("w-full max-w-md p-6 gap-4 rounded-2xl"):
                                        ui.label("Editar Registro").classes("text-xl font-bold text-slate-800")
                                        e_emp = ui.input("Empresa / Descrição", value=boleto["empresa"]).props("outlined text-base")
                                        e_val = ui.number("Valor (R$)", value=boleto["valor"], format="%.2f").props("outlined text-base")
                                        e_venc = ui.input("Vencimento", value=boleto["data_vencimento"]).props("type=date outlined text-base")

                                        def salvar_edicao():
                                            supabase.table("boletos").update({
                                                "empresa": e_emp.value,
                                                "valor": float(e_val.value),
                                                "data_vencimento": e_venc.value,
                                            }).eq("id", boleto["id"]).execute()
                                            dlg.close()
                                            ui.notify("Registro alterado com sucesso!", color="positive")
                                            renderizar_cards()

                                        with ui.row().classes("w-full justify-end gap-3 mt-2"):
                                            ui.button("Cancelar", on_click=dlg.close).props("flat text-base")
                                            ui.button("Salvar", on_click=salvar_edicao).classes("bg-blue-600 text-white font-bold px-4 py-2 rounded-xl")
                                    dlg.open()

                                def confirmar_exclusao(bid=boleto_id, nome=b.get("empresa")):
                                    with ui.dialog() as dlg_del, ui.card().classes("p-6 max-w-sm gap-4 rounded-2xl"):
                                        ui.label("Confirmar Exclusão").classes("text-xl font-bold text-red-700")
                                        ui.label(f"Deseja realmente apagar o registro de '{nome}'?").classes("text-base text-slate-700")

                                        def excluir():
                                            supabase.table("boletos").delete().eq("id", bid).execute()
                                            dlg_del.close()
                                            ui.notify("Registro excluído!", color="warning")
                                            renderizar_cards()

                                        with ui.row().classes("w-full justify-end gap-3 mt-2"):
                                            ui.button("Cancelar", on_click=dlg_del.close).props("flat text-base")
                                            ui.button("Excluir", on_click=excluir).classes("bg-red-600 text-white font-bold px-4 py-2 rounded-xl")
                                    dlg_del.open()

                                ui.button("Editar", icon="edit", on_click=abrir_modal_edicao).props("flat color=primary text-base")
                                ui.button("Excluir", icon="delete", on_click=confirmar_exclusao).props("flat color=negative text-base")

        # Eventos para Reatividade nos Filtros
        input_busca.on("update:model-value", renderizar_cards)
        input_v_min.on("update:model-value", renderizar_cards)
        input_v_max.on("update:model-value", renderizar_cards)
        input_dt_ini.on("update:model-value", renderizar_cards)
        input_dt_fim.on("update:model-value", renderizar_cards)

        # Renderização Inicial
        renderizar_botoes_categoria()
        renderizar_cards()


# ==========================================
# 2. TELA DE CADASTRO
# ==========================================
@ui.page("/")
def home_page():
    if not app.storage.user.get("user_id"):
        ui.navigate.to("/login")
        return

    drawer = menu_drawer()
    cabecalho_app(drawer)
    user_id = app.storage.user.get("user_id")

    # Busca dinamicamente todas as categorias do banco de dados
    opcoes_categorias = {}
    categoria_padrao_id = None

    try:
        cats_res = supabase.table("dim_categorias").select("*").order("nome").execute()
        if cats_res.data:
            for c in cats_res.data:
                opcoes_categorias[c["id"]] = c["nome"]
                # Define como padrão a categoria que contém "boleto" ou a primeira encontrada
                if "boleto" in c["nome"].lower() and not categoria_padrao_id:
                    categoria_padrao_id = c["id"]
            
            if not categoria_padrao_id and len(cats_res.data) > 0:
                categoria_padrao_id = cats_res.data[0]["id"]
    except Exception as e:
        print(f"Erro ao buscar categorias: {e}")

    # Checagem estrita da autenticação
    esta_autenticado = bool(user_id and user_id in user_tokens)

    def conectar_google():
        flow = obter_flow()
        auth_url, state = flow.authorization_url(prompt='consent', access_type='offline')
        app.storage.user["code_verifier"] = flow.code_verifier
        app.storage.user["oauth_state"] = state
        ui.navigate.to(auth_url, new_tab=False)

    with ui.column().classes("w-full max-w-4xl mx-auto p-3 sm:p-6 gap-6 font-sans pb-32"):
        
        # ----------------------------------------------------
        # CASO 1: DESCONECTADO (MOSTRA APENAS O CARD DE AVISO)
        # ----------------------------------------------------
        if not esta_autenticado:
            with ui.card().classes("w-full p-8 border border-blue-200 bg-blue-50/70 shadow-md rounded-2xl gap-5 text-center my-4"):
                ui.label("ℹ️ Conecte sua conta do Google Calendar").classes("text-xl sm:text-2xl font-bold text-blue-900 w-full")
                
                ui.label(
                    "Para cadastrar seus itens e receber alertas automáticos de vencimento, "
                    "faça a conexão com a sua conta do Google Calendar."
                ).classes("text-base text-slate-700 leading-relaxed max-w-2xl mx-auto")

                ui.button(
                    "🔗 Conectar conta Google", 
                    on_click=conectar_google
                ).classes("bg-blue-600 hover:bg-blue-700 text-white font-bold text-base py-3 px-8 rounded-xl shadow mx-auto mt-2")

        # ----------------------------------------------------
        # CASO 2: CONECTADO (FORMULÁRIO AJUSTADO)
        # ----------------------------------------------------
        else:
            with ui.card().classes("w-full p-4 sm:p-6 border border-slate-200 bg-white shadow-md rounded-2xl gap-5"):
                
                # Indicador movido para o topo da página (no cabeçalho)
                with ui.row().classes("w-full items-center justify-between border-b pb-3 gap-2"):
                    ui.label("➕ Cadastrar").classes("text-2xl sm:text-3xl font-bold text-slate-800")
                    ui.label("✅ Conectado ao Google Calendar").classes(
                        "text-xs sm:text-sm font-bold text-green-800 bg-green-100 px-3 py-1.5 rounded-lg border border-green-300"
                    )

                # Padronização de fontes para todos os campos
                input_props = "outlined bg-slate-50 input-class=text-base"

                # Campos Principais
                with ui.grid().classes("w-full grid-cols-1 sm:grid-cols-2 gap-5"):
                    # Campo de Seleção de Categoria
                    select_categoria = ui.select(
                        options=opcoes_categorias,
                        value=categoria_padrao_id,
                        label="Categoria"
                    ).props(input_props).classes("w-full")

                with ui.column().classes("w-full gap-5"):
                    input_empresa = ui.input(
                        "Empresa boleto / Nome do cliente",
                        placeholder="Ex: Boticário, Carla amiga..."
                    ).props(input_props).classes("w-full")

                    with ui.grid().classes("w-full grid-cols-1 sm:grid-cols-2 gap-5"):
                        input_valor = ui.number(
                            "Valor (R$)",
                            format="%.2f",
                            placeholder="0,00"
                        ).props(input_props).classes("w-full")

                        input_vencimento = ui.input(
                            "Data de vencimento"
                        ).props(f"{input_props} type=date").classes("w-full")


                        # Status inicial com opções em Title Case (Pendente)
                        select_status = ui.select(
                            ["Pendente", "Pago", "Atrasado", "Cancelado"],
                            value="Pendente",
                            label="Status inicial"
                        ).props(input_props).classes("w-full")

                # Checkbox em Title Case
                check_lembrete = ui.checkbox(
                    "🔔 Desejo receber um lembrete no Google Calendar",
                    value=True
                ).classes("mt-3 text-base sm:text-lg text-slate-800 font-bold")

                # Container de lembrete limpo sem badges repetidos dentro
                container_lembrete = ui.column().classes("w-full p-5 bg-purple-50/50 border border-purple-200 rounded-xl gap-4")
                container_lembrete.bind_visibility_from(check_lembrete, "value")

                with container_lembrete:
                    ui.label("Configuração do lembrete").classes("text-sm font-bold text-purple-900 uppercase tracking-wide")

                    with ui.grid().classes("w-full grid-cols-1 sm:grid-cols-2 gap-4"):
                        select_antecedencia = ui.select(
                            {0: "No dia do vencimento", 1: "1 dia antes", 2: "2 dias antes", 3: "3 dias antes", 4: "4 dias antes", 5: "5 dias antes"},
                            value=1,
                            label="Antecedência do aviso",
                        ).props("outlined bg-white input-class=text-base").classes("w-full")

                        # Horário Padrão alterado para 12:00
                        input_horario = ui.input(
                            "Horário do alerta",
                            value="12:00"
                        ).props("type=time outlined bg-white input-class=text-base").classes("w-full")

                def limpar_formulario():
                    input_empresa.value = ""
                    input_valor.value = None
                    input_vencimento.value = None
                    select_categoria.value = categoria_padrao_id
                    select_status.value = "Pendente"
                    check_lembrete.value = True
                    select_antecedencia.value = 1
                    input_horario.value = "12:00"

                async def salvar_boleto():
                    empresa_val = input_empresa.value.strip() if input_empresa.value else ""
                    valor_val = float(input_valor.value) if input_valor.value else 0.0
                    vencimento_val = input_vencimento.value
                    categoria_val = select_categoria.value

                    if not empresa_val or not valor_val or not vencimento_val or not categoria_val:
                        ui.notify("Por favor, preencha empresa, valor, vencimento e categoria!", color="warning", size="lg")
                        return

                    try:
                        dup_res = supabase.table("boletos").select("id").eq("user_id", user_id).eq("empresa", empresa_val).eq("valor", valor_val).eq("data_vencimento", vencimento_val).execute()
                        if dup_res.data and len(dup_res.data) > 0:
                            ui.notify("⚠️ Atenção: Este item já está cadastrado no sistema!", color="warning", size="lg")
                            return

                        status_formatado = str(select_status.value).strip().capitalize() if select_status.value else "Pendente"

                        payload = {
                            "user_id": user_id,
                            "empresa": empresa_val,
                            "valor": valor_val,
                            "data_vencimento": vencimento_val,
                            "categoria_id": categoria_val,
                            "status": status_formatado,
                            "tem_lembrete": check_lembrete.value,
                        }

                        if check_lembrete.value:
                            payload["canal_lembrete"] = "Google Calendar"
                            payload["antecedencia_dias"] = select_antecedencia.value
                            payload["horario_lembrete"] = input_horario.value

                            await asyncio.to_thread(
                                criar_evento_google_calendar_oauth,
                                user_id,
                                empresa_val,
                                valor_val,
                                vencimento_val,
                                select_antecedencia.value,
                                input_horario.value,
                            )

                        supabase.table("boletos").insert(payload).execute()

                        ui.notify("✅ Item salvo com sucesso!", color="positive", size="lg")
                        limpar_formulario()
                        
                        # Rola a tela suavemente até o topo e foca no campo da empresa
                        ui.run_javascript("window.scrollTo({top: 0, behavior: 'smooth'});")
                        input_empresa.run_method("focus")

                    except Exception as err:
                        ui.notify(f"❌ Erro ao salvar o item: {err}", color="negative", size="lg")

                # Botão atualizado para Title Case: "💾 Salvar boleto"
                ui.button("💾 Salvar", on_click=salvar_boleto).classes(
                    "bg-green-600 hover:bg-green-700 text-white font-bold text-lg mt-3 w-full py-3.5 rounded-xl shadow"
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

                def rejeitar(s=sol):
                    supabase.table("solicitacoes_acesso").update(
                        {"status": "REJEITADO"}
                    ).eq("id", s["id"]).execute()
                    ui.notify(
                        f"Solicitação de {s['email']} rejeitada.", color="warning"
                    )
                    ui.navigate.reload()

                with ui.row().classes(
                    "w-full justify-between items-center border-b py-2"
                ):
                    ui.label(f"{sol['email']} ({sol['localizacao']})").classes(
                        "text-sm font-medium"
                    )
                    with ui.row().classes("gap-2"):
                        ui.button(
                            "Aprovar", icon="check_circle", on_click=aprovar
                        ).props("color=positive").classes("font-semibold")
                        ui.button(
                            "Rejeitar", icon="cancel", on_click=rejeitar
                        ).props("color=negative").classes("font-semibold")

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
                                "EXCLUIR", icon="delete", on_click=executar_exclusao
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
                            btn_icon = (
                                "block"
                                if usr.get("ativo", True)
                                else "check_circle"
                            )

                            ui.button(
                                btn_label, icon=btn_icon, on_click=alternar_status
                            ).props(f"color={btn_color}").classes("font-semibold")
                            ui.button(
                                "Excluir", icon="delete", on_click=confirmar_exclusao
                            ).props("color=negative").classes("font-semibold")

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

                ui.button("ADICIONAR CATEGORIA", icon="add", on_click=add_categoria).classes(
                    "bg-blue-600 text-white font-bold h-10"
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
                                "SALVAR", icon="save", on_click=salvar_edicao
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
                                "EXCLUIR", icon="delete", on_click=executar_exclusao
                            ).classes("bg-red-600 text-white font-bold")

                    dialog.open()

                with ui.row().classes(
                    "w-full justify-between items-center border-b py-2"
                ):
                    ui.label(cat["nome"]).classes(
                        "text-sm font-medium text-gray-800"
                    )

                    with ui.row().classes("gap-2"):
                        ui.button("Editar", icon="edit", on_click=editar_categoria).props(
                            "color=amber"
                        ).classes("font-semibold")
                        ui.button(
                            "Excluir", icon="delete", on_click=confirmar_exclusao_categoria
                        ).props("color=negative").classes("font-semibold")



from datetime import datetime, date
import asyncio

# ==========================================
# TELA DE UTILITÁRIOS - LEMBRETES
# ==========================================
@ui.page("/lembretes")
def lembretes_page():
    if not app.storage.user.get("user_id"):
        ui.navigate.to("/login")
        return

    drawer = menu_drawer()
    cabecalho_app(drawer)
    user_id = app.storage.user.get("user_id")

    esta_autenticado = bool(user_id and user_id in user_tokens)

    def conectar_google():
        flow = obter_flow()
        auth_url, state = flow.authorization_url(prompt='consent', access_type='offline')
        app.storage.user["code_verifier"] = flow.code_verifier
        app.storage.user["oauth_state"] = state
        app.storage.user["next_url"] = "/lembretes"
        ui.navigate.to(auth_url, new_tab=False)

    # Âncora no topo para scroll suave (Requisito 4)
    topo_ancora = ui.element('div').classes('w-full')

    with ui.column().classes("w-full max-w-5xl mx-auto p-3 sm:p-6 gap-8 font-sans pb-32"):
        
        # ----------------------------------------------------
        # CASO 1: DESCONECTADO DO GOOGLE CALENDAR
        # ----------------------------------------------------
        if not esta_autenticado:
            with ui.card().classes("w-full p-8 border border-blue-200 bg-blue-50/70 shadow-md rounded-2xl gap-5 text-center my-4"):
                ui.label("ℹ️ Conecte sua conta do Google Calendar").classes("text-xl sm:text-2xl font-bold text-blue-900 w-full")
                ui.label(
                    "Para cadastrar seus lembretes e receber alertas automáticos da sua agenda, "
                    "faça a conexão com a sua conta do Google Calendar."
                ).classes("text-base text-slate-700 leading-relaxed max-w-2xl mx-auto")

                ui.button("🔗 Conectar conta Google", on_click=conectar_google).classes(
                    "bg-blue-600 hover:bg-blue-700 text-white font-bold text-base py-3 px-8 rounded-xl shadow mx-auto mt-2"
                )

        # ----------------------------------------------------
        # CASO 2: PAINEL PRINCIPAL (CADASTRAR vs GERENCIAR)
        # ----------------------------------------------------
        else:
            # Estado para controlar edição
            lembrete_em_edicao = {'id': None}

            # ====================================================
            # SEÇÃO 1: CADASTRO / EDIÇÃO DE LEMBRETES (Requisito 3)
            # ====================================================
            with ui.card().classes("w-full p-4 sm:p-6 border-2 border-purple-200 bg-purple-50/20 shadow-md rounded-2xl gap-5"):
                with ui.row().classes("w-full items-center justify-between border-b border-purple-100 pb-3 gap-2"):
                    with ui.row().classes("items-center gap-2"):
                        ui.icon("add_alert", size="28px").classes("text-purple-600")
                        titulo_formulario = ui.label("Cadastrar novo lembrete").classes("text-2xl sm:text-3xl font-bold text-slate-800")
                    
                    ui.label("✅ Conectado ao Google Calendar").classes(
                        "text-xs sm:text-sm font-bold text-green-800 bg-green-100 px-3 py-1.5 rounded-lg border border-green-300"
                    )

                input_props = "outlined bg-white input-class=text-base"

                with ui.column().classes("w-full gap-4"):
                    input_titulo = ui.input(
                        "Título do compromisso", placeholder="Ex: Reunião de equipe, Consulta médica..."
                    ).props(input_props).classes("w-full")

                    input_descricao = ui.textarea(
                        "Descrição (Opcional)", placeholder="Adicione detalhes, links ou observações..."
                    ).props(f"{input_props} rows=2").classes("w-full")

                    input_data_compromisso = ui.input("Data do compromisso").props(f"{input_props} type=date").classes("w-full sm:w-1/2")

                check_lembrete = ui.checkbox("🔔 Desejo receber um lembrete no Google Calendar", value=True).classes(
                    "mt-2 text-base font-bold text-slate-800"
                )

                container_lembrete = ui.column().classes("w-full p-4 bg-white border border-purple-200 rounded-xl gap-4 shadow-sm")
                container_lembrete.bind_visibility_from(check_lembrete, "value")

                with container_lembrete:
                    ui.label("Configuração do lembrete").classes("text-xs font-bold text-purple-900 uppercase tracking-wide")
                    with ui.grid().classes("w-full grid-cols-1 sm:grid-cols-2 gap-4"):
                        select_antecedencia = ui.select(
                            {0: "No dia do compromisso", 1: "1 dia antes", 2: "2 dias antes", 3: "3 dias antes", 4: "4 dias antes", 5: "5 dias antes"},
                            value=1, label="Antecedência do aviso",
                        ).props("outlined bg-white input-class=text-base").classes("w-full")

                        input_horario = ui.input("Horário do alerta", value="12:00").props("type=time outlined bg-white input-class=text-base").classes("w-full")

                def limpar_formulario():
                    lembrete_em_edicao['id'] = None
                    titulo_formulario.set_text("Cadastrar novo lembrete")
                    btn_salvar.set_text("💾 Salvar Lembrete")
                    btn_cancelar.set_visibility(False)
                    
                    input_titulo.value = ""
                    input_descricao.value = ""
                    input_data_compromisso.value = None
                    check_lembrete.value = True
                    select_antecedencia.value = 1
                    input_horario.value = "12:00"

                async def salvar_lembrete():
                    titulo_val = input_titulo.value.strip() if input_titulo.value else ""
                    descricao_val = input_descricao.value.strip() if input_descricao.value else ""
                    data_comp_val = input_data_compromisso.value

                    if not titulo_val or not data_comp_val:
                        ui.notify("Por favor, preencha o título e a data do compromisso!", color="warning", size="lg")
                        return

                    try:
                        payload = {
                            "user_id": user_id,
                            "titulo": titulo_val,
                            "descricao": descricao_val,
                            "data_compromisso": data_comp_val,
                            "tem_lembrete": check_lembrete.value,
                        }

                        if check_lembrete.value:
                            payload["canal_lembrete"] = "Google Calendar"
                            payload["antecedencia_dias"] = select_antecedencia.value
                            payload["horario_lembrete"] = input_horario.value

                            await asyncio.to_thread(
                                criar_evento_lembrete_google_calendar,
                                user_id, titulo_val, descricao_val, data_comp_val,
                                select_antecedencia.value, input_horario.value,
                            )

                        # Atualização ou Inserção
                        if lembrete_em_edicao['id']:
                            supabase.table("lembretes").update(payload).eq("id", lembrete_em_edicao['id']).execute()
                            ui.notify("✅ Lembrete atualizado com sucesso!", color="positive", size="lg")
                        else:
                            payload["concluido"] = False
                            supabase.table("lembretes").insert(payload).execute()
                            ui.notify("✅ Lembrete salvo com sucesso!", color="positive", size="lg")

                        limpar_formulario()
                        carregar_e_renderizar_lembretes()
                        
                        # Rolagem até o topo do formulário (Requisito 4)
                        ui.run_javascript(f'document.getElementById("{topo_ancora.id}").scrollIntoView({{behavior: "smooth"}});')

                    except Exception as err:
                        ui.notify(f"❌ Erro ao salvar o lembrete: {err}", color="negative", size="lg")

                with ui.row().classes("w-full gap-3 mt-2"):
                    btn_salvar = ui.button("💾 Salvar Lembrete", on_click=salvar_lembrete).classes(
                        "bg-purple-600 hover:bg-purple-700 text-white font-bold text-base flex-1 py-3 rounded-xl shadow transition-all"
                    )
                    btn_cancelar = ui.button("Cancelar", on_click=limpar_formulario).classes(
                        "bg-slate-200 hover:bg-slate-300 text-slate-800 font-bold py-3 px-6 rounded-xl transition-all"
                    )
                    btn_cancelar.set_visibility(False)

            # ====================================================
            # SEÇÃO 2: GESTÃO E CONSULTA DE LEMBRETES (Requisito 3)
            # ====================================================
            with ui.column().classes("w-full gap-4 mt-4"):
                with ui.row().classes("w-full items-center gap-2 border-b pb-2"):
                    ui.icon("folder_special", size="28px").classes("text-slate-700")
                    ui.label("Gestão de Lembretes Cadastrados").classes("text-2xl sm:text-3xl font-bold text-slate-800")

                # FILTROS RECOLHÍVEIS (Requisito 2)
                with ui.expansion("🔍 Filtros de Busca e Pesquisa", icon="search").classes(
                    "w-full bg-slate-100 border border-slate-200 rounded-2xl shadow-sm text-slate-700 font-bold"
                ):
                    with ui.grid().classes("w-full grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 p-2"):
                        filter_busca = ui.input("Buscar por título ou descrição", placeholder="Digite para buscar...").props("outlined bg-white dense").classes("w-full")
                        
                        filter_status = ui.select(
                            {"todos": "Todos os status", "pendente": "Pendente", "atrasado": "Atrasado", "concluido": "Concluído"},
                            value="todos", label="Status"
                        ).props("outlined bg-white dense").classes("w-full")

                        filter_data_inicio = ui.input("De (Data)").props("type=date outlined bg-white dense").classes("w-full")
                        filter_data_fim = ui.input("Até (Data)").props("type=date outlined bg-white dense").classes("w-full")

                # Container onde os cards serão renderizados
                container_cards = ui.column().classes("w-full gap-4 mt-2")

            # Function para carregar dados para Edição (Requisito 1)
            def preparar_edicao(item: dict):
                lembrete_em_edicao['id'] = item['id']
                titulo_formulario.set_text("✏️ Editar lembrete")
                btn_salvar.set_text("🔄 Atualizar Lembrete")
                btn_cancelar.set_visibility(True)

                input_titulo.value = item.get("titulo", "")
                input_descricao.value = item.get("descricao", "")
                input_data_compromisso.value = item.get("data_compromisso")
                check_lembrete.value = item.get("tem_lembrete", False)
                select_antecedencia.value = item.get("antecedencia_dias", 1)
                input_horario.value = item.get("horario_lembrete", "12:00")

                # 1. Rola a janela até o topo da página suavemente
                ui.run_javascript('window.scrollTo({top: 0, behavior: "smooth"});')

                # 2. Alternativa nativa do NiceGUI para focar no campo de título:
                input_titulo.run_method('focus')


            # Function para atualizar status de conclusão
            def alternar_status_conclusao(lembrete_id: int, status_atual: bool):
                try:
                    supabase.table("lembretes").update({"concluido": not status_atual}).eq("id", lembrete_id).execute()
                    status_nome = "reaberto" if status_atual else "concluído"
                    ui.notify(f"Compromisso marcado como {status_nome}!", color="positive")
                    carregar_e_renderizar_lembretes()
                except Exception as e:
                    ui.notify(f"Erro ao atualizar compromisso: {e}", color="negative")

            # Modal e Função de Exclusão com Confirmação (Requisito 1)
            def confirmar_exclusao(lembrete_id: int):
                with ui.dialog() as dialog, ui.card().classes("p-6 gap-4 border border-slate-200 rounded-2xl max-w-sm w-full"):
                    ui.label("⚠️ Confirmar exclusão").classes("text-lg font-bold text-slate-800")
                    ui.label("Tem certeza que deseja excluir este lembrete? Esta ação não pode ser desfeita.").classes("text-sm text-slate-600")
                    
                    with ui.row().classes("w-full justify-end gap-2 mt-2"):
                        ui.button("Cancelar", on_click=dialog.close).props("flat").classes("text-slate-600")
                        
                        def efetuar_delecao():
                            try:
                                supabase.table("lembretes").delete().eq("id", lembrete_id).execute()
                                ui.notify("Lembrete removido com sucesso!", color="info")
                                dialog.close()
                                carregar_e_renderizar_lembretes()
                            except Exception as e:
                                ui.notify(f"Erro ao remover lembrete: {e}", color="negative")

                        ui.button("Excluir", on_click=efetuar_delecao).props("unelevated color=negative").classes("rounded-lg")
                dialog.open()

            # Renderização dinâmica dos Cards
            def carregar_e_renderizar_lembretes():
                container_cards.clear()
                hoje = date.today()

                res = supabase.table("lembretes").select("*").eq("user_id", user_id).order("data_compromisso", desc=False).execute()
                todos_lembretes = res.data or []

                termo = filter_busca.value.lower().strip() if filter_busca.value else ""
                status_f = filter_status.value
                dt_ini = filter_data_inicio.value
                dt_fim = filter_data_fim.value

                lembretes_filtrados = []
                for item in todos_lembretes:
                    dt_comp = datetime.strptime(item["data_compromisso"], "%Y-%m-%d").date()
                    is_concluido = item.get("concluido", False)

                    if is_concluido:
                        calc_status = "concluido"
                    elif dt_comp < hoje:
                        calc_status = "atrasado"
                    else:
                        calc_status = "pendente"

                    item["_status_calc"] = calc_status
                    item["_dt_comp"] = dt_comp

                    if termo and not (termo in item.get("titulo", "").lower() or termo in (item.get("descricao") or "").lower()):
                        continue
                    if status_f != "todos" and calc_status != status_f:
                        continue
                    if dt_ini and item["data_compromisso"] < dt_ini:
                        continue
                    if dt_fim and item["data_compromisso"] > dt_fim:
                        continue

                    lembretes_filtrados.append(item)

                pendentes_atrasados = [l for l in lembretes_filtrados if l["_status_calc"] in ["pendente", "atrasado"]]
                concluidos = [l for l in lembretes_filtrados if l["_status_calc"] == "concluido"]

                with container_cards:
                    with ui.tabs().classes("w-full border-b") as tabs:
                        tab_pendentes = ui.tab(f"⏳ Pendentes & Atrasados ({len(pendentes_atrasados)})")
                        tab_concluidos = ui.tab(f"✅ Concluídos ({len(concluidos)})")

                    with ui.tab_panels(tabs, value=tab_pendentes).classes("w-full bg-transparent pt-4 gap-4"):
                        
                        with ui.tab_panel(tab_pendentes).classes("p-0 w-full gap-4"):
                            if not pendentes_atrasados:
                                with ui.card().classes("w-full p-8 text-center bg-slate-50 border border-dashed rounded-xl"):
                                    ui.label("🎉 Nenhum compromisso pendente no momento!").classes("text-slate-500 font-bold text-base")
                            else:
                                for item in pendentes_atrasados:
                                    renderizar_card_lembrete(item)

                        with ui.tab_panel(tab_concluidos).classes("p-0 w-full gap-4"):
                            if not concluidos:
                                with ui.card().classes("w-full p-8 text-center bg-slate-50 border border-dashed rounded-xl"):
                                    ui.label("Nenhum compromisso concluído nesta visualização.").classes("text-slate-500 font-bold text-base")
                            else:
                                for item in concluidos:
                                    renderizar_card_lembrete(item)

            def renderizar_card_lembrete(item):
                status = item["_status_calc"]
                dt_comp_str = item["_dt_comp"].strftime("%d/%m/%Y")
                l_id = item["id"]
                is_concluido = item.get("concluido", False)

                if status == "atrasado":
                    border_color = "border-l-8 border-l-red-500 border-red-200 bg-red-50/30"
                    badge_bg = "bg-red-100 text-red-800 border-red-300"
                    badge_icon = "warning"
                    status_text = "Atrasado"
                elif status == "concluido":
                    border_color = "border-l-8 border-l-emerald-500 border-slate-200 bg-slate-50/80"
                    badge_bg = "bg-emerald-100 text-emerald-800 border-emerald-300"
                    badge_icon = "check_circle"
                    status_text = "Concluído"
                else:
                    border_color = "border-l-8 border-l-purple-600 border-purple-200 bg-white"
                    badge_bg = "bg-purple-100 text-purple-800 border-purple-300"
                    badge_icon = "schedule"
                    status_text = "Pendente"

                with ui.card().classes(f"w-full p-4 border shadow-sm rounded-xl transition-all hover:shadow-md mb-3 {border_color}"):
                    with ui.row().classes("w-full items-start justify-between gap-3"):
                        
                        # Lado Esquerdo: Checkbox + Conteúdo
                        with ui.row().classes("items-start gap-3 flex-1"):
                            ui.checkbox(
                                value=is_concluido,
                                on_change=lambda e, lid=l_id, st=is_concluido: alternar_status_conclusao(lid, st)
                            ).props("size=md color=purple").classes("mt-0.5")

                            with ui.column().classes("gap-1 flex-1"):
                                title_class = "line-through text-slate-400" if is_concluido else "text-slate-800 font-bold"
                                ui.label(item["titulo"]).classes(f"text-lg sm:text-xl {title_class}")

                                if item.get("descricao"):
                                    ui.label(item["descricao"]).classes("text-sm text-slate-600 leading-relaxed")

                                with ui.row().classes("items-center gap-4 mt-2 text-xs font-semibold text-slate-500 flex-wrap"):
                                    ui.label(f"📅 Compromisso: {dt_comp_str}").classes("bg-slate-100 px-2.5 py-1 rounded-md border")
                                    
                                    if item.get("tem_lembrete"):
                                        ui.label(
                                            f"⏰ Alerta: {item.get('antecedencia_dias')}d antes às {item.get('horario_lembrete')}"
                                        ).classes("bg-purple-50 text-purple-700 px-2.5 py-1 rounded-md border border-purple-200")

                        # Lado Direito: Status + Botões de Ação (Requisito 1)
                        with ui.column().classes("items-end gap-2"):
                            with ui.row().classes(f"items-center gap-1.5 px-3 py-1 rounded-full border text-xs font-bold {badge_bg}"):
                                ui.icon(badge_icon, size="16px")
                                ui.label(status_text)

                            with ui.row().classes("items-center gap-1 mt-1"):
                                ui.button(
                                    icon="edit",
                                    on_click=lambda it=item: preparar_edicao(it)
                                ).props("flat round dense color=primary").classes("hover:bg-blue-50").tooltip("Editar Lembrete")

                                ui.button(
                                    icon="delete",
                                    on_click=lambda lid=l_id: confirmar_exclusao(lid)
                                ).props("flat round dense color=negative").classes("hover:bg-red-50").tooltip("Excluir Lembrete")

            # Listeners dos filtros
            filter_busca.on("update:model-value", carregar_e_renderizar_lembretes)
            filter_status.on("update:model-value", carregar_e_renderizar_lembretes)
            filter_data_inicio.on("update:model-value", carregar_e_renderizar_lembretes)
            filter_data_fim.on("update:model-value", carregar_e_renderizar_lembretes)

            # Carga Inicial
            carregar_e_renderizar_lembretes()


from datetime import datetime
import asyncio
from nicegui import ui, app

# ==========================================
# TELA DE UTILITÁRIOS - TAREFAS / CHECKLIST
# ==========================================
@ui.page("/tarefas")
def tarefas_page():
    if not app.storage.user.get("user_id"):
        ui.navigate.to("/login")
        return

    drawer = menu_drawer()
    cabecalho_app(drawer)
    user_id = app.storage.user.get("user_id")

    # Estado local para controle de edição e lista temporária de itens
    tarefa_em_edicao = {'id': None}
    itens_temporarios = []  # Estrutura: [{'texto': str, 'concluido': bool}]

    # Âncora no topo para scroll suave ao editar
    topo_ancora = ui.element('div').classes('w-full')

    with ui.column().classes("w-full max-w-5xl mx-auto p-3 sm:p-6 gap-8 font-sans pb-32"):

        # ====================================================
        # SEÇÃO 1: CADASTRO / EDIÇÃO DE TAREFAS (FORMULÁRIO)
        # ====================================================
        with ui.card().classes("w-full p-4 sm:p-6 border-2 border-purple-200 bg-purple-50/20 shadow-md rounded-2xl gap-5"):
            with ui.row().classes("w-full items-center justify-between border-b border-purple-100 pb-3"):
                with ui.row().classes("items-center gap-2"):
                    ui.icon("check_box", size="28px").classes("text-purple-600")
                    titulo_formulario = ui.label("Cadastrar Nova Lista de Tarefas").classes("text-2xl sm:text-3xl font-bold text-slate-800")

            input_props = "outlined bg-white input-class=text-base"

            with ui.column().classes("w-full gap-4"):
                # Campo Título
                input_titulo = ui.input(
                    "Título da Tarefa / Lista", 
                    placeholder="Ex: Compras do Mês, Checklist do Projeto..."
                ).props(input_props).classes("w-full")

                # Área de Adição Dinâmica de Itens
                ui.label("Itens do Checklist").classes("text-sm font-bold text-slate-700 mt-2")
                
                with ui.row().classes("w-full items-center gap-2"):
                    input_novo_item = ui.input(
                        "Adicionar Item", 
                        placeholder="Digite o item e pressione Enter ou clique em Adicionar"
                    ).props(input_props).classes("flex-1")
                    
                    def adicionar_item_lista():
                        txt = input_novo_item.value.strip() if input_novo_item.value else ""
                        if txt:
                            itens_temporarios.append({'texto': txt, 'concluido': False})
                            input_novo_item.value = ""
                            atualizar_view_itens_temporarios()
                        else:
                            ui.notify("Digite o texto do item antes de adicionar!", color="warning")

                    input_novo_item.on('keydown.enter', adicionar_item_lista)
                    ui.button("➕ Adicionar", on_click=adicionar_item_lista).classes(
                        "bg-purple-600 hover:bg-purple-700 text-white font-bold h-[56px] px-5 rounded-xl shadow"
                    )

                # Container visual para preview/edição dos itens adicionados no formulário
                container_preview_itens = ui.column().classes("w-full gap-2 p-3 bg-white border border-purple-100 rounded-xl")

                def atualizar_view_itens_temporarios():
                    container_preview_itens.clear()
                    with container_preview_itens:
                        if not itens_temporarios:
                            ui.label("Nenhum item adicionado ainda.").classes("text-xs text-slate-400 italic")
                        else:
                            for idx, item in enumerate(itens_temporarios):
                                with ui.row().classes("w-full items-center justify-between p-2 bg-slate-50 border rounded-lg gap-2"):
                                    def alternar_check(e, index=idx):
                                        itens_temporarios[index]['concluido'] = e.value

                                    ui.checkbox(
                                        value=item['concluido'], 
                                        on_change=alternar_check
                                    ).classes("text-slate-800")

                                    def atualizar_texto_item(e, index=idx):
                                        itens_temporarios[index]['texto'] = e.value.strip()

                                    ui.input(
                                        value=item['texto'],
                                        on_change=atualizar_texto_item
                                    ).props("dense borderless input-class=text-sm font-medium").classes("flex-1 bg-white px-2 rounded border border-slate-200")

                                    def remover_item_temp(index=idx):
                                        itens_temporarios.pop(index)
                                        atualizar_view_itens_temporarios()

                                    ui.button(icon="delete", on_click=remover_item_temp).props("flat round dense color=negative").tooltip("Remover item")

                atualizar_view_itens_temporarios()

            def limpar_formulario():
                tarefa_em_edicao['id'] = None
                itens_temporarios.clear()
                titulo_formulario.set_text("Cadastrar Nova Lista de Tarefas")
                btn_salvar.set_text("💾 Salvar Tarefa")
                btn_cancelar.set_visibility(False)
                input_titulo.value = ""
                input_novo_item.value = ""
                atualizar_view_itens_temporarios()

            async def salvar_tarefa():
                titulo_val = input_titulo.value.strip() if input_titulo.value else ""

                if not titulo_val:
                    ui.notify("Por favor, informe o título da tarefa!", color="warning", size="lg")
                    return

                itens_validos = [it for it in itens_temporarios if it.get('texto', '').strip()]

                if not itens_validos:
                    ui.notify("Adicione pelo menos um item válido à lista de tarefas!", color="warning", size="lg")
                    return

                try:
                    payload = {
                        "user_id": user_id,
                        "titulo": titulo_val,
                        "itens": itens_validos
                    }

                    if tarefa_em_edicao['id']:
                        supabase.table("tarefas").update(payload).eq("id", tarefa_em_edicao['id']).execute()
                        ui.notify("✅ Tarefa atualizada com sucesso!", color="positive", size="lg")
                    else:
                        supabase.table("tarefas").insert(payload).execute()
                        ui.notify("✅ Tarefa salva com sucesso!", color="positive", size="lg")

                    limpar_formulario()
                    carregar_e_renderizar_tarefas()
                    
                    ui.run_javascript(f'document.getElementById("{topo_ancora.id}").scrollIntoView({{behavior: "smooth"}});')

                except Exception as err:
                    ui.notify(f"❌ Erro ao salvar tarefa: {err}", color="negative", size="lg")

            with ui.row().classes("w-full gap-3 mt-2"):
                btn_salvar = ui.button("💾 Salvar Tarefa", on_click=salvar_tarefa).classes(
                    "bg-purple-600 hover:bg-purple-700 text-white font-bold text-base flex-1 py-3 rounded-xl shadow transition-all"
                )
                btn_cancelar = ui.button("Cancelar", on_click=limpar_formulario).classes(
                    "bg-slate-200 hover:bg-slate-300 text-slate-800 font-bold py-3 px-6 rounded-xl transition-all"
                )
                btn_cancelar.set_visibility(False)

        # ====================================================
        # SEÇÃO 2: GESTÃO E CONSULTA DE TAREFAS / CARDS
        # ====================================================
        with ui.column().classes("w-full gap-4 mt-4"):
            with ui.row().classes("w-full items-center gap-2 border-b pb-2"):
                ui.icon("task", size="28px").classes("text-slate-700")
                ui.label("Minhas Tarefas e Checklists").classes("text-2xl sm:text-3xl font-bold text-slate-800")

            # Filtros
            with ui.expansion("🔍 Filtros de Busca", icon="search").classes(
                "w-full bg-slate-100 border border-slate-200 rounded-2xl shadow-sm text-slate-700 font-bold"
            ):
                with ui.grid().classes("w-full grid-cols-1 sm:grid-cols-2 gap-3 p-2"):
                    filter_busca = ui.input("Buscar por título ou item", placeholder="Digite para buscar...").props("outlined bg-white dense").classes("w-full")
                    
                    filter_status = ui.select(
                        {"todos": "Todas", "pendentes": "Em Andamento", "concluidas": "100% Concluídas"},
                        value="todos", label="Status das tarefas"
                    ).props("outlined bg-white dense").classes("w-full")

            container_cards = ui.column().classes("w-full gap-6 mt-2")

        def preparar_edicao(item: dict):
            tarefa_em_edicao['id'] = item['id']
            titulo_formulario.set_text("✏️ Editar Tarefa")
            btn_salvar.set_text("🔄 Atualizar Tarefa")
            btn_cancelar.set_visibility(True)

            input_titulo.value = item.get("titulo", "")
            
            itens_temporarios.clear()
            for it in item.get("itens", []):
                itens_temporarios.append({'texto': it['texto'], 'concluido': it.get('concluido', False)})
            
            atualizar_view_itens_temporarios()

            ui.run_javascript('window.scrollTo({top: 0, behavior: "smooth"});')
            input_titulo.run_method('focus')

        def confirmar_exclusao(tarefa_id: int):
            with ui.dialog() as dialog, ui.card().classes("p-6 gap-4 border border-slate-200 rounded-2xl max-w-sm w-full"):
                ui.label("⚠️ Confirmar exclusão").classes("text-lg font-bold text-slate-800")
                ui.label("Tem certeza que deseja excluir esta tarefa e todos os seus itens?").classes("text-sm text-slate-600")
                
                with ui.row().classes("w-full justify-end gap-2 mt-2"):
                    ui.button("Cancelar", on_click=dialog.close).props("flat").classes("text-slate-600")
                    
                    def efetuar_delecao():
                        try:
                            supabase.table("tarefas").delete().eq("id", tarefa_id).execute()
                            ui.notify("Tarefa removida com sucesso!", color="info")
                            dialog.close()
                            carregar_e_renderizar_tarefas()
                        except Exception as e:
                            ui.notify(f"Erro ao remover tarefa: {e}", color="negative")

                    ui.button("Excluir", on_click=efetuar_delecao).props("unelevated color=negative").classes("rounded-lg")
            dialog.open()

        def salvar_status_silencioso(tarefa_id: int, lista_itens: list):
            try:
                supabase.table("tarefas").update({"itens": lista_itens}).eq("id", tarefa_id).execute()
            except Exception as e:
                ui.notify(f"Erro ao salvar status do item: {e}", color="negative")

        # Renderização Dinâmica dos Cards
        def carregar_e_renderizar_tarefas():
            container_cards.clear()

            res = supabase.table("tarefas").select("*").eq("user_id", user_id).order("id", desc=True).execute()
            todas_tarefas = res.data or []

            termo = filter_busca.value.lower().strip() if filter_busca.value else ""
            status_f = filter_status.value

            tarefas_pendentes = []
            tarefas_concluidas = []

            for item in todas_tarefas:
                itens = item.get("itens", [])
                total_itens = len(itens)
                concluidos = sum(1 for i in itens if i.get("concluido"))
                
                is_totalmente_concluido = (total_itens > 0 and concluidos == total_itens)

                texto_itens_concatenado = " ".join([i.get("texto", "") for i in itens]).lower()
                if termo and not (termo in item.get("titulo", "").lower() or termo in texto_itens_concatenado):
                    continue

                if status_f == "concluidas" and not is_totalmente_concluido:
                    continue
                if status_f == "pendentes" and is_totalmente_concluido:
                    continue

                if is_totalmente_concluido:
                    tarefas_concluidas.append(item)
                else:
                    tarefas_pendentes.append(item)

            with container_cards:
                if not tarefas_pendentes and not tarefas_concluidas:
                    with ui.card().classes("w-full p-8 text-center bg-slate-50 border border-dashed rounded-xl"):
                        ui.label("Nenhuma tarefa encontrada.").classes("text-slate-500 font-bold text-base")
                else:
                    if tarefas_pendentes and status_f != "concluidas":
                        with ui.column().classes("w-full gap-3"):
                            with ui.row().classes("items-center gap-2 border-b border-purple-200 pb-1"):
                                ui.icon("pending_actions", size="20px").classes("text-purple-600")
                                ui.label(f"Em Andamento ({len(tarefas_pendentes)})").classes("text-lg font-bold text-slate-700")
                            for item in tarefas_pendentes:
                                renderizar_card_tarefa(item)

                    if tarefas_concluidas and status_f != "pendentes":
                        with ui.column().classes("w-full gap-3 mt-2"):
                            with ui.row().classes("items-center gap-2 border-b border-emerald-200 pb-1"):
                                ui.icon("check_circle", size="20px").classes("text-emerald-600")
                                ui.label(f"Concluídas ({len(tarefas_concluidas)})").classes("text-lg font-bold text-slate-700")
                            for item in tarefas_concluidas:
                                renderizar_card_tarefa(item)

        def renderizar_card_tarefa(item):
            t_id = item["id"]
            itens = item.get("itens", [])

            with ui.expansion().classes("w-full border shadow-sm rounded-xl transition-all hover:shadow-md mb-2 bg-white border-purple-200 border-l-8 border-l-purple-600") as expansion:
                
                with expansion.add_slot('header'):
                    with ui.row().classes("w-full items-center justify-between pr-2 gap-2"):
                        with ui.column().classes("gap-0.5 flex-1"):
                            lbl_titulo = ui.label(item["titulo"]).classes("text-lg sm:text-xl text-slate-800 font-bold")
                            lbl_resumo_itens = ui.label("").classes("text-xs font-semibold text-slate-500")

                        with ui.row().classes("items-center gap-2"):
                            container_badge = ui.row().classes("items-center gap-1 px-3 py-1 rounded-full border text-xs font-bold")
                            icon_badge = ui.icon("").classes("text-sm")
                            lbl_badge = ui.label("")

                            ui.button(
                                icon="edit",
                                on_click=lambda e, it=item: preparar_edicao(it)
                            ).props("flat round dense color=primary").classes("hover:bg-blue-50").tooltip("Editar Tarefa")

                            ui.button(
                                icon="delete",
                                on_click=lambda e, tid=t_id: confirmar_exclusao(tid)
                            ).props("flat round dense color=negative").classes("hover:bg-red-50").tooltip("Excluir Tarefa")

                def atualizar_estado_visual_card():
                    tot = len(itens)
                    conc = sum(1 for i in itens if i.get("concluido"))
                    tudo_pronto = (tot > 0 and conc == tot)

                    lbl_resumo_itens.set_text(f"📋 Total de Itens: {tot}")

                    if tudo_pronto:
                        lbl_titulo.classes(replace="line-through text-slate-400 font-bold")
                        container_badge.classes(replace="items-center gap-1 px-3 py-1 rounded-full border text-xs font-bold bg-emerald-100 text-emerald-800 border-emerald-300")
                        icon_badge.props("name=check_circle")
                        lbl_badge.set_text("Concluída")
                    else:
                        lbl_titulo.classes(replace="text-slate-800 font-bold")
                        container_badge.classes(replace="items-center gap-1 px-3 py-1 rounded-full border text-xs font-bold bg-purple-100 text-purple-800 border-purple-300")
                        icon_badge.props("name=pending_actions")
                        lbl_badge.set_text(f"{conc}/{tot} Concluídos")

                atualizar_estado_visual_card()

                with ui.column().classes("w-full p-4 gap-3 bg-white/70 border-t border-slate-100"):
                    ui.label("Checklist de Itens:").classes("text-xs font-bold text-slate-500 uppercase tracking-wide")
                    
                    for index, it in enumerate(itens):
                        chk_label = ui.checkbox(
                            text=it.get("texto"),
                            value=it.get("concluido", False)
                        )
                        
                        def aplicar_estilo_chk(cb_element, esta_concluido):
                            if esta_concluido:
                                cb_element.style("text-decoration: line-through; opacity: 0.6;")
                            else:
                                cb_element.style("text-decoration: none; opacity: 1.0;")

                        aplicar_estilo_chk(chk_label, it.get("concluido", False))

                        def ao_mudar_chk(e, idx=index, cb_elem=chk_label):
                            novo_val = e.value
                            itens[idx]["concluido"] = novo_val
                            
                            # Atualiza a interface local e o cabeçalho sem dar refresh no DOM
                            aplicar_estilo_chk(cb_elem, novo_val)
                            atualizar_estado_visual_card()
                            salvar_status_silencioso(t_id, itens)
                            
                            # Se 100% das tarefas forem finalizadas, recarrega para transferir para a seção "Concluídas"
                            tot = len(itens)
                            conc = sum(1 for i in itens if i.get("concluido"))
                            if conc == tot:
                                carregar_e_renderizar_tarefas()

                        chk_label.on_value_change(ao_mudar_chk)

        filter_busca.on("update:model-value", carregar_e_renderizar_tarefas)
        filter_status.on("update:model-value", carregar_e_renderizar_tarefas)

        carregar_e_renderizar_tarefas()


from datetime import datetime
from zoneinfo import ZoneInfo
import asyncio
from nicegui import ui, app

# Helper para converter e formatar a data recebida no fuso de Brasília
def formatar_data_brasilia(data_iso_str: str) -> str:
    if not data_iso_str:
        return ""
    try:
        # Normaliza sufixo Z para offset ISO
        data_clean = data_iso_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(data_clean)
        
        # Caso a string venha sem fuso definido (naive), assume UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
            
        # Converte para o fuso de Brasília
        dt_br = dt.astimezone(ZoneInfo("America/Sao_Paulo"))
        return f"Criado em: {dt_br.strftime('%d/%m/%Y às %H:%M')}"
    except Exception:
        return ""

# ==========================================
# TELA DE UTILITÁRIOS - BLOCO DE ANOTAÇÕES
# ==========================================
@ui.page("/anotacoes")
def anotacoes_page():
    if not app.storage.user.get("user_id"):
        ui.navigate.to("/login")
        return

    drawer = menu_drawer()
    cabecalho_app(drawer)
    user_id = app.storage.user.get("user_id")

    anotacao_em_edicao = {'id': None}
    topo_ancora = ui.element('div').classes('w-full')

    ui.add_body_html('''
        <script>
            window.inicializarCanvasAnotacoes = function() {
                const canvas = document.getElementById('sketchpad');
                if(!canvas) return;
                const ctx = canvas.getContext('2d');
                let drawing = false;

                ctx.lineWidth = 3;
                ctx.lineCap = 'round';
                ctx.strokeStyle = '#000000';

                function getPos(e) {
                    const rect = canvas.getBoundingClientRect();
                    const clientX = e.touches ? e.touches[0].clientX : e.clientX;
                    const clientY = e.touches ? e.touches[0].clientY : e.clientY;
                    return { x: clientX - rect.left, y: clientY - rect.top };
                }

                function startDraw(e) { drawing = true; draw(e); }
                function endDraw() { drawing = false; ctx.beginPath(); }
                function draw(e) {
                    if(!drawing) return;
                    e.preventDefault();
                    const pos = getPos(e);
                    ctx.lineTo(pos.x, pos.y);
                    ctx.stroke();
                    ctx.beginPath();
                    ctx.moveTo(pos.x, pos.y);
                }

                canvas.addEventListener('mousedown', startDraw);
                canvas.addEventListener('mouseup', endDraw);
                canvas.addEventListener('mousemove', draw);
                canvas.addEventListener('touchstart', startDraw);
                canvas.addEventListener('touchend', endDraw);
                canvas.addEventListener('touchmove', draw);

                window.limparCanvas = function() {
                    ctx.clearRect(0, 0, canvas.width, canvas.height);
                };
                window.obterDataURLCanvas = function() {
                    return canvas.toDataURL("image/png");
                };
            };
        </script>
    ''')

    with ui.column().classes("w-full max-w-6xl mx-auto p-3 sm:p-6 gap-8 font-sans pb-32"):

        # ====================================================
        # SEÇÃO 1: FORMULÁRIO DE ANOTAÇÃO (FOLHA EM BRANCO)
        # ====================================================
        with ui.card().classes("w-full p-4 sm:p-6 border-2 border-purple-200 bg-purple-50/10 shadow-lg rounded-2xl gap-5"):
            with ui.row().classes("w-full items-center justify-between border-b border-purple-100 pb-3"):
                with ui.row().classes("items-center gap-2"):
                    ui.icon("edit_note", size="32px").classes("text-purple-600")
                    titulo_formulario = ui.label("Nova Anotação").classes("text-2xl sm:text-3xl font-bold text-slate-800")

            with ui.column().classes("w-full gap-4"):
                # Campo Título
                input_titulo = ui.input(
                    "Título da Anotação", 
                    placeholder="Ex: Ideias para o projeto, Minuta de reunião..."
                ).props("outlined bg-white input-class=text-lg font-bold").classes("w-full")

                # Área Principal de Anotações (Editor Amplo Visível)
                ui.label("Conteúdo / Folha de Anotação").classes("text-base font-bold text-slate-700")

                toolbar_options = "[['bold', 'italic', 'underline', 'strike'], ['quote', 'unordered', 'ordered'], ['left', 'center', 'right', 'justify'], ['token', 'hr', 'link'], ['undo', 'redo', 'fullscreen']]"

                editor_conteudo = ui.editor(
                    placeholder="Escreva livremente aqui sua anotação..."
                ).classes("w-full bg-white border-2 border-purple-200 rounded-xl min-h-[420px] text-lg shadow-inner").props(f':toolbar="{toolbar_options}"')

                # Barra de opções rápidas para cores e marca-texto no editor
                with ui.row().classes("gap-2 items-center text-xs font-bold text-slate-600 bg-slate-50 p-2 rounded-lg border border-slate-200"):
                    ui.label("Cores e Marca-Texto:").classes("mr-1")
                    ui.button("Texto Vermelho", on_click=lambda: ui.run_javascript("document.execCommand('foreColor', false, '#e11d48')")).props("dense outline color=red")
                    ui.button("Texto Azul", on_click=lambda: ui.run_javascript("document.execCommand('foreColor', false, '#2563eb')")).props("dense outline color=blue")
                    ui.button("Texto Verde", on_click=lambda: ui.run_javascript("document.execCommand('foreColor', false, '#16a34a')")).props("dense outline color=green")
                    ui.button("🟡 Marca-Texto Amarelo", on_click=lambda: ui.run_javascript("document.execCommand('hiliteColor', false, '#fef08a')")).props("dense color=warning")
                    ui.button("🟢 Marca-Texto Verde", on_click=lambda: ui.run_javascript("document.execCommand('hiliteColor', false, '#bbf7d0')")).props("dense color=positive")
                    ui.button("🧹 Limpar Formatação", on_click=lambda: ui.run_javascript("document.execCommand('removeFormat', false, null)")).props("dense flat color=grey")

                # Área de Desenho a Mão Livre (Canvas)
                exp_desenho = ui.expansion("🎨 Adicionar Desenho / Rascunho a Mão Livre", icon="gesture").classes(
                    "w-full bg-white border border-purple-200 rounded-xl text-purple-900 font-bold"
                )
                
                with exp_desenho:
                    with ui.column().classes("w-full items-center gap-3 p-3"):
                        ui.label("Utilize o mouse ou touch para desenhar no quadro abaixo:").classes("text-xs text-slate-500 font-normal")
                        
                        ui.html('''
                            <div style="text-align: center;">
                                <canvas id="sketchpad" width="750" height="280" style="border:2px dashed #a855f7; border-radius:12px; background-color:#ffffff; cursor:crosshair; touch-action:none; max-width:100%;"></canvas>
                            </div>
                        ''').classes("w-full")

                        with ui.row().classes("gap-2"):
                            ui.button("🧹 Limpar Desenho", on_click=lambda: ui.run_javascript("if(window.limparCanvas) window.limparCanvas();")).props("flat color=warning dense")
                            
                            async def anexar_desenho():
                                img_url = await ui.run_javascript("return window.obterDataURLCanvas ? window.obterDataURLCanvas() : '';")
                                if img_url and len(img_url) > 100:
                                    tag_img = f'<p><img src="{img_url}" style="max-width:100%; border-radius:8px; border:1px solid #cbd5e1; margin:12px 0;" /></p>'
                                    editor_conteudo.value = (editor_conteudo.value or "") + tag_img
                                    ui.notify("Desenho anexado à anotação!", color="positive")
                                    ui.run_javascript("if(window.limparCanvas) window.limparCanvas();")
                                else:
                                    ui.notify("Desenhe algo no quadro antes de anexar!", color="warning")

                            ui.button("📌 Anexar Desenho à Anotação", on_click=anexar_desenho).classes("bg-purple-600 text-white font-bold rounded-lg text-xs")

                exp_desenho.on_value_change(lambda e: ui.run_javascript("setTimeout(() => { if(window.inicializarCanvasAnotacoes) window.inicializarCanvasAnotacoes(); }, 200);") if e.value else None)

            def limpar_formulario():
                anotacao_em_edicao['id'] = None
                titulo_formulario.set_text("Nova Anotação")
                btn_salvar.set_text("💾 Salvar Anotação")
                btn_cancelar.set_visibility(False)
                input_titulo.value = ""
                editor_conteudo.value = ""
                ui.run_javascript("if(window.limparCanvas) window.limparCanvas();")

            async def salvar_anotacao():
                titulo_val = input_titulo.value.strip() if input_titulo.value else ""
                conteudo_val = editor_conteudo.value.strip() if editor_conteudo.value else ""

                if not titulo_val:
                    ui.notify("Informe o título da anotação!", color="warning", size="lg")
                    return

                if not conteudo_val:
                    ui.notify("O texto da anotação não pode ficar vazio!", color="warning", size="lg")
                    return

                current_user_id = app.storage.user.get("user_id")

                try:
                    # Obtém a data e hora atual no fuso de Brasília (ISO format)
                    agora_brasilia = datetime.now(ZoneInfo("America/Sao_Paulo")).isoformat()

                    payload = {
                        "user_id": current_user_id,
                        "titulo": titulo_val,
                        "conteudo": conteudo_val,
                        "updated_at": agora_brasilia
                    }

                    if anotacao_em_edicao['id']:
                        supabase.table("anotacoes").update(payload).eq("id", anotacao_em_edicao['id']).execute()
                        ui.notify("✅ Anotação atualizada!", color="positive", size="lg")
                    else:
                        # Inclui created_at explicitamente ao criar novo registro
                        payload["created_at"] = agora_brasilia
                        supabase.table("anotacoes").insert(payload).execute()
                        ui.notify("✅ Anotação salva com sucesso!", color="positive", size="lg")

                    limpar_formulario()
                    carregar_e_renderizar_anotacoes()

                except Exception as err:
                    ui.notify(f"❌ Erro ao salvar: {err}", color="negative", size="lg")

            with ui.row().classes("w-full gap-3 mt-2"):
                btn_salvar = ui.button("💾 Salvar Anotação", on_click=salvar_anotacao).classes(
                    "bg-purple-600 hover:bg-purple-700 text-white font-bold text-base flex-1 py-3 rounded-xl shadow transition-all"
                )
                btn_cancelar = ui.button("Cancelar", on_click=limpar_formulario).classes(
                    "bg-slate-200 hover:bg-slate-300 text-slate-800 font-bold py-3 px-6 rounded-xl transition-all"
                )
                btn_cancelar.set_visibility(False)

        # ====================================================
        # SEÇÃO 2: CONSULTA E LISTAGEM DE ANOTAÇÕES
        # ====================================================
        with ui.column().classes("w-full gap-4 mt-4"):
            with ui.row().classes("w-full items-center gap-2 border-b pb-2"):
                ui.icon("folder", size="28px").classes("text-slate-700")
                ui.label("Minhas Anotações Guardadas").classes("text-2xl sm:text-3xl font-bold text-slate-800")

            filter_busca = ui.input(
                "Pesquisar Anotações", 
                placeholder="Busque por título ou palavras contidas nas anotações..."
            ).props("outlined bg-white dense icon=search").classes("w-full")

            container_cards = ui.column().classes("w-full gap-4 mt-2")

        def preparar_edicao(item: dict):
            anotacao_em_edicao['id'] = item['id']
            titulo_formulario.set_text("✏️ Editar Anotação")
            btn_salvar.set_text("🔄 Atualizar Anotação")
            btn_cancelar.set_visibility(True)

            input_titulo.value = item.get("titulo", "")
            editor_conteudo.value = item.get("conteudo", "")

            ui.run_javascript('window.scrollTo({top: 0, behavior: "smooth"});')
            input_titulo.run_method('focus')

        def confirmar_exclusao(anotacao_id: int):
            with ui.dialog() as dialog, ui.card().classes("p-6 gap-4 border border-slate-200 rounded-2xl max-w-sm w-full"):
                ui.label("⚠️ Confirmar exclusão").classes("text-lg font-bold text-slate-800")
                ui.label("Tem certeza que deseja excluir esta anotação permanentemente?").classes("text-sm text-slate-600")
                
                with ui.row().classes("w-full justify-end gap-2 mt-2"):
                    ui.button("Cancelar", on_click=dialog.close).props("flat").classes("text-slate-600")
                    
                    def efetuar_delecao():
                        try:
                            supabase.table("anotacoes").delete().eq("id", anotacao_id).execute()
                            ui.notify("Anotação excluída!", color="info")
                            dialog.close()
                            carregar_e_renderizar_anotacoes()
                        except Exception as e:
                            ui.notify(f"Erro ao excluir anotação: {e}", color="negative")

                    ui.button("Excluir", on_click=efetuar_delecao).props("unelevated color=negative").classes("rounded-lg")
            dialog.open()

        def carregar_e_renderizar_anotacoes():
            container_cards.clear()

            current_user_id = app.storage.user.get("user_id")
            res = supabase.table("anotacoes").select("*").eq("user_id", current_user_id).order("id", desc=True).execute()
            todas_anotacoes = res.data or []

            termo = filter_busca.value.lower().strip() if filter_busca.value else ""

            anotacoes_filtradas = []
            for item in todas_anotacoes:
                titulo_txt = item.get("titulo", "").lower()
                conteudo_txt = item.get("conteudo", "").lower()

                if termo and not (termo in titulo_txt or termo in conteudo_txt):
                    continue
                anotacoes_filtradas.append(item)

            with container_cards:
                if not anotacoes_filtradas:
                    with ui.card().classes("w-full p-8 text-center bg-slate-50 border border-dashed rounded-xl"):
                        ui.label("Nenhuma anotação encontrada.").classes("text-slate-500 font-bold text-base")
                else:
                    for item in anotacoes_filtradas:
                        renderizar_card_anotacao(item)

        def renderizar_card_anotacao(item):
            a_id = item["id"]
            
            with ui.expansion().classes("w-full border shadow-sm rounded-xl transition-all hover:shadow-md mb-2 bg-white border-purple-200 border-l-8 border-l-purple-600") as expansion:
                
                with expansion.add_slot('header'):
                    with ui.row().classes("w-full items-center justify-between pr-2 gap-2"):
                        with ui.column().classes("gap-0.5 flex-1"):
                            ui.label(item["titulo"]).classes("text-lg sm:text-xl text-slate-800 font-bold")
                            
                            # Formatação precisa com conversão de timezone para Brasília
                            data_fmt = formatar_data_brasilia(item.get("created_at", ""))

                            ui.label(data_fmt).classes("text-xs font-medium text-slate-400")

                        with ui.row().classes("items-center gap-2"):
                            ui.button(
                                icon="edit",
                                on_click=lambda e, it=item: preparar_edicao(it)
                            ).props("flat round dense color=primary").classes("hover:bg-blue-50").tooltip("Editar Anotação")

                            ui.button(
                                icon="delete",
                                on_click=lambda e, aid=a_id: confirmar_exclusao(aid)
                            ).props("flat round dense color=negative").classes("hover:bg-red-50").tooltip("Excluir Anotação")

                with ui.column().classes("w-full p-5 bg-white border-t border-slate-100 overflow-x-auto text-slate-800"):
                    ui.html(item.get("conteudo", "")).classes("w-full prose max-w-none")

        filter_busca.on("update:model-value", carregar_e_renderizar_anotacoes)

        carregar_e_renderizar_anotacoes()


@app.get("/ping")
def ping():
    return {"status": "ok"}


from nicegui import app, ui
import os

# Expõe os arquivos da raiz como arquivos estáticos
app.add_static_files('/static', '.')

# Adiciona o link do manifesto, ícone da Apple e registra o Service Worker em todas as páginas
ui.add_head_html('''
    <link rel="manifest" href="/static/manifest.json">
    <link rel="apple-touch-icon" href="icon.png">
    <meta name="mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <script>
      if ('serviceWorker' in navigator) {
        navigator.serviceWorker.register('/static/sw.js');
      }
    </script>
''', shared=True)

ui.run(
    title="Agendamentos Pessoais",
    favicon="icon.png",  # Aponta para a rota estática criada acima
    host="0.0.0.0",
    port=PORT,
    storage_secret=os.getenv("STORAGE_SECRET", "chave_secreta_padrao_substituir_em_producao"),
)