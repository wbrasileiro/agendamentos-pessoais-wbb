import asyncio
import io
import logging
import os
import re
import smtplib
import subprocess
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
import easyocr
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from nicegui import app, run, ui
from PIL import Image
import pytesseract
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

# Inicializa o leitor EasyOCR em Português e Inglês
reader_ocr = easyocr.Reader(["pt", "en"], gpu=False)


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


def orientar_imagem(img_stream, callback_status=None):
    """Detecta a orientação do texto na imagem e a ajusta para leitura."""
    if callback_status:
        callback_status("Ajustando a orientação da imagem...")

    image = Image.open(img_stream).convert("RGB")
    try:
        osd = pytesseract.image_to_osd(image)
        angle = int([line.split(":")[1] for line in osd.split("\n") if "Rotate:" in line][0].strip())
        if angle != 0:
            image = image.rotate(360 - angle, expand=True)
    except Exception:
        pass

    output_stream = io.BytesIO()
    image.save(output_stream, format="JPEG")
    output_stream.seek(0)
    return output_stream


def extrair_dados_imagem_easyocr(img_stream, callback_status=None):
    """
    Extrai informações do documento (Empresa, Valor, Vencimento, Código de Barras).
    Aceita 'callback_status' para reportar o progresso em linguagem amigável.
    """
    img_stream = orientar_imagem(img_stream, callback_status)

    if callback_status:
        callback_status("Analisando o texto do documento...")

    image = Image.open(img_stream)
    detalhes = reader_ocr.readtext(image, detail=1)
    texto_full = " ".join([item[1] for item in detalhes])

    empresa = None
    valor = None
    vencimento = None
    codigo_barras = None

    if callback_status:
        callback_status("Localizando código de barras e dados de pagamento...")

    match_fmt = re.search(
        r'\b(\d{5}[\.\s]?\d{5}\s+\d{5}[\.\s]?\d{6}\s+\d{5}[\.\s]?\d{6}\s+\d\s+\d{14})\b',
        texto_full
    )
    if match_fmt:
        codigo_barras = re.sub(r"\D", "", match_fmt.group(1))

    if not codigo_barras:
        apenas_numeros = re.sub(r"\D", "", texto_full)
        match_ld = re.search(r"\d{47}", apenas_numeros)
        if match_ld:
            codigo_barras = match_ld.group(0)
        else:
            match_ruido = re.search(r"\d{49,53}", apenas_numeros)
            if match_ruido:
                cand = match_ruido.group(0)
                codigo_barras = cand[-47:]

    if codigo_barras and len(codigo_barras) == 47:
        fator_venc = int(codigo_barras[33:37])
        if fator_venc > 0:
            base_date = datetime(2022, 5, 29) if fator_venc >= 1000 else datetime(1997, 10, 7)
            dt_venc = base_date + timedelta(days=fator_venc)
            vencimento = dt_venc.strftime("%Y-%m-%d")

        val_centavos = int(codigo_barras[37:47])
        if val_centavos > 0:
            valor = val_centavos / 100.0

    if not valor:
        candidatos_valor = re.findall(r"\b(\d{1,3}(?:\.\d{3})*,\d{2})\b", texto_full)
        for cand in candidatos_valor:
            try:
                val_flt = float(cand.replace(".", "").replace(",", "."))
                if val_flt >= 5.0:
                    valor = val_flt
                    break
            except ValueError:
                continue

    if not vencimento:
        match_venc = re.search(r"Vencimento\s*:?\s*(\d{2}/\d{2}/\d{4})", texto_full, re.IGNORECASE)
        if match_venc:
            try:
                vencimento = datetime.strptime(match_venc.group(1), "%d/%m/%Y").strftime("%Y-%m-%d")
            except ValueError:
                pass

        if not vencimento:
            datas = re.findall(r"\b(\d{2}/\d{2}/\d{4})\b", texto_full)
            if datas:
                try:
                    vencimento = datetime.strptime(datas[0], "%d/%m/%Y").strftime("%Y-%m-%d")
                except ValueError:
                    pass

    if callback_status:
        callback_status("Identificando a empresa / beneficiário...")

    termos_proibidos = [
        "BANCO", "BRADESCO", "ITAU", "SANTANDER", "CAIXA", "BRASIL",
        "LOCAL DE PAGAMENTO", "PAGAVEL", "PREFERENCIALMENTE", "VENCIMENTO",
        "CARTEIRA", "ESPECIE", "COMPROVANTE", "FICHA DE COMPENSACAO",
    ]

    for item in detalhes:
        txt = item[1].strip()
        txt_upper = txt.upper()
        if re.search(r"\b(LTDA|S\.?A\.?|ME|EPP|EIRELI)\b", txt_upper):
            txt_limpo = re.sub(r"^(BENEFICI[ÁA]RIO|CEDENTE|RAZ[ÃA]O SOCIAL|NOME)\s*:?", "", txt, flags=re.IGNORECASE).strip()
            if not any(tp in txt_upper for tp in termos_proibidos):
                empresa = txt_limpo
                break

    if not empresa:
        for i, item in enumerate(detalhes):
            txt = item[1].strip()
            if re.search(r"^(Benefici[áa]rio|Cedente)", txt, re.IGNORECASE):
                sub_txt = re.sub(r"^(Benefici[áa]rio|Cedente)\s*:?", "", txt, flags=re.IGNORECASE).strip()
                if len(sub_txt) > 3 and not any(tp in sub_txt.upper() for tp in termos_proibidos):
                    empresa = sub_txt
                    break
                if i + 1 < len(detalhes):
                    prox_txt = detalhes[i + 1][1].strip()
                    if len(prox_txt) > 3 and not any(tp in prox_txt.upper() for tp in termos_proibidos):
                        empresa = prox_txt
                        break

    if not empresa:
        for item in detalhes:
            txt = item[1].strip()
            txt_upper = txt.upper()
            if len(txt) > 4 and not any(tp in txt_upper for tp in termos_proibidos):
                if not re.search(r"^\d+$", txt):
                    empresa = txt
                    break

    if empresa:
        match_sufixo = re.search(r"^(.*?\b(?:LTDA|S\.?A\.?|ME|EPP|EIRELI)\b)", empresa, re.IGNORECASE)
        if match_sufixo:
            empresa = match_sufixo.group(1).strip()
        else:
            empresa = re.split(r"(?:\(|CNPJ|CPF|AV\.|AVENIDA|RUA)", empresa, flags=re.IGNORECASE)[0].strip()
        empresa = re.sub(r"[^\w]+$", "", empresa).strip()

    if callback_status:
        callback_status("Processamento concluído com sucesso!")

    return empresa, valor, vencimento, codigo_barras


# --- NOTIFICAÇÕES (TELEGRAM & E-MAIL) ---
def enviar_notificacao_telegram(email_solicitante: str, telefone: str, dispositivo: str, localizacao: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    mensagem = (
        f"🚨 <b>SOLICITAÇÃO DE ACESSO - AGENDAMENTOS</b>\n\n"
        f"📧 <b>E-mail:</b> {email_solicitante}\n"
        f"📞 <b>Telefone/Alertas:</b> {telefone}\n"
        f"📱 <b>Dispositivo:</b> {dispositivo[:60]}\n"
        f"📍 <b>Localização:</b> {localizacao}"
    )
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": mensagem, "parse_mode": "HTML"}, timeout=5)
    except Exception as e:
        print(f"Erro Telegram: {e}")


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
                ui.label("📅 Meus Boletos e Alertas").classes("font-bold text-sm")

            if app.storage.user.get("is_admin", False) or user_email == ADMIN_EMAIL:
                ui.separator().classes("my-2")
                ui.label("ADMINISTRAÇÃO").classes("text-[10px] font-bold text-amber-600 px-3")
                with ui.button(on_click=lambda: navegar("/admin")).props("flat no-caps align=left").classes(
                    "w-full hover:bg-amber-100/50 rounded-lg py-2 px-3"
                ):
                    ui.label("⚙️ Painel de Manutenção").classes("font-bold text-sm text-amber-950")

        with ui.column().classes("w-full p-4 border-t bg-white gap-2"):
            with ui.button(
                on_click=lambda: (
                    drawer.hide(),
                    app.storage.user.clear(),
                    ui.navigate.to("/login"),
                )
            ).props("flat no-caps align=left").classes("w-full hover:bg-red-50 rounded-lg py-2 px-3"):
                ui.label("Sair da Conta").classes("font-bold text-sm text-red-600")

    return drawer


def cabecalho_app(drawer):
    user_email = app.storage.user.get("email", "Usuário")
    with ui.header().classes("bg-blue-900 text-white justify-between items-center p-3 w-full"):
        ui.button(icon="menu", on_click=drawer.toggle).props("flat color=white")
        ui.label("Agendamentos Pessoais").classes("text-lg font-bold")
        ui.label(user_email.split("@")[0]).classes("text-xs bg-blue-700 px-2 py-1 rounded")


# --- TELA DE LOGIN & SOLICITAÇÃO ---
@ui.page("/login")
def login_page():
    def abrir_modal_solicitacao():
        with ui.dialog() as dialog, ui.card().classes("w-full max-w-sm p-4"):
            ui.label("Solicitar Acesso").classes("text-xl font-bold text-gray-800 mb-1")
            
            # Mensagem explicativa do cadastro de telefone
            ui.label(
                "ℹ️ Seu número de telefone será utilizado para o envio automático de alertas de vencimento."
            ).classes("text-xs text-blue-800 bg-blue-50 p-2 rounded mb-3 border border-blue-200 font-medium")

            solicita_email = ui.input("E-mail").props("outlined").classes("w-full mb-2")
            solicita_telefone = (
                ui.input("Telefone / WhatsApp (com DDD)", placeholder="(11) 99999-9999")
                .props("outlined")
                .classes("w-full mb-2")
            )
            solicita_senha = (
                ui.input("Senha desejada", password=True, password_toggle_button=True)
                .props("outlined")
                .classes("w-full mb-4")
            )

            async def processar_solicitacao():
                            email_txt = (solicita_email.value or "").strip().lower()
                            telefone_txt = (solicita_telefone.value or "").strip()
                            senha_txt = (solicita_senha.value or "").strip()

                            # 1. Validação de preenchimento dos campos
                            if not email_valido(email_txt) or not telefone_txt or not senha_txt:
                                ui.notify("Preencha todos os campos corretamente!", color="warning")
                                return

                            # 2. VALIDAÇÃO DO FORMATO DO TELEFONE (Cole aqui)
                            e_valido, msg_erro = validar_telefone(telefone_txt)
                            if not e_valido:
                                ui.notify(msg_erro, color="negative", size="lg")
                                return  # Interrompe o envio se o telefone contiver traços ou formato inválido                            

                            user_agent = str(ui.context.client.environ.get("HTTP_USER_AGENT", "Dispositivo Móvel"))[:150]
                            loc_text = "Não informada"

                            try:
                                ip_cliente = ui.context.client.environ.get("REMOTE_ADDR", "")
                                ip_data = requests.get(f"https://ipapi.co/{ip_cliente}/json/", timeout=2).json()
                                loc_text = f"{ip_data.get('city')}, {ip_data.get('region')}"
                            except Exception:
                                pass

                            # Tenta salvar no Supabase e trata a falha
                            try:
                                res = supabase.table("solicitacoes_acesso").insert({
                                    "created_at": obter_hora_brasilia().isoformat(),
                                    "email": email_txt,
                                    "telefone": telefone_txt,
                                    "senha_temporaria": senha_txt,
                                    "dispositivo": user_agent,
                                    "localizacao": loc_text,
                                }).execute()
                                
                                dialog.close()

                                # Dispara e-mails e alertas apenas se gravou no banco com sucesso
                                asyncio.create_task(asyncio.to_thread(enviar_notificacao_email, email_txt, telefone_txt, user_agent, loc_text))
                                asyncio.create_task(asyncio.to_thread(enviar_notificacao_telegram, email_txt, telefone_txt, user_agent, loc_text))

                                ui.notify("Solicitação enviada com sucesso ao Administrador!", color="positive")

                            except Exception as e:
                                print(f"Erro Supabase ao salvar solicitação: {e}")
                                ui.notify(f"Erro ao salvar solicitação no banco de dados. Tente novamente.", color="negative")


            ui.button("ENVIAR SOLICITAÇÃO", on_click=processar_solicitacao).classes(
                "w-full bg-blue-600 text-white font-bold mb-2"
            )
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
        ui.button("SOLICITAR ACESSO", on_click=abrir_modal_solicitacao).props("flat dense").classes(
            "w-full text-blue-500 font-medium text-xs mt-2"
        )


# ==========================================
# PÁGINA PRINCIPAL DE BOLETOS
# ==========================================
@ui.page("/")
def home_page():
    if not app.storage.user.get("user_id"):
        ui.navigate.to("/login")
        return

    drawer = menu_drawer()
    cabecalho_app(drawer)
    user_id = app.storage.user.get("user_id")

    res_perfil = supabase.table("perfis_usuarios").select("*").eq("id", user_id).execute()
    perfil_usr = res_perfil.data[0] if res_perfil.data else {}
    email_cadastrado = perfil_usr.get("email_notificacao") or perfil_usr.get("email", "")
    
    # Busca whatsapp ou telefone cadastrado no perfil
    whatsapp_cadastrado = perfil_usr.get("whatsapp") or perfil_usr.get("telefone", "")

    cats_res = supabase.table("dim_categorias").select("*").execute()
    categorias_list = {c["id"]: c["nome"] for c in (cats_res.data or [])}

    # --- Função para abrir o Modal de Alteração de Contato ---
    def abrir_modal_perfil():
        dialog = ui.dialog()
        with dialog, ui.card().classes("w-full max-w-md p-5 gap-4 bg-white rounded-2xl"):
            ui.label("📱 Alterar Dados de Contato").classes("text-xl font-bold text-slate-800 border-b pb-2 w-full")
            
            input_tel = ui.input("WhatsApp / Telefone", value=whatsapp_cadastrado).props("outlined bg-slate-50").classes("w-full")
            input_email = ui.input("E-mail de Notificação", value=email_cadastrado).props("outlined bg-slate-50").classes("w-full")

            def salvar_contato():
                novo_tel = input_tel.value.strip() if input_tel.value else ""
                novo_email = input_email.value.strip() if input_email.value else ""

                try:
                    # Atualiza as colunas 'whatsapp' e 'telefone' para manter o Painel Admin sincronizado
                    payload_perfil = {
                        "whatsapp": novo_tel,
                        "telefone": novo_tel,
                        "email_notificacao": novo_email
                    }
                    supabase.table("perfis_usuarios").update(payload_perfil).eq("id", user_id).execute()
                    
                    ui.notify("Dados de contato atualizados com sucesso!", color="positive")
                    dialog.close()
                    ui.navigate.reload()
                except Exception as err:
                    ui.notify(f"Erro ao atualizar dados: {err}", color="negative")

            with ui.row().classes("w-full justify-end gap-2 mt-2"):
                ui.button("Cancelar", on_click=dialog.close).props("flat color=grey")
                ui.button("Salvar", on_click=salvar_contato).classes("bg-purple-700 text-white font-bold px-4 py-2 rounded-lg")

        dialog.open()

    with ui.column().classes("w-full max-w-4xl mx-auto p-3 sm:p-6 gap-6 font-sans pb-32"):
        with ui.card().classes("w-full p-4 bg-purple-50 border border-purple-200 rounded-xl shadow-sm"):
            with ui.column().classes("w-full sm:flex-row justify-between items-center gap-3"):
                with ui.row().classes("items-center gap-2"):
                    ui.icon("receipt_long", size="32px", color="purple-9")
                    ui.label("Meus Boletos").classes("text-2xl sm:text-3xl font-extrabold text-slate-800")

                with ui.row().classes(
                    "items-center gap-2 bg-white p-2 px-3 rounded-lg border border-purple-200 w-full sm:w-auto justify-between"
                ):
                    with ui.row().classes("items-center gap-1"):
                        ui.icon("notifications", color="purple").classes("text-base")
                        ui.label(
                            f"Contato: {whatsapp_cadastrado or email_cadastrado or 'Não configurado'}"
                        ).classes("text-sm text-purple-900 font-semibold")
                    
                    # Botão 'Alterar' atualizado para abrir o dialog diretamente
                    ui.button("Alterar", on_click=abrir_modal_perfil).props(
                        "flat dense size=md color=purple"
                    )

        with ui.card().classes("w-full p-4 sm:p-6 border border-slate-200 bg-white shadow-md rounded-2xl gap-4"):
            ui.label("➕ Cadastrar Novo Boleto").classes("text-xl sm:text-2xl font-bold text-slate-800 border-b pb-2 w-full")

            modo = (
                ui.radio({"manual": "Entrada Manual", "arquivo": "Importar PDF ou Imagem"}, value="manual")
                .props("inline size=lg")
                .classes("text-base font-semibold text-slate-700 my-1")
            )

            with ui.column().classes("w-full gap-4"):
                input_empresa = (
                    ui.input("Empresa / Nome do Boleto", placeholder="Ex: Conta de Luz, Internet...")
                    .props("outlined size=lg bg-slate-50")
                    .classes("w-full text-lg")
                )
                input_cod_barras = (
                    ui.input(
                        "Código de Barras / Linha Digitável (Opcional)",
                        placeholder="Ex: 34191.79001 01043.510047 91020.150008 5 90000000010000",
                    )
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

            check_lembrete = ui.checkbox("🔔 Desejo receber um lembrete antes do vencimento").classes(
                "mt-2 text-base sm:text-lg text-slate-800 font-bold"
            )

            container_lembrete = ui.column().classes("w-full p-4 bg-purple-50 border-2 border-purple-200 rounded-xl gap-4")
            container_lembrete.bind_visibility_from(check_lembrete, "value")

            with container_lembrete:
                ui.label("Configuração do Lembrete").classes("text-sm font-bold text-purple-800 uppercase tracking-wide")
                with ui.grid().classes("w-full grid-cols-1 sm:grid-cols-3 gap-3"):
                    select_canal = (
                        ui.select(["Telegram"], value="Telegram", label="Canal")
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
                        ui.input("Horário", value="09:00").props("type=time outlined bg-white size=lg").classes("w-full")
                    )

            container_importacao = ui.column().classes("w-full p-4 bg-slate-100 border border-slate-300 rounded-xl gap-4")
            container_importacao.bind_visibility_from(modo, "value", backward=lambda v: v == "arquivo")

            def finalizar_importacao_e_focar():
                """Subir a tela e focar no primeiro campo."""
                ui.run_javascript("window.scrollTo({top: 0, behavior: 'smooth'});")
                input_empresa.run_method("focus")

            with container_importacao:
                ui.label("📄 Opção 1: Anexar PDF do Boleto").classes("font-bold text-slate-800 text-base")

                async def handle_upload_pdf(e):
                    try:
                        from pdf_utils import extrair_dados_pdf_boleto

                        content_obj = getattr(e, "content", None) or getattr(e, "file", None)
                        file_bytes = content_obj.read() if hasattr(content_obj, "read") else content_obj
                        if hasattr(file_bytes, "__await__"):
                            file_bytes = await file_bytes

                        pdf_stream = io.BytesIO(file_bytes)
                        dados_pdf = extrair_dados_pdf_boleto(pdf_stream)
                        
                        if len(dados_pdf) == 4:
                            empresa, valor, vencimento, codigo_barras = dados_pdf
                        else:
                            empresa, valor, vencimento = dados_pdf
                            codigo_barras = None

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
                        if codigo_barras:
                            input_cod_barras.value = codigo_barras
                            preencheu = True

                        uploader_pdf.reset()

                        if preencheu:
                            ui.notify("Dados lidos do PDF com sucesso!", color="positive", size="lg")
                        else:
                            ui.notify("Não foi possível ler os dados do PDF automaticamente.", color="warning")

                        finalizar_importacao_e_focar()
                    except Exception as err:
                        ui.notify(f"Erro ao processar PDF: {err}", color="negative")

                uploader_pdf = ui.upload(on_upload=handle_upload_pdf, auto_upload=True).props("accept=.pdf flat").classes(
                    "w-full bg-white border-2 border-dashed border-slate-300 p-2 rounded-lg"
                )

                ui.separator()

                ui.label("🖼️ Opção 2: Importar Imagem do Boleto (PNG/JPG via EasyOCR)").classes(
                    "font-bold text-slate-800 text-base"
                )

                async def handle_upload_imagem(e):
                    try:
                        content_obj = getattr(e, "content", None) or getattr(e, "file", None)
                        file_bytes = content_obj.read() if hasattr(content_obj, "read") else content_obj
                        if hasattr(file_bytes, "__await__"):
                            file_bytes = await file_bytes

                        img_stream = io.BytesIO(file_bytes)
                        ui.notify("Processando imagem com EasyOCR...", color="info")

                        empresa, valor, vencimento, codigo_barras = await run.io_bound(
                            extrair_dados_imagem_easyocr, img_stream
                        )

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
                        if codigo_barras:
                            input_cod_barras.value = codigo_barras
                            preencheu = True

                        uploader_img.reset()

                        if preencheu:
                            ui.notify("Dados lidos da imagem via EasyOCR!", color="positive", size="lg")
                        else:
                            ui.notify("Imagem processada, mas nenhum dado pôde ser identificado.", color="warning")

                        finalizar_importacao_e_focar()
                    except Exception as err:
                        ui.notify(f"Erro ao processar imagem via EasyOCR: {err}", color="negative")

                uploader_img = ui.upload(on_upload=handle_upload_imagem, auto_upload=True).props("accept=.jpg,.jpeg,.png flat").classes(
                    "w-full bg-white border-2 border-dashed border-slate-300 p-2 rounded-lg"
                )

            def limpar_formulario():
                input_empresa.value = ""
                input_cod_barras.value = ""
                input_valor.value = None
                input_vencimento.value = None
                select_categoria.value = None
                select_status.value = "PENDENTE"
                check_lembrete.value = False
                select_canal.value = "Telegram"
                select_antecedencia.value = 1
                input_horario.value = "09:00"
                uploader_pdf.reset()
                uploader_img.reset()

            def salvar_boleto():
                empresa_val = input_empresa.value.strip() if input_empresa.value else ""
                cod_barras_val = input_cod_barras.value.strip() if input_cod_barras.value else None
                valor_val = float(input_valor.value) if input_valor.value else 0.0
                vencimento_val = input_vencimento.value

                if not empresa_val or not valor_val or not vencimento_val:
                    ui.notify("Por favor, preencha Empresa, Valor e Vencimento!", color="warning", size="lg")
                    return

                query = supabase.table("boletos").select("id").eq("user_id", user_id)
                if cod_barras_val:
                    dup_res = query.eq("cod_barras", cod_barras_val).execute()
                else:
                    dup_res = query.eq("empresa", empresa_val).eq("valor", valor_val).eq("data_vencimento", vencimento_val).execute()

                if dup_res.data and len(dup_res.data) > 0:
                    ui.notify("⚠️ Atenção: Este boleto já está cadastrado no sistema!", color="warning", size="lg")
                    return

                payload = {
                    "user_id": user_id,
                    "empresa": empresa_val,
                    "cod_barras": cod_barras_val,
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

        ui.label("📋 Meus Boletos Cadastrados").classes("text-xl sm:text-2xl font-bold text-slate-800 mt-4")

        with ui.expansion("🔍 Filtros Avançados de Pesquisa", icon="filter_alt").classes(
            "w-full bg-slate-100 border border-slate-300 rounded-xl font-bold text-slate-700 text-base"
        ):
            with ui.column().classes("w-full p-3 gap-3 bg-white rounded-b-xl"):
                input_busca = (
                    ui.input("Empresa/Descrição", placeholder="Buscar por nome...").props("outlined dense bg-white").classes("w-full")
                )

                with ui.grid().classes("w-full grid-cols-1 sm:grid-cols-2 gap-3"):
                    opcoes_cat = {"TODAS": "Todas as Categorias"}
                    opcoes_cat.update(categorias_list)
                    select_filtro_cat = ui.select(opcoes_cat, value="TODAS", label="Categoria").props("outlined dense").classes("w-full")

                    select_filtro_status = (
                        ui.select(
                            {
                                "TODOS": "Todos os Status",
                                "PENDENTE": "PENDENTE",
                                "PAGO": "PAGO",
                                "ATRASADO": "ATRASADO",
                                "CANCELADO": "CANCELADO",
                            },
                            value="TODOS",
                            label="Filtrar por Status no Filtro",
                        )
                        .props("outlined dense")
                        .classes("w-full")
                    )

                ui.label("Período de Vencimento:").classes("text-sm font-semibold text-slate-600 mt-1")
                with ui.grid().classes("w-full grid-cols-2 gap-3"):
                    dt_inicio = ui.input("De").props("type=date outlined dense").classes("w-full")
                    dt_fim = ui.input("Até").props("type=date outlined dense").classes("w-full")

                ui.label("Faixa de Valor (R$):").classes("text-sm font-semibold text-slate-600 mt-1")
                with ui.grid().classes("w-full grid-cols-2 gap-3"):
                    val_min = ui.number("Valor Mínimo").props("outlined dense").classes("w-full")
                    val_max = ui.number("Valor Máximo").props("outlined dense").classes("w-full")

        for element in [input_busca, select_filtro_cat, select_filtro_status, dt_inicio, dt_fim, val_min, val_max]:
            element.on("update:model-value", lambda: renderizar_boletos_filtrados())

        with ui.tabs().classes("w-full text-purple-900 font-bold") as tabs:
            tab_pendentes = ui.tab("pendentes", label="A Vencer")
            tab_atrasados = ui.tab("atrasados", label="Atrasados")
            tab_pagos = ui.tab("pagos", label="Pagos")
            tab_todos = ui.tab("todos", label="Todos")

        container_boletos = ui.column().classes("w-full gap-3 mt-2")

        def copiar_codigo_barras(codigo):
            if codigo:
                ui.run_javascript(f'navigator.clipboard.writeText("{codigo}")')
                ui.notify("Código de barras copiado!", color="positive", icon="content_copy")
            else:
                ui.notify("Nenhum código de barras cadastrado para este boleto.", color="warning")

        def abrir_modal_edicao(b):
            dialog = ui.dialog()
            with dialog, ui.card().classes("w-full max-w-lg p-5 gap-4 bg-white rounded-2xl"):
                ui.label("✏️ Editar Boleto").classes("text-xl font-bold text-slate-800 border-b pb-2 w-full")

                edit_empresa = ui.input("Empresa / Nome", value=b.get("empresa", "")).props("outlined bg-slate-50").classes("w-full")
                edit_cod_barras = (
                    ui.input("Código de Barras (Opcional)", value=b.get("cod_barras", "") or "").props("outlined bg-slate-50").classes("w-full")
                )

                with ui.grid().classes("w-full grid-cols-1 sm:grid-cols-2 gap-3"):
                    edit_valor = (
                        ui.number("Valor (R$)", value=float(b.get("valor", 0)), format="%.2f")
                        .props("outlined bg-slate-50")
                        .classes("w-full")
                    )
                    edit_vencimento = (
                        ui.input("Vencimento", value=b.get("data_vencimento", ""))
                        .props("type=date outlined bg-slate-50")
                        .classes("w-full")
                    )

                with ui.grid().classes("w-full grid-cols-1 sm:grid-cols-2 gap-3"):
                    edit_categoria = (
                        ui.select(categorias_list, label="Categoria", value=b.get("categoria_id"))
                        .props("outlined bg-slate-50")
                        .classes("w-full")
                    )
                    edit_status = (
                        ui.select(
                            ["PENDENTE", "PAGO", "ATRASADO", "CANCELADO"],
                            label="Status",
                            value=b.get("status", "PENDENTE"),
                        )
                        .props("outlined bg-slate-50")
                        .classes("w-full")
                    )

                ui.separator()

                edit_check_lembrete = ui.checkbox("🔔 Lembrete configurado", value=bool(b.get("tem_lembrete"))).classes(
                    "text-base font-bold text-slate-800"
                )

                box_edit_lembrete = ui.column().classes("w-full p-3 bg-purple-50 border border-purple-200 rounded-xl gap-3")
                box_edit_lembrete.bind_visibility_from(edit_check_lembrete, "value")

                with box_edit_lembrete:
                    edit_select_canal = (
                        ui.select(
                            ["Telegram"],
                            label="Canal",
                            value="Telegram",
                        )
                        .props("outlined bg-white")
                        .classes("w-full")
                    )

                    antecedencia_map = {
                        0: "No dia do vencimento",
                        1: "1 dia antes",
                        2: "2 dias antes",
                        3: "3 dias antes",
                        5: "5 dias antes",
                    }
                    val_antecedencia = b.get("antecedencia_dias", 1)
                    edit_select_antecedencia = (
                        ui.select(
                            antecedencia_map,
                            label="Aviso",
                            value=val_antecedencia if val_antecedencia in antecedencia_map else 1,
                        )
                        .props("outlined bg-white")
                        .classes("w-full")
                    )

                    edit_input_horario = (
                        ui.input("Horário", value=b.get("horario_lembrete", "09:00"))
                        .props("type=time outlined bg-white")
                        .classes("w-full")
                    )

                def salvar_edicao():
                    payload_update = {
                        "empresa": edit_empresa.value.strip() if edit_empresa.value else "",
                        "cod_barras": edit_cod_barras.value.strip() if edit_cod_barras.value else None,
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

        def solicitar_confirmacao_delecao(b):
            with ui.dialog() as dialog, ui.card().classes("w-full max-w-sm p-4"):
                ui.label("⚠️ Confirmar Exclusão").classes("text-lg font-bold text-red-600 mb-2")
                ui.label(f"Tem certeza que deseja excluir o boleto '{b.get('empresa')}'?").classes("text-sm text-gray-700 mb-4")

                def executar_delecao():
                    try:
                        dialog.close()
                        supabase.table("boletos").delete().eq("id", b["id"]).execute()
                        ui.notify("Boleto excluído com sucesso!", color="info")
                        renderizar_boletos_filtrados()
                    except Exception as err:
                        ui.notify(f"Erro ao excluir: {err}", color="negative")

                with ui.row().classes("w-full justify-end gap-2"):
                    ui.button("CANCELAR", on_click=dialog.close).props("flat text-color=gray")
                    ui.button("EXCLUIR", on_click=executar_delecao).classes("bg-red-600 text-white font-bold")

            dialog.open()

        def renderizar_boletos_filtrados():
            container_boletos.clear()

            res = (
                supabase.table("boletos")
                .select("*")
                .eq("user_id", user_id)
                .order("data_vencimento", desc=False)
                .execute()
            )
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

                cnt_todos += 1
                if status_atual == "PENDENTE":
                    cnt_pendentes += 1
                elif status_atual == "ATRASADO":
                    cnt_atrasados += 1
                elif status_atual == "PAGO":
                    cnt_pagos += 1

                aba_ativa = tabs.value
                if aba_ativa == "pendentes" and status_atual != "PENDENTE":
                    continue
                elif aba_ativa == "atrasados" and status_atual != "ATRASADO":
                    continue
                elif aba_ativa == "pagos" and status_atual != "PAGO":
                    continue

                boletos_filtrados.append((b, dt_venc, status_atual))

            tab_pendentes.text = f"A Vencer ({cnt_pendentes})"
            tab_atrasados.text = f"Atrasados ({cnt_atrasados})"
            tab_pagos.text = f"Pagos ({cnt_pagos})"
            tab_todos.text = f"Todos ({cnt_todos})"

            if not boletos_filtrados:
                with container_boletos:
                    ui.label("Nenhum boleto nesta categoria.").classes(
                        "text-slate-500 italic p-4 text-center w-full bg-slate-50 rounded-xl border border-dashed"
                    )
                return

            with container_boletos:
                for b, dt_venc, status_efetivo in boletos_filtrados:
                    vence_hoje_ou_atrasado = (
                        status_efetivo != "PAGO" and dt_venc and dt_venc <= hoje
                    ) or status_efetivo == "ATRASADO"

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

                        cod_barras_txt = b.get("cod_barras")
                        if cod_barras_txt:
                            with ui.row().classes(
                                "w-full items-center justify-between bg-slate-100 p-2 rounded-lg border border-slate-200 gap-2"
                            ):
                                with ui.row().classes("items-center gap-2 overflow-hidden"):
                                    ui.icon("barcode", size="20px", color="slate-700")
                                    ui.label(cod_barras_txt).classes(
                                        "text-xs font-mono text-slate-700 truncate max-w-[200px] sm:max-w-md"
                                    )
                                ui.button(
                                    "Copiar",
                                    icon="content_copy",
                                    on_click=lambda c=cod_barras_txt: copiar_codigo_barras(c),
                                ).props("flat dense size=sm color=purple").classes("font-semibold")

                        ui.separator().classes("my-1")

                        with ui.row().classes("w-full justify-between items-center"):
                            val_fmt = f"R$ {float(b.get('valor', 0)):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                            ui.label(val_fmt).classes("text-xl font-black text-slate-800")

                            venc_str = dt_venc.strftime("%d/%m/%Y") if dt_venc else b.get("data_vencimento", "N/A")
                            ui.label(f"Vencimento: {venc_str}").classes(f"text-sm {texto_venc_class}")

                        with ui.row().classes("w-full justify-end gap-2 mt-2"):
                            ui.button(
                                "Marcar Pago" if status_efetivo != "PAGO" else "Marcar Pendente",
                                on_click=lambda current_b=b: alternar_status_pago(current_b),
                            ).props("flat dense size=sm").classes("text-purple-700 font-semibold")

                            ui.button(
                                "Editar",
                                on_click=lambda current_b=b: abrir_modal_edicao(current_b),
                            ).props("flat dense size=sm icon=edit").classes("text-slate-700")

                            ui.button(
                                "Excluir",
                                on_click=lambda current_b=b: solicitar_confirmacao_delecao(current_b),
                            ).props("flat dense size=sm icon=delete color=negative")

        tabs.on("update:model-value", lambda: renderizar_boletos_filtrados())
        renderizar_boletos_filtrados()



# ==========================================
# FUNÇÃO AUXILIAR DE VALIDAÇÃO DE TELEFONE
# ==========================================
def validar_telefone(telefone: str) -> tuple[bool, str]:
    """
    Valida se o telefone contém apenas números (incluindo DDD).
    Retorna (True, "") se válido ou (False, mensagem_erro) se inválido.
    """
    tel_limpo = telefone.strip() if telefone else ""
    
    if not tel_limpo:
        return False, "O número de telefone/WhatsApp é obrigatório."
    
    # Verifica se contém apenas números
    if not tel_limpo.isdigit():
        return False, "O telefone deve conter apenas números (sem traços, espaços ou parênteses, ex: 11999999999)."
    
    # Verifica se o número possui tamanho válido para DDD + Telefone (10 a 11 dígitos)
    if len(tel_limpo) < 10 or len(tel_limpo) > 11:
        return False, "O telefone deve conter DDD + número com 10 ou 11 dígitos (ex: 11977051343)."
        
    return True, ""


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
        ui.label("⚙️ Painel do Administrador").classes("text-2xl font-bold text-amber-900")

        # 1. SOLICITAÇÕES PENDENTES (Exibe apenas se houver solicitações)
        solicitacoes = (
            supabase.table("solicitacoes_acesso")
            .select("*")
            .execute()
            .data
            or []
        )

        if solicitacoes:
            with ui.card().classes("w-full p-4 border border-amber-200 bg-white shadow-sm"):
                ui.label("Solicitações Pendentes de Acesso").classes("text-lg font-bold mb-2")

                for sol in solicitacoes:
                    with ui.row().classes("w-full items-center justify-between p-2 border-b"):
                        with ui.column().classes("gap-0"):
                            ui.label(f"📧 {sol['email']}").classes("font-bold text-sm")
                            ui.label(f"📞 Tel: {sol.get('telefone', 'Não informado')}").classes("text-xs text-gray-600")
                            
                            loc = sol.get("localizacao")
                            if loc and loc != "Não informada":
                                ui.label(f"📍 {loc}").classes("text-xs text-gray-500")

                        with ui.row().classes("gap-2"):
                            async def aprovar(s=sol):
                                telefone_informado = s.get("telefone") or s.get("whatsapp") or ""
                                
                                # Validação do Telefone
                                e_valido, msg_erro = validar_telefone(telefone_informado)
                                if not e_valido:
                                    ui.notify(
                                        f"Erro ao aprovar {s['email']}: {msg_erro}", 
                                        color="negative", 
                                        size="lg", 
                                        icon="error"
                                    )
                                    return

                                email_usuario = s["email"].strip()

                                try:
                                    # Grava o mesmo e-mail nos dois campos e salva o WhatsApp
                                    payload_perfil = {
                                        "email": email_usuario,
                                        "email_notificacao": email_usuario,
                                        "whatsapp": telefone_informado.strip(),
                                        "senha": s["senha_temporaria"],
                                        "ativo": True,
                                        "is_admin": False
                                    }

                                    supabase.table("perfis_usuarios").insert(payload_perfil).execute()
                                    supabase.table("solicitacoes_acesso").delete().eq("id", s["id"]).execute()
                                    
                                    ui.notify(f"Acesso aprovado para {email_usuario}", color="positive")
                                    ui.navigate.reload()
                                except Exception as err:
                                    ui.notify(f"Erro no banco de dados ao aprovar: {err}", color="negative")

                            async def rejeitar(s=sol):
                                supabase.table("solicitacoes_acesso").delete().eq("id", s["id"]).execute()
                                ui.notify(f"Solicitação de {s['email']} rejeitada.", color="warning")
                                ui.navigate.reload()

                            ui.button("APROVAR", on_click=aprovar).classes("bg-blue-600 text-white text-xs font-bold")
                            ui.button("REJEITAR", on_click=rejeitar).classes("bg-red-600 text-white text-xs font-bold")

        # 2. GERENCIAMENTO DE USUÁRIOS
        usuarios = supabase.table("perfis_usuarios").select("*").order("email").execute().data or []

        with ui.card().classes("w-full p-5 border border-amber-200 bg-white shadow-sm rounded-xl"):
            ui.label("👥 Usuários Cadastrados").classes("text-lg font-bold text-slate-800 mb-3")

            for usr in usuarios:

                def alternar_status(u=usr):
                    novo_status = not u.get("ativo", True)
                    supabase.table("perfis_usuarios").update({"ativo": novo_status}).eq("id", u["id"]).execute()
                    ui.notify(f"Status de {u['email']} alterado!", color="info")
                    ui.navigate.reload()

                def confirmar_exclusao(u=usr):
                    with ui.dialog() as dialog, ui.card().classes("w-full max-w-sm p-5 rounded-xl"):
                        ui.label("⚠️ Confirmar Exclusão").classes("text-lg font-bold text-red-600 mb-2")
                        ui.label(
                            f"Tem certeza que deseja excluir o usuário '{u['email']}'? "
                            "Esta ação apagará todos os agendamentos vinculados a esta conta e não poderá ser desfeita."
                        ).classes("text-sm text-gray-600 mb-4")

                        def executar_exclusao():
                            dialog.close()
                            supabase.table("boletos").delete().eq("user_id", u["id"]).execute()
                            supabase.table("perfis_usuarios").delete().eq("id", u["id"]).execute()
                            ui.notify(f"Usuário {u['email']} excluído com sucesso!", color="negative")
                            ui.navigate.reload()

                        with ui.row().classes("w-full justify-end gap-2"):
                            ui.button("CANCELAR", on_click=dialog.close).props("flat text-color=gray").classes("rounded-lg")
                            ui.button("EXCLUIR", on_click=executar_exclusao).classes("bg-red-600 hover:bg-red-700 text-white font-bold rounded-lg px-4")

                    dialog.open()

                with ui.row().classes("w-full justify-between items-center border-b border-gray-100 py-3 gap-4"):
                    with ui.column().classes("gap-1 flex-1"):
                        ui.label(usr.get("email", "")).classes("font-bold text-base text-slate-800")
                        
                        tel = usr.get("telefone") or usr.get("celular") or usr.get("contato") or usr.get("whatsapp") or "Não informado"
                        ui.label(f"📞 Telefone: {tel}").classes("text-xs text-gray-700 font-medium")

                        loc = usr.get("localizacao") or usr.get("cidade")
                        if loc:
                            ui.label(f"📍 Localização: {loc}").classes("text-xs text-gray-600")

                        is_ativo = usr.get("ativo", True)
                        status_label = "Status: Ativo" if is_ativo else "Status: Inativo"
                        badge_classes = "bg-emerald-100 text-emerald-800" if is_ativo else "bg-red-100 text-red-800"
                        ui.label(status_label).classes(f"text-xs font-semibold px-2.5 py-0.5 rounded-full w-fit mt-1 {badge_classes}")

                    if usr.get("email") != ADMIN_EMAIL:
                        with ui.row().classes("gap-2 items-center"):
                            is_ativo = usr.get("ativo", True)
                            
                            if is_ativo:
                                btn_text = "INATIVAR"
                                btn_class = "bg-amber-500 hover:bg-amber-600 text-white"
                                btn_icon = "block"
                            else:
                                btn_text = "ATIVAR"
                                btn_class = "bg-emerald-600 hover:bg-emerald-700 text-white"
                                btn_icon = "check_circle"

                            ui.button(btn_text, icon=btn_icon, on_click=alternar_status).classes(
                                f"font-bold text-xs px-3 py-1.5 rounded-lg shadow-sm transition-all {btn_class}"
                            )

                            ui.button("EXCLUIR", icon="delete", on_click=confirmar_exclusao).classes(
                                "bg-red-600 hover:bg-red-700 text-white font-bold text-xs px-3 py-1.5 rounded-lg shadow-sm transition-all"
                            )

        # 3. TESTE DE ALERTAS VIA BOT
        with ui.card().classes("w-full p-4 border border-blue-200 bg-white shadow-sm"):
            ui.label("🧪 Teste de Envio de Alertas").classes("text-lg font-bold mb-1 text-blue-900")
            ui.label("Selecione um usuário para enviar um alerta de teste em tempo real via Bot.").classes("text-xs text-gray-600 mb-3")

            opcoes_usuarios = {u["email"]: u["email"] for u in usuarios if u.get("email")}

            with ui.row().classes("w-full items-center gap-3"):
                select_usuario = ui.select(
                    options=opcoes_usuarios,
                    label="Selecione o Usuário",
                    with_input=True
                ).classes("flex-1 max-w-xs")

                async def disparar_teste_alerta():
                    email_alvo = select_usuario.value
                    if not email_alvo:
                        ui.notify("Selecione um usuário na lista!", color="warning")
                        return

                    usr_alvo = next((u for u in usuarios if u.get("email") == email_alvo), {})
                    telefone_alvo = usr_alvo.get("telefone") or usr_alvo.get("whatsapp") or "Não informado"

                    try:
                        await asyncio.to_thread(
                            enviar_notificacao_telegram,
                            email_alvo,
                            telefone_alvo,
                            "Teste Manual Admin",
                            "Painel de Testes"
                        )
                        ui.notify(f"Alerta de teste enviado para {email_alvo}!", color="positive")
                    except Exception as err:
                        ui.notify(f"Erro ao disparar alerta: {err}", color="negative")

                ui.button("ENVIAR ALERTA DE TESTE", on_click=disparar_teste_alerta).classes("bg-blue-600 text-white font-bold h-10 text-xs")

        # 4. GERENCIAMENTO DE CATEGORIAS
        with ui.card().classes("w-full p-4 border border-amber-200 bg-white shadow-sm"):
            ui.label("🏷️ Categorias de Contas").classes("text-lg font-bold mb-2")

            with ui.row().classes("w-full items-center gap-2 mb-4"):
                nova_cat = ui.input(placeholder="Nova Categoria").props("outlined bg-white dense").classes("flex-1")

                def add_categoria():
                    if nova_cat.value and nova_cat.value.strip():
                        supabase.table("dim_categorias").insert({"nome": nova_cat.value.strip()}).execute()
                        ui.notify("Categoria criada com sucesso!", color="positive")
                        ui.navigate.reload()

                ui.button("ADICIONAR CATEGORIA", on_click=add_categoria).classes("bg-blue-600 text-white font-bold")

            ui.separator().classes("my-2")

            categorias = supabase.table("dim_categorias").select("*").order("nome").execute().data or []

            if not categorias:
                ui.label("Nenhuma categoria cadastrada.").classes("text-sm text-gray-500 italic")

            for cat in categorias:

                def editar_categoria(c=cat):
                    with ui.dialog() as dialog, ui.card().classes("w-full max-w-sm p-4"):
                        ui.label("✏️ Editar Categoria").classes("text-lg font-bold text-slate-800 mb-2")
                        campo_nome = ui.input("Nome da Categoria", value=c["nome"]).props("outlined dense").classes("w-full mb-4")

                        def salvar_edicao():
                            if campo_nome.value and campo_nome.value.strip():
                                supabase.table("dim_categorias").update({"nome": campo_nome.value.strip()}).eq("id", c["id"]).execute()
                                dialog.close()
                                ui.notify("Categoria atualizada!", color="positive")
                                ui.navigate.reload()

                        with ui.row().classes("w-full justify-end gap-2"):
                            ui.button("CANCELAR", on_click=dialog.close).props("flat text-color=gray")
                            ui.button("SALVAR", on_click=salvar_edicao).classes("bg-green-600 text-white font-bold")

                    dialog.open()

                def confirmar_exclusao_categoria(c=cat):
                    with ui.dialog() as dialog, ui.card().classes("w-full max-w-sm p-4"):
                        ui.label("⚠️ Confirmar Exclusão").classes("text-lg font-bold text-red-600 mb-2")
                        ui.label(f"Tem certeza que deseja excluir a categoria '{c['nome']}'?").classes("text-sm text-gray-700 mb-4")

                        def executar_exclusao():
                            dialog.close()
                            supabase.table("dim_categorias").delete().eq("id", c["id"]).execute()
                            ui.notify(f"Categoria '{c['nome']}' excluída!", color="negative")
                            ui.navigate.reload()

                        with ui.row().classes("w-full justify-end gap-2"):
                            ui.button("CANCELAR", on_click=dialog.close).props("flat text-color=gray")
                            ui.button("EXCLUIR", on_click=executar_exclusao).classes("bg-red-600 text-white font-bold")

                    dialog.open()

                with ui.row().classes("w-full justify-between items-center border-b py-2"):
                    ui.label(cat["nome"]).classes("text-sm font-medium text-gray-800")

                    with ui.row().classes("gap-2"):
                        ui.button("Editar", on_click=editar_categoria).props("color=amber dense size=sm")
                        ui.button("Excluir", on_click=confirmar_exclusao_categoria).props("color=negative dense size=sm")


# Rota leve para Keep-Alive / Health Check
@app.get("/ping")
def ping():
    return {"status": "ok"}


# Inicializador do Scheduler e Servidor NiceGUI
try:
    subprocess.Popen(["python", "scheduler.py"])
    print("Scheduler iniciado com sucesso em segundo plano!")
except Exception as e:
    print(f"Erro ao iniciar o scheduler: {e}")

port = int(os.environ.get("PORT", 8080))
ui.run(
    host="0.0.0.0",
    port=port,
    storage_secret="sua_chave_secreta_aqui",
)