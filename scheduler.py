import os
import time
import json
import urllib.parse
import urllib.request
from datetime import datetime, date, timedelta
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from supabase import create_client, Client

# 1. Carrega as variáveis de ambiente
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("SUPABASE_URL ou SUPABASE_KEY não foram encontradas no arquivo .env!")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# 2. Envio via Telegram
def enviar_telegram(mensagem):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("  ⚠️ [TELEGRAM] Token ou Chat ID não configurados no arquivo .env!")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": mensagem,
        "parse_mode": "Markdown"
    }

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as response:
            if response.status == 200:
                print("  ✅ [TELEGRAM] Notificação enviada para o celular com sucesso!")
                return True
    except Exception as e:
        print(f"  ❌ [TELEGRAM ERRO] Falha ao enviar mensagem: {e}")
        return False


# 3. Disparo da notificação
def enviar_notificacao(boleto, data_vencimento, hoje):
    nome_boleto = boleto.get("empresa") or boleto.get("titulo") or "Boleto sem nome"
    valor = boleto.get("valor", 0.0)
    
    # Formata a data para dd/mm/yyyy
    vencimento_str = data_vencimento.strftime("%d/%m/%Y")
    
    # Calcula a diferença de dias até o vencimento
    dias_restantes = (data_vencimento - hoje).days

    if dias_restantes == 0:
        texto_vencimento = f"vence *hoje* data {vencimento_str}"
    elif dias_restantes == 1:
        texto_vencimento = f"vence *amanhã* data {vencimento_str}"
    elif dias_restantes > 1:
        texto_vencimento = f"vence *daqui {dias_restantes} dias* data {vencimento_str}"
    else:
        texto_vencimento = f"venceu no dia {vencimento_str}"

    mensagem = f"⏰ *Lembrete de Vencimento*\nO boleto *{nome_boleto}* no valor de R$ {valor:.2f} {texto_vencimento}."

    print(f"\n[ALERTA DISPARADO!] Boleto: {nome_boleto}")
    print(f"Mensagem: {mensagem}")

    # Tenta enviar pelo Telegram
    sucesso = enviar_telegram(mensagem)
    print("-" * 50)
    return sucesso


# 4. Lógica de checagem a cada 5 minutos
def verificar_boletos_e_notificar():
    agora = datetime.now()
    hoje = agora.date()
    hora_atual_str = agora.strftime("%H:%M")
    
    print(f"[{agora.strftime('%Y-%m-%d %H:%M:%S')}] Verificando alertas pendentes...")

    try:
        # Busca apenas boletos com lembrete ativo
        resposta = supabase.table("boletos") \
            .select("*") \
            .eq("tem_lembrete", True) \
            .execute()

        boletos = resposta.data
        disparados = 0

        for boleto in boletos:
            # Ignora boletos já pagos ou que já foram notificados nesta data
            status = str(boleto.get("status", "")).lower()
            if status == "pago" or boleto.get("notificado") is True:
                continue

            try:
                # Converte data de vencimento
                str_venc = str(boleto["data_vencimento"])
                if "/" in str_venc:
                    data_vencimento = datetime.strptime(str_venc, "%d/%m/%Y").date()
                else:
                    data_vencimento = datetime.strptime(str_venc, "%Y-%m-%d").date()

                antecedencia = int(boleto.get("antecedencia_dias", 0))
                data_disparo = data_vencimento - timedelta(days=antecedencia)

                # Pega a hora personalizada definida pelo usuário (padrão: "08:00")
                hora_alerta = str(boleto.get("hora_lembrete", "08:00"))

                # Valida se a data é hoje e se o horário já chegou ou passou
                if data_disparo == hoje and hora_atual_str >= hora_alerta:
                    sucesso = enviar_notificacao(boleto, data_vencimento, hoje)
                    
                    # Marca no banco que já foi notificado para não repetir
                    if sucesso:
                        supabase.table("boletos") \
                            .update({"notificado": True}) \
                            .eq("id", boleto["id"]) \
                            .execute()
                        disparados += 1

            except Exception as e:
                print(f"Erro ao processar boleto ID {boleto.get('id')}: {e}")

        if disparados > 0:
            print(f"Total de alertas disparados nesta rodada: {disparados}")

    except Exception as e:
        print(f"Erro ao consultar o Supabase: {e}")


# 5. Agendador configurado para 5 minutos
def iniciar_agendador():
    scheduler = BackgroundScheduler()
    
    # Executa a verificação a cada 5 minutos
    scheduler.add_job(verificar_boletos_e_notificar, 'interval', minutes=5)
    
    # Executa imediatamente 1 vez ao iniciar o script
    scheduler.add_job(verificar_boletos_e_notificar, 'date', run_date=datetime.now())

    scheduler.start()
    print("Agendador iniciado! Verificando a cada 5 minutos.")


if __name__ == "__main__":
    iniciar_agendador()
    
    try:
        while True:
            time.sleep(2)
    except (KeyboardInterrupt, SystemExit):
        print("\nAgendador encerrado.")