"""
Bot de automação: Shopee Affiliate API -> IA (texto) -> Telegram

Fluxo:
1. Consulta a API oficial de afiliados da Shopee (GraphQL) e busca produtos
   mais vendidos, já com o link de afiliado (offerLink) embutido.
2. Gera um texto de divulgação usando a API gratuita do Google Gemini.
3. Envia a mensagem (foto + texto + link) para o canal do Telegram.

Todas as chaves sensíveis são lidas de variáveis de ambiente (GitHub Secrets).
"""

import os
import time
import hashlib
import json
import random
import requests
from datetime import datetime

# ---------------------------------------------------------------------------
# Configurações (vêm de variáveis de ambiente / GitHub Secrets)
# ---------------------------------------------------------------------------
SHOPEE_APP_ID = os.environ["SHOPEE_APP_ID"]
SHOPEE_APP_SECRET = os.environ["SHOPEE_APP_SECRET"]

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]  # ex: @seucanal ou -100xxxxxxxxxx

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

SHOPEE_GRAPHQL_URL = "https://open-api.affiliate.shopee.com.br/graphql"

# Palavra-chave / categoria de produtos a buscar por padrão, quando nenhuma
# campanha de data específica estiver ativa.
KEYWORD_PADRAO = os.environ.get("SHOPEE_KEYWORD", "promocao")

# ---------------------------------------------------------------------------
# Campanhas por data: cada item define um período (início e fim, formato
# "MM-DD") e a palavra-chave a usar nesse período. O script verifica a data
# de hoje e, se estiver dentro de algum período, usa a keyword da campanha
# em vez da padrão. Datas que viram o ano (ex: 25-12 a 05-01) funcionam.
# ---------------------------------------------------------------------------
CAMPANHAS_POR_DATA = [
    {"nome": "Dia das Mães", "inicio": "04-20", "fim": "05-12", "keyword": "presente dia das maes"},
    {"nome": "Dia dos Namorados", "inicio": "05-25", "fim": "06-12", "keyword": "presente dia dos namorados"},
    {"nome": "Black Friday", "inicio": "11-01", "fim": "11-29", "keyword": "black friday"},
    {"nome": "Natal", "inicio": "12-01", "fim": "12-24", "keyword": "presente de natal"},
    # Adicione novas campanhas seguindo o mesmo formato acima.
]


def escolher_keyword_do_dia() -> str:
    hoje = datetime.now().strftime("%m-%d")

    for campanha in CAMPANHAS_POR_DATA:
        inicio, fim = campanha["inicio"], campanha["fim"]
        if inicio <= fim:
            dentro_do_periodo = inicio <= hoje <= fim
        else:
            # Período que cruza a virada do ano (ex: dez -> jan)
            dentro_do_periodo = hoje >= inicio or hoje <= fim

        if dentro_do_periodo:
            print(f"Campanha ativa hoje: {campanha['nome']} (keyword: {campanha['keyword']})")
            return campanha["keyword"]

    return KEYWORD_PADRAO

# Quantos produtos buscar por execução (o script escolhe 1 aleatoriamente
# entre eles, para dar variedade mesmo com muitas postagens por dia)
LIMIT = 20


# ---------------------------------------------------------------------------
# 1) Autenticação e chamada à API da Shopee
# ---------------------------------------------------------------------------
def gerar_assinatura(app_id: str, timestamp: int, payload: str, secret: str) -> str:
    """
    A Shopee exige: Signature = SHA256(AppId + Timestamp + Payload + Secret)
    concatenados sem espaços, nessa ordem exata.
    """
    base_string = f"{app_id}{timestamp}{payload}{secret}"
    return hashlib.sha256(base_string.encode("utf-8")).hexdigest()


def buscar_produtos_shopee(keyword: str, limit: int = 5) -> list:
    query = """
    query buscarProdutos($keyword: String, $limit: Int) {
      productOfferV2(keyword: $keyword, limit: $limit, sortType: 2) {
        nodes {
          itemId
          productName
          commissionRate
          price
          priceMax
          priceMin
          imageUrl
          offerLink
          shopName
        }
      }
    }
    """
    variables = {"keyword": keyword, "limit": limit}
    payload = json.dumps({"query": query, "variables": variables})

    timestamp = int(time.time())
    assinatura = gerar_assinatura(SHOPEE_APP_ID, timestamp, payload, SHOPEE_APP_SECRET)

    headers = {
        "Content-Type": "application/json",
        "Authorization": (
            f"SHA256 Credential={SHOPEE_APP_ID}, "
            f"Timestamp={timestamp}, Signature={assinatura}"
        ),
    }

    resp = requests.post(SHOPEE_GRAPHQL_URL, headers=headers, data=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if "errors" in data:
        raise RuntimeError(f"Erro na API da Shopee: {data['errors']}")

    return data["data"]["productOfferV2"]["nodes"]


# ---------------------------------------------------------------------------
# 2) Geração automática do texto com a API gratuita do Google Gemini
# ---------------------------------------------------------------------------
def gerar_texto_divulgacao(produto: dict) -> str:
    prompt = f"""
Crie um texto curto (máximo 4 linhas) e persuasivo para divulgar este produto
em um canal de ofertas no Telegram. Use emojis, tom animado, e destaque o
preço de forma natural dentro do texto, como um gancho que chama atenção.
Não invente informações que não foram fornecidas. Não inclua o link (ele
será adicionado separadamente). Responda apenas com o texto final, sem
explicações extras.

Produto: {produto.get('productName')}
Loja: {produto.get('shopName')}
Preço: R$ {produto.get('price')}
"""

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    )

    resp = requests.post(
        url,
        headers={"content-type": "application/json"},
        json={"contents": [{"parts": [{"text": prompt}]}]},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"].strip()


# ---------------------------------------------------------------------------
# 3) Envio para o Telegram
# ---------------------------------------------------------------------------
def enviar_para_telegram(produto: dict, texto: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"

    legenda = f"{texto}\n\n🔗 {produto['offerLink']}"

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "photo": produto["imageUrl"],
        "caption": legenda,
        "parse_mode": "HTML",
    }

    resp = requests.post(url, data=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Execução principal
# ---------------------------------------------------------------------------
def main():
    keyword = escolher_keyword_do_dia()
    print(f"Buscando produtos para a palavra-chave: '{keyword}'...")
    produtos = buscar_produtos_shopee(keyword, LIMIT)

    if not produtos:
        print("Nenhum produto encontrado. Encerrando.")
        return

    # sortType: 2 = já retorna os produtos ordenados por MAIS VENDIDOS.
    # Escolhemos um aleatoriamente entre eles (não filtramos por comissão,
    # já que qualquer venda feita pelo seu link gera comissão para você).
    produto = random.choice(produtos)
    print(f"Produto escolhido: {produto['productName']}")

    print("Gerando título com IA...")
    titulo = gerar_texto_divulgacao(produto)
    print(f"Título gerado:\n{titulo}")

    print("Enviando para o Telegram...")
    resultado = enviar_para_telegram(produto, titulo)
    print("Enviado com sucesso!", resultado.get("ok"))


if __name__ == "__main__":
    main()
