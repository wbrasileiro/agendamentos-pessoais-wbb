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

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Configuração OAuth 2.0 Web do Google Calendar via .env
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

PORT = int(os.environ.get("PORT", 8080))
REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", f"http://localhost:{PORT}/oauth2callback")

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

    return RedirectResponse(url="/?auth=success")


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
            "description": f"Aviso de boleto da empresa {empresa} no valor de R$ {valor:.2f}.\nVencimento: {data_venc.strftime('%d/%m/%Y')}",
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
                ui.icon("event_note", size="28px").classes("text-blue-700 font-bold")
                ui.label("Agendamentos").classes("text-xl font-black")
            ui.label(user_email if user_email else "Minha Conta").classes("text-xs text-slate-500 truncate")

        with ui.column().classes("w-full p-4 gap-2 flex-1"):
            with ui.button(on_click=lambda: navegar("/")).props("flat no-caps align=left").classes(
                "w-full hover:bg-slate-200 rounded-lg py-2 px-3"
            ):
                ui.label("➕ Cadastrar Boleto").classes("font-bold text-sm")

            with ui.button(on_click=lambda: navegar("/dashboard")).props("flat no-caps align=left").classes(
                "w-full hover:bg-slate-200 rounded-lg py-2 px-3"
            ):
                ui.label("📊 Dashboard & Boletos").classes("font-bold text-sm")

            if app.storage.user.get("is_admin", False) or user_email == ADMIN_EMAIL:
                ui.separator().classes("my-2")
                ui.label("ADMINISTRAÇÃO").classes("text-[10px] font-bold text-amber-600 px-3")
                with ui.button(on_click=lambda: navegar("/admin")).props("flat no-caps align=left").classes(
                    "w-full hover:bg-amber-100/50 rounded-lg py-2 px-3"
                ):
                    ui.label("⚙️ Painel de Manutenção").classes("font-bold text-sm text-amber-950")

        # Rodapé do Drawer: Logout + Crédito Discreto
        with ui.column().classes("w-full gap-4 pt-4 border-t border-slate-200 mt-auto"):
            def fazer_logout():
                app.storage.user.clear()
                ui.navigate.to("/login")

            with ui.row().classes("w-full items-center gap-3 p-3 rounded-xl hover:bg-red-50 text-red-600 cursor-pointer transition-all") \
                    .on("click", fazer_logout):
                ui.icon("logout", size="24px")
                ui.label("Sair da Conta").classes("text-base font-bold")

            # Desenvolvedor
            ui.label("Desenvolvido por Wellington Batista Brasileiro") \
                .classes("w-full text-center text-[11px] font-medium text-slate-400 py-1 tracking-tight opacity-75")                

    return drawer


def cabecalho_app(drawer):
    user_email = app.storage.user.get("email", "Usuário")
    with ui.header().classes("bg-blue-900 text-white justify-between items-center p-3 w-full"):
        ui.button(icon="menu", on_click=drawer.toggle).props("flat color=white")
        ui.label("Agendamentos Pessoais").classes("text-lg font-bold")
        ui.label(user_email.split("@")[0]).classes("text-xs bg-blue-700 px-2 py-1 rounded")


# --- TELA DE LOGIN ---
@ui.page("/login")
def login_page():
    def abrir_modal_solicitacao():
        with ui.dialog() as dialog, ui.card().classes("w-full max-w-sm p-4"):
            ui.label("Solicitar Acesso").classes("text-xl font-bold text-gray-800 mb-1")
            ui.label(
                "ℹ️ Seu número de telefone e e-mail serão utilizados para validação de acesso e notificações."
            ).classes("text-xs text-blue-800 bg-blue-50 p-2 rounded mb-3 border border-blue-200 font-medium")

            solicita_email = ui.input("E-mail").props("outlined").classes("w-full mb-2")
            solicita_telefone = ui.input("Telefone / WhatsApp (com DDD)").props("outlined").classes("w-full mb-2")
            solicita_senha = ui.input("Senha desejada", password=True, password_toggle_button=True).props("outlined").classes("w-full mb-4")

            async def processar_solicitacao():
                email_txt = (solicita_email.value or "").strip().lower()
                telefone_txt = (solicita_telefone.value or "").strip()
                senha_txt = (solicita_senha.value or "").strip()

                if not email_valido(email_txt) or not telefone_txt or not senha_txt:
                    ui.notify("Preencha todos os campos corretamente!", color="warning")
                    return

                e_valido, msg_erro = validar_telefone(telefone_txt)
                if not e_valido:
                    ui.notify(msg_erro, color="negative", size="lg")
                    return

                user_agent = str(ui.context.client.environ.get("HTTP_USER_AGENT", "Dispositivo Móvel"))[:150]
                loc_text = "Não informada"

                try:
                    ip_cliente = ui.context.client.environ.get("REMOTE_ADDR", "")
                    ip_data = requests.get(f"https://ipapi.co/{ip_cliente}/json/", timeout=2).json()
                    loc_text = f"{ip_data.get('city')}, {ip_data.get('region')}"
                except Exception:
                    pass

                try:
                    supabase.table("solicitacoes_acesso").insert({
                        "created_at": obter_hora_brasilia().isoformat(),
                        "email": email_txt,
                        "telefone": telefone_txt,
                        "senha_temporaria": senha_txt,
                        "dispositivo": user_agent,
                        "localizacao": loc_text,
                    }).execute()

                    dialog.close()
                    asyncio.create_task(asyncio.to_thread(enviar_notificacao_email, email_txt, telefone_txt, user_agent, loc_text))
                    ui.notify("Solicitação enviada com sucesso ao Administrador!", color="positive")
                except Exception as e:
                    ui.notify(f"Erro ao salvar solicitação: {e}", color="negative")

            ui.button("ENVIAR SOLICITAÇÃO", on_click=processar_solicitacao).classes("w-full bg-blue-600 text-white font-bold mb-2")
            ui.button("CANCELAR", on_click=dialog.close).props("flat").classes("w-full text-gray-600")

        dialog.open()

    with ui.card().classes("w-11/12 max-w-sm absolute-center p-6 shadow-xl rounded-xl"):
        ui.label("Agendamentos Pessoais").classes("text-2xl font-bold text-blue-800 text-center w-full mb-4")
        email = ui.input("E-mail").props("outlined").classes("w-full mb-2")
        password = ui.input("Senha", password=True, password_toggle_button=True).props("outlined").classes("w-full mb-4")

        def try_login():
            email_val = email.value.strip().lower() if email.value else ""
            pwd_val = password.value.strip() if password.value else ""

            res = supabase.table("perfis_usuarios").select("*").eq("email", email_val).execute()
            users = res.data or []

            if users and users[0].get("senha") == pwd_val:
                if not users[0].get("ativo", True):
                    ui.notify("Usuário inativo! Fale com o administrador.", color="negative")
                    return
                app.storage.user["user_id"] = users[0]["id"]
                app.storage.user["email"] = users[0]["email"]
                app.storage.user["is_admin"] = users[0].get("is_admin", False)
                ui.navigate.to("/")
            else:
                ui.notify("E-mail ou senha incorretos!", color="negative")

        ui.button("ENTRAR", on_click=try_login).classes("w-full bg-blue-600 text-white font-bold mb-3")
        ui.separator().classes("my-2")
        ui.button("SOLICITAR ACESSO", on_click=abrir_modal_solicitacao).props("flat dense").classes("w-full text-blue-500 font-medium text-xs mt-2")


# ==========================================
# 1. TELA DE CADASTRO DE BOLETOS
# ==========================================
@ui.page("/")
def home_page():
    if not app.storage.user.get("user_id"):
        ui.navigate.to("/login")
        return

    drawer = menu_drawer()
    cabecalho_app(drawer)
    user_id = app.storage.user.get("user_id")

    # Busca categoria "Boleto" no banco sem exibir o campo visualmente
    categoria_boleto_id = None
    try:
        cats_res = supabase.table("dim_categorias").select("*").execute()
        if cats_res.data:
            for c in cats_res.data:
                if "boleto" in c["nome"].lower():
                    categoria_boleto_id = c["id"]
                    break
            if not categoria_boleto_id and len(cats_res.data) > 0:
                categoria_boleto_id = cats_res.data[0]["id"]
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
                    "Para cadastrar seus boletos e receber alertas automáticos de vencimento, "
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
                    ui.label("➕ Cadastrar novo boleto").classes("text-2xl sm:text-3xl font-bold text-slate-800")
                    ui.label("✅ Conectado ao Google Calendar").classes(
                        "text-xs sm:text-sm font-bold text-green-800 bg-green-100 px-3 py-1.5 rounded-lg border border-green-300"
                    )

                # Padronização de fontes para todos os campos
                input_props = "outlined bg-slate-50 input-class=text-base"

                # Campos Principais (Campo Categoria Removido)
                with ui.column().classes("w-full gap-5"):
                    input_empresa = ui.input(
                        "Empresa / Nome do boleto",
                        placeholder="Ex: Boticário, Eudora..."
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
                    select_status.value = "Pendente"
                    check_lembrete.value = True
                    select_antecedencia.value = 1
                    input_horario.value = "12:00"

                async def salvar_boleto():
                    empresa_val = input_empresa.value.strip() if input_empresa.value else ""
                    valor_val = float(input_valor.value) if input_valor.value else 0.0
                    vencimento_val = input_vencimento.value

                    if not empresa_val or not valor_val or not vencimento_val:
                        ui.notify("Por favor, preencha empresa, valor e vencimento!", color="warning", size="lg")
                        return

                    try:
                        dup_res = supabase.table("boletos").select("id").eq("user_id", user_id).eq("empresa", empresa_val).eq("valor", valor_val).eq("data_vencimento", vencimento_val).execute()
                        if dup_res.data and len(dup_res.data) > 0:
                            ui.notify("⚠️ Atenção: Este boleto já está cadastrado no sistema!", color="warning", size="lg")
                            return

                        status_formatado = str(select_status.value).strip().capitalize() if select_status.value else "Pendente"

                        payload = {
                            "user_id": user_id,
                            "empresa": empresa_val,
                            "valor": valor_val,
                            "data_vencimento": vencimento_val,
                            "categoria_id": categoria_boleto_id,
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

                        ui.notify("✅ Boleto salvo com sucesso!", color="positive", size="lg")
                        limpar_formulario()

                    except Exception as err:
                        ui.notify(f"❌ Erro ao salvar o boleto: {err}", color="negative", size="lg")

                # Botão atualizado para Title Case: "💾 Salvar boleto"
                ui.button("💾 Salvar boleto", on_click=salvar_boleto).classes(
                    "bg-green-600 hover:bg-green-700 text-white font-bold text-lg mt-3 w-full py-3.5 rounded-xl shadow"
                )



# ==========================================
# 2. TELA DE GESTÃO DE BOLETOS (REATIVIDADE & VISUAL ACESSÍVEL)
# ==========================================
@ui.page("/dashboard")
def dashboard_page():
    if not app.storage.user.get("user_id"):
        ui.navigate.to("/login")
        return

    drawer = menu_drawer()
    cabecalho_app(drawer)
    user_id = app.storage.user.get("user_id")

    cats_res = supabase.table("dim_categorias").select("*").execute()
    categorias_list = {c["id"]: c["nome"] for c in (cats_res.data or [])}

    def carregar_dados():
        res = supabase.table("boletos").select("*").eq("user_id", user_id).order("data_vencimento").execute()
        dados = res.data or []
        for item in dados:
            st = str(item.get("status") or "PENDENTE").strip().upper()
            item["status_norm"] = st
        return dados

    # Variáveis Globais da Tela
    filtro_status = {"valor": "TODOS"}

    with ui.column().classes("w-full max-w-6xl mx-auto p-4 gap-6 pb-32"):
        ui.label("📊 Gestão de boletos").classes("text-3xl font-black text-slate-800 tracking-tight")

        # Containers reativos para Big Numbers e Gráficos
        container_bignumbers = ui.row().classes("w-full grid grid-cols-1 sm:grid-cols-3 gap-4")
        container_graficos = ui.column().classes("w-full gap-4")

        # 2. Filtros de Pesquisa Recolhidos
        with ui.expansion("🔍 Clique aqui para filtrar boletos", icon="filter_alt").classes("w-full border border-slate-300 bg-slate-50 rounded-2xl text-base font-bold text-slate-700 p-2 shadow-sm"):
            with ui.column().classes("w-full p-2 gap-3"):
                with ui.grid().classes("w-full grid-cols-1 sm:grid-cols-3 gap-3"):
                    input_busca = ui.input("Empresa / Descrição", placeholder="Ex: Luz, Eudora...").props("outlined bg-white text-base")
                    input_v_min = ui.number("Valor Mínimo (R$)", format="%.2f").props("outlined bg-white text-base")
                    input_v_max = ui.number("Valor Máximo (R$)", format="%.2f").props("outlined bg-white text-base")

                with ui.grid().classes("w-full grid-cols-1 sm:grid-cols-2 gap-3"):
                    input_dt_ini = ui.input("Vencimento De").props("type=date outlined bg-white text-base")
                    input_dt_fim = ui.input("Vencimento Até").props("type=date outlined bg-white text-base")

        # 5. Botões de Filtro Congelados no Topo (Sticky Header)
        with ui.element("div").classes("w-full sticky top-0 z-20 bg-slate-100/90 backdrop-blur-md py-3 px-1 border-b border-slate-200"):
            ui.label("Filtrar por situação:").classes("text-xs font-bold text-slate-500 uppercase tracking-wider mb-1")
            with ui.row().classes("w-full justify-start gap-2 flex-wrap"):
                def set_status_filtro(st):
                    filtro_status["valor"] = st
                    renderizar_cards()

                ui.button("Todos", on_click=lambda: set_status_filtro("TODOS")).classes("bg-slate-800 text-white text-sm font-bold px-4 py-2 rounded-xl")
                ui.button("Pendentes", on_click=lambda: set_status_filtro("PENDENTE")).classes("bg-amber-600 text-white text-sm font-bold px-4 py-2 rounded-xl")
                ui.button("Pagos", on_click=lambda: set_status_filtro("PAGO")).classes("bg-green-700 text-white text-sm font-bold px-4 py-2 rounded-xl")
                ui.button("Atrasados", on_click=lambda: set_status_filtro("ATRASADO")).classes("bg-red-700 text-white text-sm font-bold px-4 py-2 rounded-xl")
                ui.button("Cancelados", on_click=lambda: set_status_filtro("CANCELADO")).classes("bg-gray-600 text-white text-sm font-bold px-4 py-2 rounded-xl")

        container_cards = ui.column().classes("w-full gap-4 mt-2")

        # Função de recálculo dos Big Numbers e Gráficos
        def atualizar_dashboard(boletos_dados):
            container_bignumbers.clear()
            container_graficos.clear()
            hoje = datetime.now().date()

            total_a_vencer = sum(float(b.get("valor", 0)) for b in boletos_dados if b["status_norm"] == "PENDENTE" and datetime.strptime(b["data_vencimento"], "%Y-%m-%d").date() >= hoje)
            cnt_a_vencer = sum(1 for b in boletos_dados if b["status_norm"] == "PENDENTE" and datetime.strptime(b["data_vencimento"], "%Y-%m-%d").date() >= hoje)

            total_atrasados = sum(float(b.get("valor", 0)) for b in boletos_dados if b["status_norm"] == "ATRASADO" or (b["status_norm"] == "PENDENTE" and datetime.strptime(b["data_vencimento"], "%Y-%m-%d").date() < hoje))
            cnt_atrasados = sum(1 for b in boletos_dados if b["status_norm"] == "ATRASADO" or (b["status_norm"] == "PENDENTE" and datetime.strptime(b["data_vencimento"], "%Y-%m-%d").date() < hoje))

            total_pagos = sum(float(b.get("valor", 0)) for b in boletos_dados if b["status_norm"] == "PAGO")
            cnt_pagos = sum(1 for b in boletos_dados if b["status_norm"] == "PAGO")

            # Renderiza Big Numbers
            with container_bignumbers:
                with ui.card().classes("p-5 bg-blue-50 border-2 border-blue-200 rounded-2xl shadow-sm w-full"):
                    ui.label("Boletos a Vencer").classes("text-sm font-bold text-blue-900 uppercase tracking-wider")
                    ui.label(f"R$ {formatar_br(total_a_vencer)}").classes("text-3xl font-black text-blue-900 my-1")
                    ui.label(f"Quantidade: {cnt_a_vencer}").classes("text-sm text-blue-800 font-semibold")

                with ui.card().classes("p-5 bg-red-50 border-2 border-red-200 rounded-2xl shadow-sm w-full"):
                    ui.label("Boletos Atrasados").classes("text-sm font-bold text-red-900 uppercase tracking-wider")
                    ui.label(f"R$ {formatar_br(total_atrasados)}").classes("text-3xl font-black text-red-900 my-1")
                    ui.label(f"Quantidade: {cnt_atrasados}").classes("text-sm text-red-800 font-semibold")

                with ui.card().classes("p-5 bg-green-50 border-2 border-green-200 rounded-2xl shadow-sm w-full"):
                    ui.label("Boletos Pagos").classes("text-sm font-bold text-green-900 uppercase tracking-wider")
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
                            ui.label("Quantidade de Boletos Pendentes").classes("text-base font-bold text-slate-700")
                            ui.echart({
                                "xAxis": {"type": "category", "data": meses_ordenados},
                                "yAxis": {"type": "value"},
                                "series": [{"data": qtdes_pendentes, "type": "bar", "color": "#f59e0b", "barWidth": "40%"}],
                                "tooltip": {"trigger": "axis"}
                            }).classes("h-64 w-full")

                        with ui.card().classes("w-full p-4 border border-slate-200 rounded-2xl bg-white shadow-sm"):
                            ui.label("Valor Total Pendente (R$)").classes("text-base font-bold text-slate-700")
                            ui.echart({
                                "xAxis": {"type": "category", "data": meses_ordenados},
                                "yAxis": {"type": "value"},
                                "series": [{"data": valores_pendentes, "type": "line", "color": "#d97706", "smooth": True, "areaStyle": {}}],
                                "tooltip": {"trigger": "axis"}
                            }).classes("h-64 w-full")

        # Função de Renderização Geral dos Cards e atualização da tela
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

                if match_txt and match_vmin and match_vmax and match_dti and match_dtf and match_st:
                    filtrados.append(b)

            with container_cards:
                if not filtrados:
                    ui.label("Nenhum boleto encontrado.").classes("text-slate-500 text-base italic py-6 text-center w-full bg-white rounded-2xl border border-slate-200")
                    return

                for b in filtrados:
                    boleto_id = b["id"]
                    status_norm = b.get("status_norm", "PENDENTE")
                    status_exibicao = status_norm.capitalize()

                    # Definição visual por status (Ícone, Badge e Cores de Alto Contraste)
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

                    # Layout do Card com Acessibilidade Avançada
                    with ui.card().classes(f"w-full p-5 bg-white border border-slate-200 rounded-2xl shadow-sm flex-col gap-3 {border_color}"):
                        
                        # Topo do Card: Badge de Status Bem Visível
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

                        # Rodapé do Card: Alteração de Status e Ações
                        with ui.row().classes("w-full justify-between items-center gap-3 flex-wrap"):
                            with ui.row().classes("items-center gap-2"):
                                ui.label("Alterar para:").classes("text-sm font-bold text-slate-700")

                                def atualizar_status(e, bid=boleto_id):
                                    val_salvar = str(e.value).upper()
                                    supabase.table("boletos").update({"status": val_salvar}).eq("id", bid).execute()
                                    ui.notify(f"Situação alterada para {e.value}!", color="positive")
                                    renderizar_cards()

                                ui.select(
                                    ["Pendente", "Pago", "Atrasado", "Cancelado"],
                                    value=status_exibicao,
                                    on_change=atualizar_status
                                ).props("dense outlined text-base").classes("w-40 font-bold")

                            with ui.row().classes("items-center gap-1"):
                                def abrir_modal_edicao(boleto=b):
                                    with ui.dialog() as dlg, ui.card().classes("w-full max-w-md p-6 gap-4 rounded-2xl"):
                                        ui.label("Editar Boleto").classes("text-xl font-bold text-slate-800")
                                        e_emp = ui.input("Empresa", value=boleto["empresa"]).props("outlined text-base")
                                        e_val = ui.number("Valor (R$)", value=boleto["valor"], format="%.2f").props("outlined text-base")
                                        e_venc = ui.input("Vencimento", value=boleto["data_vencimento"]).props("type=date outlined text-base")

                                        def salvar_edicao():
                                            supabase.table("boletos").update({
                                                "empresa": e_emp.value,
                                                "valor": float(e_val.value),
                                                "data_vencimento": e_venc.value,
                                            }).eq("id", boleto["id"]).execute()
                                            dlg.close()
                                            ui.notify("Boleto alterado com sucesso!", color="positive")
                                            renderizar_cards()

                                        with ui.row().classes("w-full justify-end gap-3 mt-2"):
                                            ui.button("Cancelar", on_click=dlg.close).props("flat text-base")
                                            ui.button("Salvar", on_click=salvar_edicao).classes("bg-blue-600 text-white font-bold px-4 py-2 rounded-xl")
                                    dlg.open()

                                def confirmar_exclusao(bid=boleto_id, nome=b.get("empresa")):
                                    with ui.dialog() as dlg_del, ui.card().classes("p-6 max-w-sm gap-4 rounded-2xl"):
                                        ui.label("Confirmar Exclusão").classes("text-xl font-bold text-red-700")
                                        ui.label(f"Deseja realmente apagar o boleto da '{nome}'?").classes("text-base text-slate-700")

                                        def excluir():
                                            supabase.table("boletos").delete().eq("id", bid).execute()
                                            dlg_del.close()
                                            ui.notify("Boleto excluído!", color="warning")
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
        renderizar_cards()


# ==========================================
# PAINEL EXCLUSIVO DO ADMIN
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
        ui.label("⚙️ Painel do Administrador").classes("text-amber-900 font-bold text-2xl")

        solicitacoes = supabase.table("solicitacoes_acesso").select("*").execute().data or []

        if solicitacoes:
            with ui.card().classes("w-full p-4 border border-amber-200 bg-white shadow-sm"):
                ui.label("Solicitações Pendentes de Acesso").classes("text-lg font-bold mb-2")

                for sol in solicitacoes:
                    with ui.row().classes("w-full items-center justify-between p-2 border-b"):
                        with ui.column().classes("gap-0"):
                            ui.label(f"📧 {sol['email']}").classes("font-bold text-sm")
                            ui.label(f"📞 Tel: {sol.get('telefone', 'Não informado')}").classes("text-xs text-gray-600")

                        with ui.row().classes("gap-2"):
                            async def aprovar(s=sol):
                                telefone_informado = s.get("telefone") or ""
                                e_valido, msg_erro = validar_telefone(telefone_informado)
                                if not e_valido:
                                    ui.notify(f"Erro ao aprovar {s['email']}: {msg_erro}", color="negative")
                                    return

                                email_usuario = s["email"].strip()

                                try:
                                    payload_perfil = {
                                        "email": email_usuario,
                                        "email_notificacao": email_usuario,
                                        "telefone": telefone_informado.strip(),
                                        "senha": s["senha_temporaria"],
                                        "ativo": True,
                                        "is_admin": False,
                                    }

                                    supabase.table("perfis_usuarios").insert(payload_perfil).execute()
                                    supabase.table("solicitacoes_acesso").delete().eq("id", s["id"]).execute()
                                    ui.notify(f"Acesso aprovado para {email_usuario}", color="positive")
                                    ui.navigate.reload()
                                except Exception as err:
                                    ui.notify(f"Erro ao aprovar: {err}", color="negative")

                            async def rejeitar(s=sol):
                                supabase.table("solicitacoes_acesso").delete().eq("id", s["id"]).execute()
                                ui.notify(f"Solicitação de {s['email']} rejeitada.", color="warning")
                                ui.navigate.reload()

                            ui.button("APROVAR", on_click=aprovar).classes("bg-blue-600 text-white text-xs font-bold")
                            ui.button("REJEITAR", on_click=rejeitar).classes("bg-red-600 text-white text-xs font-bold")

        usuarios = supabase.table("perfis_usuarios").select("*").order("email").execute().data or []

        with ui.card().classes("w-full p-5 border border-amber-200 bg-white shadow-sm rounded-xl"):
            ui.label("👥 Usuários Cadastrados").classes("text-lg font-bold text-slate-800 mb-3")

            for usr in usuarios:
                def alternar_status(u=usr):
                    novo_status = not u.get("ativo", True)
                    supabase.table("perfis_usuarios").update({"ativo": novo_status}).eq("id", u["id"]).execute()
                    ui.notify(f"Status de {u['email']} alterado!", color="info")
                    ui.navigate.reload()

                with ui.row().classes("w-full justify-between items-center border-b border-gray-100 py-3 gap-4"):
                    with ui.column().classes("gap-1 flex-1"):
                        ui.label(usr.get("email", "")).classes("font-bold text-base text-slate-800")
                        tel = usr.get("telefone") or usr.get("whatsapp") or "Não informado"
                        ui.label(f"📞 Telefone: {tel}").classes("text-xs text-gray-700 font-medium")

                    if usr.get("email") != ADMIN_EMAIL:
                        ui.button("ALTERAR STATUS", on_click=alternar_status).classes("bg-amber-500 text-white text-xs font-bold")


@app.get("/ping")
def ping():
    return {"status": "ok"}


ui.run(
    host="0.0.0.0",
    port=PORT,
    storage_secret=os.getenv("STORAGE_SECRET", "chave_secreta_padrao_substituir_em_producao"),
)