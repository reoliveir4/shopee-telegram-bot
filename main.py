"""
Bot de automação: Shopee Affiliate API -> IA (texto) -> Telegram

Fluxo:
1. Consulta a API oficial de afiliados da Shopee (GraphQL) e busca produtos
   com comissão, já com o link de afiliado (offerLink) embutido.
2. Gera um texto de divulgação usando a API da Anthropic (Claude).
3. Envia a mensagem (foto + texto + link) para o canal do Telegram.

Todas as chaves sensíveis são lidas de variáveis de ambiente (GitHub Secrets).
"""

import os
import time
import hashlib
import json
import requests

# ---------------------------------------------------------------------------
# Configurações (vêm de variáveis de ambiente / GitHub Secrets)
# ---------------------------------------------------------------------------
SHOPEE_APP_ID = os.environ["SHOPEE_APP_ID"]
SHOPEE_APP_SECRET = os.environ["SHOPEE_APP_SECRET"]

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]  # ex: @seucanal ou -100xxxxxxxxxx

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

SHOPEE_GRAPHQL_URL = "https://open-api.affiliate.shopee.com.br/graphql"

# Palavra-chave / categoria de produtos a buscar. Pode virar uma lista para
# variar o nicho a cada execução.
KEYWORD = os.environ.get("SHOPEE_KEYWORD", "promocao")

# Quantos produtos buscar por execução (o script posta apenas 1 por rodada,
# mas busca alguns para poder escolher o de maior comissão)
LIMIT = 5


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
# 2) Geração automática do texto com a API da Anthropic (Claude)
# ---------------------------------------------------------------------------
def gerar_texto_divulgacao(produto: dict) -> str:
    prompt = f"""
Crie um texto curto (máximo 4 linhas) e persuasivo para divulgar este produto
em um canal de ofertas no Telegram. Use emojis, tom animado, e destaque a
comissão/desconto se fizer sentido. Não invente informações que não foram
fornecidas. Não inclua o link (ele será adicionado separadamente).

Produto: {produto.get('productName')}
Loja: {produto.get('shopName')}
Preço: R$ {produto.get('price')}
"""

    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-sonnet-4-6",
            "max_tokens": 300,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["content"][0]["text"].strip()


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
    print(f"Buscando produtos para a palavra-chave: '{KEYWORD}'...")
    produtos = buscar_produtos_shopee(KEYWORD, LIMIT)

    if not produtos:
        print("Nenhum produto encontrado. Encerrando.")
        return

    # Escolhe o produto com maior comissão dentre os retornados
    produto = max(produtos, key=lambda p: float(p.get("commissionRate", 0)))
    print(f"Produto escolhido: {produto['productName']}")

    print("Gerando texto de divulgação com IA...")
    texto = gerar_texto_divulgacao(produto)
    print(f"Texto gerado:\n{texto}")

    print("Enviando para o Telegram...")
    resultado = enviar_para_telegram(produto, texto)
    print("Enviado com sucesso!", resultado.get("ok"))


if __name__ == "__main__":
    main()
