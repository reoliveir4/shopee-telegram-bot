"""
Bot de automação: Shopee Affiliate API -> IA (texto) -> Telegram

Fluxo:
1. A cada execução (disparada a cada 10 minutos pelo GitHub Actions), o
   script verifica se algum horário-alvo do dia já venceu e ainda não foi
   postado. Se sim, busca um produto mais vendido na Shopee, gera um texto
   com IA (Google Gemini) e posta no canal do Telegram no formato:
     [gancho]
     [preço com desconto]
     [descrição do produto]
     [link]
2. Se nenhum horário estiver pendente, o script encerra rapidamente sem
   gastar chamadas de API.

Todas as chaves sensíveis são lidas de variáveis de ambiente (GitHub Secrets).
"""

import os
import time
import hashlib
import json
import random
import re
import requests
from datetime import datetime, timedelta, date

# ---------------------------------------------------------------------------
# Configurações (vêm de variáveis de ambiente / GitHub Secrets)
# ---------------------------------------------------------------------------
SHOPEE_APP_ID = os.environ["SHOPEE_APP_ID"].strip()
SHOPEE_APP_SECRET = os.environ["SHOPEE_APP_SECRET"].strip()

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"].strip()

SHOPEE_GRAPHQL_URL = "https://open-api.affiliate.shopee.com.br/graphql"

# Se SHOPEE_KEYWORD estiver definida no workflow com um valor específico,
# ela trava a busca nessa única palavra-chave (fora dos dias de campanha).
# Deixe em branco ("") para sortear entre TAGS_POPULARES a cada execução.
KEYWORD_FIXA = os.environ.get("SHOPEE_KEYWORD", "").strip()

# ---------------------------------------------------------------------------
# CONFIGURAÇÃO DE HORÁRIOS — ajuste aqui a quantidade de posts por dia e a
# janela de horário. O robô distribui os posts igualmente dentro da janela.
# ---------------------------------------------------------------------------
POSTS_POR_DIA = 35
HORA_INICIO = "05:00"   # horário de Brasília
HORA_FIM = "23:00"      # horário de Brasília

ARQUIVO_ESTADO = "estado.json"


def gerar_horarios_alvo() -> list:
    """Gera POSTS_POR_DIA horários (HH:MM) igualmente espaçados entre
    HORA_INICIO e HORA_FIM."""
    h_ini, m_ini = map(int, HORA_INICIO.split(":"))
    h_fim, m_fim = map(int, HORA_FIM.split(":"))
    minutos_inicio = h_ini * 60 + m_ini
    minutos_fim = h_fim * 60 + m_fim
    intervalo = (minutos_fim - minutos_inicio) / POSTS_POR_DIA

    horarios = []
    for i in range(POSTS_POR_DIA):
        minutos = int(minutos_inicio + i * intervalo)
        horarios.append(f"{minutos // 60:02d}:{minutos % 60:02d}")
    return horarios


# ---------------------------------------------------------------------------
# CÁLCULO DE DATAS COMEMORATIVAS (funciona para qualquer ano, sem precisar
# atualizar manualmente — inclusive datas "móveis" como Dia das Mães,
# Dia dos Pais, Black Friday e Páscoa).
# ---------------------------------------------------------------------------
def _enesimo_dia_semana(ano: int, mes: int, dia_semana: int, n: int) -> date:
    """dia_semana: Segunda=0 ... Domingo=6. Retorna a n-ésima ocorrência
    desse dia da semana no mês (ex: 2º domingo de maio)."""
    d = date(ano, mes, 1)
    delta = (dia_semana - d.weekday()) % 7
    dia = 1 + delta + 7 * (n - 1)
    return date(ano, mes, dia)


def _ultimo_dia_semana(ano: int, mes: int, dia_semana: int) -> date:
    """Retorna a última ocorrência de um dia da semana no mês (ex: última
    sexta-feira de novembro, para Black Friday)."""
    if mes == 12:
        proximo_mes = date(ano + 1, 1, 1)
    else:
        proximo_mes = date(ano, mes + 1, 1)
    ultimo_dia_do_mes = proximo_mes - timedelta(days=1)
    delta = (ultimo_dia_do_mes.weekday() - dia_semana) % 7
    return ultimo_dia_do_mes - timedelta(days=delta)


def _pascoa(ano: int) -> date:
    """Algoritmo de Meeus/Jones/Butcher para calcular a data da Páscoa."""
    a = ano % 19
    b = ano // 100
    c = ano % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes = (h + l - 7 * m + 114) // 31
    dia = ((h + l - 7 * m + 114) % 31) + 1
    return date(ano, mes, dia)


def _data_do_evento(campanha: dict, ano: int) -> date:
    regra = campanha["regra"]
    tipo = regra[0]
    if tipo == "fixa":
        _, mes, dia = regra
        return date(ano, mes, dia)
    if tipo == "enesimo_dia_semana":
        _, mes, n, dia_semana = regra
        return _enesimo_dia_semana(ano, mes, dia_semana, n)
    if tipo == "ultimo_dia_semana":
        _, mes, dia_semana = regra
        return _ultimo_dia_semana(ano, mes, dia_semana)
    if tipo == "pascoa":
        return _pascoa(ano)
    raise ValueError(f"Tipo de regra desconhecido: {tipo}")


# ---------------------------------------------------------------------------
# Datas comemorativas pré-definidas (Brasil). "dias_antecedencia" é quantos
# dias antes do evento o robô já começa a divulgar produtos relacionados.
# dia_semana: Segunda=0, Terça=1, Quarta=2, Quinta=3, Sexta=4, Sábado=5,
# Domingo=6.
# ---------------------------------------------------------------------------
CAMPANHAS_POR_DATA = [
    {"nome": "Dia do Consumidor", "regra": ("fixa", 3, 15), "dias_antecedencia": 10,
     "keyword": "promocao dia do consumidor"},
    {"nome": "Páscoa", "regra": ("pascoa",), "dias_antecedencia": 10,
     "keyword": "presente pascoa chocolate"},
    {"nome": "Dia das Mães", "regra": ("enesimo_dia_semana", 5, 2, 6), "dias_antecedencia": 10,
     "keyword": "presente dia das maes"},
    {"nome": "Dia dos Namorados", "regra": ("fixa", 6, 12), "dias_antecedencia": 10,
     "keyword": "presente dia dos namorados"},
    {"nome": "Dia dos Pais", "regra": ("enesimo_dia_semana", 8, 2, 6), "dias_antecedencia": 10,
     "keyword": "presente dia dos pais"},
    {"nome": "Dia das Crianças", "regra": ("fixa", 10, 12), "dias_antecedencia": 10,
     "keyword": "presente dia das criancas brinquedo"},
    {"nome": "Black Friday", "regra": ("ultimo_dia_semana", 11, 4), "dias_antecedencia": 20,
     "keyword": "black friday"},
    {"nome": "Natal", "regra": ("fixa", 12, 25), "dias_antecedencia": 15,
     "keyword": "presente de natal"},
    # Adicione novas campanhas seguindo o mesmo formato acima.
]


def escolher_keyword_do_dia() -> str:
    hoje = datetime.now().date()

    for campanha in CAMPANHAS_POR_DATA:
        # Considera tanto a ocorrência deste ano quanto a do ano seguinte,
        # para cobrir corretamente campanhas próximas da virada do ano.
        for ano in (hoje.year, hoje.year + 1):
            evento = _data_do_evento(campanha, ano)
            inicio = evento - timedelta(days=campanha["dias_antecedencia"])
            if inicio <= hoje <= evento:
                print(f"Campanha ativa hoje: {campanha['nome']} (evento em {evento}, keyword: {campanha['keyword']})")
                return campanha["keyword"]

    if KEYWORD_FIXA:
        return KEYWORD_FIXA

    keyword_sorteada = random.choice(TAGS_POPULARES)
    print(f"Nenhuma campanha ativa. Categoria sorteada: '{keyword_sorteada}'")
    return keyword_sorteada


# ---------------------------------------------------------------------------
# Categorias populares: quando nenhuma campanha de data estiver ativa, o
# robô sorteia uma dessas categorias a cada execução. Edite livremente.
# ---------------------------------------------------------------------------
TAGS_POPULARES = [
    "eletronicos",
    "beleza e cuidado pessoal",
    "casa e decoracao",
    "moda feminina",
    "moda masculina",
    "cozinha utensilios",
    "fitness e academia",
    "smartphone acessorios",
    "bolsas e mochilas",
    "calcados",
    "brinquedos infantil",
    "pet shop",
    "informatica gamer",
    "relogios",
    "organizacao domestica",
    "skincare",
    "fones de ouvido",
    "ferramentas",
    "papelaria",
    "jardim e piscina",
]

# Quantos produtos buscar por execução
LIMIT = 50


# ---------------------------------------------------------------------------
# Controle de estado: horários já disparados hoje + produtos já postados hoje
# ---------------------------------------------------------------------------
def carregar_estado() -> dict:
    hoje = datetime.now().strftime("%Y-%m-%d")
    padrao = {"data": hoje, "ids_enviados": [], "horarios_postados": []}

    if not os.path.exists(ARQUIVO_ESTADO):
        return padrao

    try:
        with open(ARQUIVO_ESTADO, "r", encoding="utf-8") as f:
            dados = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return padrao

    if dados.get("data") != hoje:
        return padrao

    dados.setdefault("ids_enviados", [])
    dados.setdefault("horarios_postados", [])
    return dados


def salvar_estado(estado: dict):
    with open(ARQUIVO_ESTADO, "w", encoding="utf-8") as f:
        json.dump(estado, f)


# ---------------------------------------------------------------------------
# 1) Autenticação e chamada à API da Shopee
# ---------------------------------------------------------------------------
def gerar_assinatura(app_id: str, timestamp: int, payload: str, secret: str) -> str:
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
          priceDiscountRate
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
# 2) Geração automática do conteúdo (gancho + descrição) com Google Gemini
# ---------------------------------------------------------------------------
def gerar_conteudo_ia(produto: dict) -> dict:
    prompt = f"""
Crie o conteúdo de um anúncio para um canal de ofertas no Telegram, no
formato JSON, com EXATAMENTE estas duas chaves:

- "gancho": uma frase curta (1 linha), com um emoji relevante no início,
  que desperte curiosidade sobre o problema/necessidade que o produto
  resolve. NÃO mencione preço nem o nome completo do produto aqui.
- "descricao": uma frase curta (1 linha), com um emoji relevante no
  início, seguida do nome do produto, um traço "–" e uma explicação
  rápida do principal benefício.

Não invente informações que não foram fornecidas. Responda APENAS com o
JSON puro, sem texto antes ou depois, sem marcação markdown.

Produto: {produto.get('productName')}
Loja: {produto.get('shopName')}
"""

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-flash-latest:generateContent?key={GEMINI_API_KEY}"
    )

    resp = requests.post(
        url,
        headers={"content-type": "application/json"},
        json={"contents": [{"parts": [{"text": prompt}]}]},
        timeout=30,
    )
    resp.raise_for_status()
    texto_bruto = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()

    # Remove eventuais marcações de bloco de código (```json ... ```)
    texto_limpo = re.sub(r"^```(json)?|```$", "", texto_bruto.strip(), flags=re.MULTILINE).strip()

    try:
        conteudo = json.loads(texto_limpo)
        gancho = conteudo.get("gancho", "").strip()
        descricao = conteudo.get("descricao", "").strip()
        if gancho and descricao:
            return {"gancho": gancho, "descricao": descricao}
    except json.JSONDecodeError:
        pass

    # Fallback: se a IA não devolveu um JSON válido, usa o texto bruto como
    # gancho e monta uma descrição simples com o nome do produto.
    return {
        "gancho": texto_bruto.split("\n")[0][:120],
        "descricao": f"🛒 {produto.get('productName')}",
    }


def formatar_preco(valor: float) -> str:
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def montar_linha_preco(produto: dict) -> str:
    preco = float(produto.get("price", 0))
    desconto = produto.get("priceDiscountRate")
    desconto = float(desconto) if desconto else 0.0

    if desconto > 0:
        preco_original = preco / (1 - desconto / 100)
        return (
            f"🔥 De {formatar_preco(preco_original)} por apenas "
            f"{formatar_preco(preco)} [🎟️ {round(desconto)}% OFF]"
        )
    return f"💰 {formatar_preco(preco)}"


# ---------------------------------------------------------------------------
# 3) Envio para o Telegram
# ---------------------------------------------------------------------------
def enviar_para_telegram(produto: dict, conteudo: dict):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"

    linha_preco = montar_linha_preco(produto)
    legenda = (
        f"{conteudo['gancho']}\n"
        f"{linha_preco}\n"
        f"{conteudo['descricao']}\n\n"
        f"🔗 {produto['offerLink']}"
    )

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
    agora_brt = datetime.utcnow() - timedelta(hours=3)  # Brasília = UTC-3
    agora_hm = agora_brt.strftime("%H:%M")

    estado = carregar_estado()
    horarios_alvo = gerar_horarios_alvo()

    horarios_pendentes = [
        h for h in horarios_alvo
        if h <= agora_hm and h not in estado["horarios_postados"]
    ]

    if not horarios_pendentes:
        print(f"Nenhum horário pendente agora ({agora_hm} BRT). Encerrando sem postar.")
        return

    horario_disparado = horarios_pendentes[0]
    print(f"Horário-alvo '{horario_disparado}' está pendente (agora são {agora_hm} BRT). Postando...")

    keyword = escolher_keyword_do_dia()
    print(f"Buscando produtos para a palavra-chave: '{keyword}'...")
    produtos = buscar_produtos_shopee(keyword, LIMIT)

    if not produtos:
        print("Nenhum produto encontrado. Marcando horário como tentado, sem postar.")
        estado["horarios_postados"].append(horario_disparado)
        salvar_estado(estado)
        return

    ids_enviados = set(estado["ids_enviados"])
    produtos_novos = [p for p in produtos if str(p["itemId"]) not in ids_enviados]

    if not produtos_novos:
        print("Todos os produtos encontrados já foram postados hoje. Permitindo repetição.")
        produtos_novos = produtos

    produto = random.choice(produtos_novos)
    print(f"Produto escolhido: {produto['productName']}")

    print("Gerando conteúdo com IA...")
    conteudo = gerar_conteudo_ia(produto)
    print(f"Gancho: {conteudo['gancho']}\nDescrição: {conteudo['descricao']}")

    print("Enviando para o Telegram...")
    resultado = enviar_para_telegram(produto, conteudo)
    print("Enviado com sucesso!", resultado.get("ok"))

    estado["ids_enviados"].append(str(produto["itemId"]))
    estado["horarios_postados"].append(horario_disparado)
    salvar_estado(estado)


if __name__ == "__main__":
    main()
