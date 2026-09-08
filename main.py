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
                    with ui.row().classes("items-center justify-between w-full"):
                        with ui.row().classes("items-center gap-3"):
                            ui.icon("check_box", size="20px").classes("text-purple-600")
                            ui.label("Tarefas").classes("font-bold text-sm")
                        ui.label("Em breve").classes("text-[10px] font-bold bg-purple-100 text-purple-700 px-2 py-0.5 rounded-full")

                # Anotações
                with ui.button(on_click=lambda: navegar("/anotacoes")).props("flat no-caps align=left").classes(
                    "w-full hover:bg-purple-50 text-slate-700 hover:text-purple-700 rounded-xl py-2 px-3 transition-all"
                ):
                    with ui.row().classes("items-center justify-between w-full"):
                        with ui.row().classes("items-center gap-3"):
                            ui.icon("sticky_note_2", size="20px").classes("text-purple-600")
                            ui.label("Anotações").classes("font-bold text-sm")
                        ui.label("Em breve").classes("text-[10px] font-bold bg-purple-100 text-purple-700 px-2 py-0.5 rounded-full")

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

                    with ui.grid().classes("w-full grid-cols-1 sm:grid-cols-2 gap-5"):
                        # Campo de Seleção de Categoria
                        select_categoria = ui.select(
                            options=opcoes_categorias,
                            value=categoria_padrao_id,
                            label="Categoria"
                        ).props(input_props).classes("w-full")

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

                # 1. Sobe a tela suavemente até a âncora/topo
                ui.run_javascript(f'document.getElementById("{topo_ancora.id}").scrollIntoView({{behavior: "smooth", block: "start"}});')

                # 2. Seleciona e foca no campo de Título para que o teclado/foco abra no local correto
                ui.run_javascript(f'document.getElementById("{input_titulo.id}").focus();')


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


@app.get("/ping")
def ping():
    return {"status": "ok"}


ui.run(
    host="0.0.0.0",
    port=PORT,
    storage_secret=os.getenv("STORAGE_SECRET", "chave_secreta_padrao_substituir_em_producao"),
)