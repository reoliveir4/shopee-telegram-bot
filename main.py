"""
Bot de automação: Shopee Affiliate API -> IA (texto) -> Telegram

Fluxo:
1. A cada execução (disparada a cada 10 minutos pelo GitHub Actions), o
   script verifica se algum horário-alvo do dia já venceu e ainda não foi
   postado. Se sim, busca um produto mais vendido na Shopee, gera um texto
   com IA (Google Gemini) e posta no canal do Telegram no formato:
     [título]
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

KEYWORD_FIXA = os.environ.get("SHOPEE_KEYWORD", "").strip()

# ---------------------------------------------------------------------------
# CONFIGURAÇÃO DE HORÁRIOS
# ---------------------------------------------------------------------------
POSTS_POR_DIA = 50
HORA_INICIO = "05:00"
HORA_FIM = "23:00"

ARQUIVO_ESTADO = "estado.json"


def gerar_horarios_alvo() -> list:
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


def _enesimo_dia_semana(ano: int, mes: int, dia_semana: int, n: int) -> date:
    d = date(ano, mes, 1)
    delta = (dia_semana - d.weekday()) % 7
    dia = 1 + delta + 7 * (n - 1)
    return date(ano, mes, dia)


def _ultimo_dia_semana(ano: int, mes: int, dia_semana: int) -> date:
    if mes == 12:
        proximo_mes = date(ano + 1, 1, 1)
    else:
        proximo_mes = date(ano, mes + 1, 1)
    ultimo_dia_do_mes = proximo_mes - timedelta(days=1)
    delta = (ultimo_dia_do_mes.weekday() - dia_semana) % 7
    return ultimo_dia_do_mes - timedelta(days=delta)


def _pascoa(ano: int) -> date:
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


CAMPANHAS_POR_DATA = [
    {"nome": "Dia do Consumidor", "regra": ("fixa", 3, 15), "dias_antecedencia": 10,
     "keywords": ["promocao dia do consumidor"]},
    {"nome": "Páscoa", "regra": ("pascoa",), "dias_antecedencia": 10,
     "keywords": ["ovo de pascoa gourmet", "chocolate premium pascoa", "cesta de pascoa"]},
    {"nome": "Dia das Mães", "regra": ("enesimo_dia_semana", 5, 2, 6), "dias_antecedencia": 10,
     "keywords": [
         "blusa feminina", "conjunto pijama feminino", "bolsa feminina",
         "perfume feminino", "kit skincare feminino", "tenis feminino casual",
         "relogio feminino", "semijoia colar feminino",
     ]},
    {"nome": "Dia dos Namorados", "regra": ("fixa", 6, 12), "dias_antecedencia": 10,
     "keywords": [
         "perfume unissex", "conjunto pijama casal", "kit spa casal",
         "semijoia presente", "fone de ouvido bluetooth",
     ]},
    {"nome": "Dia dos Pais", "regra": ("enesimo_dia_semana", 8, 2, 6), "dias_antecedencia": 10,
     "keywords": [
         "dia dos pais", "kit 4 camiseta oversized", "kit 2 shorts bermuda",
         "kit 3 bermudas moletom", "kit 3 calças jogger",
         "camisa masculina gola social", "kit 2 bermudas jeans",
         "kit cueca samba canção", "meias esportivas masculinas",
         "blazer calça slim masculino", "jaqueta de couro masculina",
         "conjunto masculino", "kit sapatênis masculino",
         "kit presente masculino boné carteira e relógio",
         "tenis masculino", "kit 3 pares sapatenis",
         "curren relógio masculino", "kit relógio masculino",
         "relógio de quartzo empresarial", "foxbox relógio masculino",
         "poedagar luxo relógio", "perfume masculino",
         "body splash masculino", "original armaf club perfume",
     ]},
    {"nome": "Dia das Crianças", "regra": ("fixa", 10, 12), "dias_antecedencia": 10,
     "keywords": [
         "brinquedo educativo infantil", "boneca infantil",
         "carrinho de brinquedo infantil", "jogo de tabuleiro infantil",
         "quebra cabeca infantil", "bicicleta infantil",
     ]},
    {"nome": "Black Friday", "regra": ("ultimo_dia_semana", 11, 4), "dias_antecedencia": 20,
     "keywords": ["black friday"]},
    {"nome": "Natal", "regra": ("fixa", 12, 25), "dias_antecedencia": 15,
     "keywords": [
         "enfeite de natal decoracao", "perfume presente natal",
         "relogio presente", "fone de ouvido bluetooth",
         "brinquedo infantil natal", "kit spa presente",
     ]},
]


CATEGORIAS_EM_COOLDOWN = 12


def escolher_keyword_do_dia(estado: dict) -> str:
    hoje = datetime.now().date()

    for campanha in CAMPANHAS_POR_DATA:
        for ano in (hoje.year, hoje.year + 1):
            evento = _data_do_evento(campanha, ano)
            inicio = evento - timedelta(days=campanha["dias_antecedencia"])
            if inicio <= hoje <= evento:
                recentes = estado.get("categorias_recentes", [])
                candidatas = [k for k in campanha["keywords"] if k not in recentes]
                if not candidatas:
                    candidatas = campanha["keywords"]

                keyword_sorteada = random.choice(candidatas)
                print(f"Campanha ativa hoje: {campanha['nome']} (evento em {evento}, keyword sorteada: '{keyword_sorteada}')")

                estado.setdefault("categorias_recentes", [])
                estado["categorias_recentes"].append(keyword_sorteada)
                estado["categorias_recentes"] = estado["categorias_recentes"][-CATEGORIAS_EM_COOLDOWN:]

                return keyword_sorteada

    if KEYWORD_FIXA:
        return KEYWORD_FIXA

    recentes = estado.get("categorias_recentes", [])
    candidatas = [t for t in TAGS_POPULARES if t not in recentes]
    if not candidatas:
        candidatas = TAGS_POPULARES

    keyword_sorteada = random.choice(candidatas)
    print(f"Nenhuma campanha ativa. Categoria sorteada: '{keyword_sorteada}'")

    estado.setdefault("categorias_recentes", [])
    estado["categorias_recentes"].append(keyword_sorteada)
    estado["categorias_recentes"] = estado["categorias_recentes"][-CATEGORIAS_EM_COOLDOWN:]

    return keyword_sorteada


TAGS_POPULARES = [
    "organizador de armario multiuso", "caixa organizadora empilhavel",
    "prateleira organizadora de parede", "varal de roupas retratil",
    "dispenser de sabonete para pia", "esfregao de limpeza multiuso",
    "produtos de limpeza para casa", "luminaria de decoracao para quarto",
    "decoracao minimalista para casa", "organizador de gaveta multiuso",
    "cabide organizador de roupas", "tapete decorativo para sala",
    "cortina de blackout para quarto", "porta trecos organizador de mesa",
    "air fryer eletrica", "panela eletrica multifuncional",
    "processador de alimentos eletrico", "potes hermeticos para cozinha",
    "descascador de legumes eletrico", "escova eletrica para limpar grelha",
    "liquidificador portatil eletrico", "kit de facas para cozinha",
    "organizador de temperos para cozinha", "forma de silicone para cozinha",
    "robo aspirador de po", "purificador de agua eletrico",
    "ventilador de torre eletrico", "passadeira de roupas a vapor",
    "cooktop portatil eletrico", "umidificador de ar eletrico",
    "aspirador de po portatil", "secador de cabelo profissional",
    "alisadora de cabelo eletrica", "massageador eletrico portatil",
    "colchao massageador eletrico", "barbeador eletrico recarregavel",
    "escova de dente eletrica", "aparador de pelos eletrico",
    "jogo de lencol 400 fios", "manta cobertor para casal",
    "travesseiro de algodao", "toalha de banho felpuda",
    "kit de tapetes para banheiro", "fone de ouvido bluetooth",
    "power bank portatil", "mini projetor portatil",
    "camera digital compacta", "console de video game retro portatil",
    "tablet android", "caixa de som bluetooth portatil",
    "smartwatch relogio inteligente", "mouse e teclado sem fio",
    "carregador rapido para celular", "estacao de musculacao dobravel",
    "halteres ajustaveis", "esteira eletrica dobravel",
    "faixa elastica para exercicio", "corda de pular para treino",
    "kit de camisetas basicas", "moletom unissex", "jaqueta corta vento",
    "mochila para notebook", "oculos de sol unissex",
    "canivete multifuncional", "ferramenta dobravel multiuso",
    "caneta 3d para desenho", "revolver de pressao replica brinquedo",
    "lanterna tatica recarregavel", "kit de ferramentas para casa",
    "mochila impermeavel para viagem",
]

LIMIT = 50

PALAVRAS_BLOQUEADAS = [
    "barba", "barbear", "barbeador",
    "costura", "linha de costura", "agulha de costura", "kit de costura",
    "aparelho de barbear",
    "vidro eletrico", "maquina de vidro", "retrovisor", "farol",
    "parachoque", "para-choque", "motorista", "carona", "dianteira",
    "traseira", "automotivo", "veicular", "regulador de vidro",
    "peca automotiva", "capa de banco",
]


def produto_bloqueado(produto: dict) -> bool:
    nome = str(produto.get("productName", "")).lower()

    for palavra in PALAVRAS_BLOQUEADAS:
        if palavra == "tesoura de corte":
            if "tesoura" in nome and not any(
                termo in nome for termo in ["cozinha", "culinaria", "culinária", "cozinhar"]
            ):
                return True
        elif palavra in nome:
            return True
    return False


EXCLUSOES_POR_CATEGORIA = {
    "revolver de pressao replica brinquedo": [
        "capa", "coldre", "estojo", "case", "bolsa", "cinto", "suporte de parede",
    ],
}


def produto_bloqueado_pela_categoria(produto: dict, keyword: str) -> bool:
    exclusoes = EXCLUSOES_POR_CATEGORIA.get(keyword)
    if not exclusoes:
        return False
    nome = str(produto.get("productName", "")).lower()
    return any(termo in nome for termo in exclusoes)


DIAS_SEM_REPETIR_PRODUTO = 7


def carregar_estado() -> dict:
    hoje = datetime.now().strftime("%Y-%m-%d")
    padrao = {"data": hoje, "horarios_postados": [], "produtos_enviados": {}}

    if not os.path.exists(ARQUIVO_ESTADO):
        return padrao

    try:
        with open(ARQUIVO_ESTADO, "r", encoding="utf-8") as f:
            dados = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return padrao

    dados.setdefault("produtos_enviados", {})
    dados.setdefault("horarios_postados", [])

    if dados.get("data") != hoje:
        dados["data"] = hoje
        dados["horarios_postados"] = []

    return dados


def limpar_produtos_antigos(estado: dict):
    hoje = datetime.now().date()
    limite = hoje - timedelta(days=DIAS_SEM_REPETIR_PRODUTO)

    produtos_validos = {}
    for item_id, info in estado["produtos_enviados"].items():
        data_str = info.get("data") if isinstance(info, dict) else info
        try:
            data_envio = datetime.strptime(data_str, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        if data_envio >= limite:
            produtos_validos[item_id] = info if isinstance(info, dict) else {"data": info, "nome": ""}

    estado["produtos_enviados"] = produtos_validos


def salvar_estado(estado: dict):
    with open(ARQUIVO_ESTADO, "w", encoding="utf-8") as f:
        json.dump(estado, f)


def gerar_assinatura(app_id: str, timestamp: int, payload: str, secret: str) -> str:
    base_string = f"{app_id}{timestamp}{payload}{secret}"
    return hashlib.sha256(base_string.encode("utf-8")).hexdigest()


def buscar_produtos_shopee(keyword: str, limit: int = 5) -> list:
    query = """
    query buscarProdutos($keyword: String, $limit: Int) {
      productOfferV2(keyword: $keyword, limit: $limit, sortType: 5) {
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


def gerar_conteudo_ia(produto: dict) -> dict:
    prompt = f"""
Você escreve títulos de anúncio para um canal de ofertas no Telegram, no
estilo direto e simples usado em anúncios da Shopee — SEM gatilho mental,
SEM pergunta retórica, SEM frases tipo "cansado de", "já pensou em".

Analise o produto abaixo e responda em formato JSON puro, com EXATAMENTE
estas duas chaves:

- "titulo": um título curto e direto (até 12 palavras), descrevendo o
  produto de forma objetiva e chamativa, do jeito que uma pessoa real
  descreveria o produto pra vender rápido. Pode ter um toque de
  personalidade/humor leve, mas sem ser uma pergunta nem um gatilho
  psicológico. Exemplos do estilo esperado (não copie, apenas siga o
  padrão):
  "VARAL DE PAREDE AÇO REDOBRÁVEL 80KG 2 HASTES"
  "2 TRAVESSEIROS DE ALGODÃO NO PRECINHO"
  "LUMINÁRIA DE CABECEIRA PERFEITA PARA O SEU QUARTO"
  "KIT BERMUDAS MASCULINA PARA TREINOS INTENSOS"
  "PASTA DE DENTE PREMIUM PARA TIRAR O BAFÃO"
- "descricao": uma frase curta (1 linha), com um emoji relevante no
  início, seguida do nome do produto e um traço "–" e uma explicação
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

    tentativas = 3
    espera = 15
    resp = None
    for tentativa in range(1, tentativas + 1):
        resp = requests.post(
            url,
            headers={"content-type": "application/json"},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=60,
        )
        if resp.status_code != 429:
            break
        print(f"Limite de requisições do Gemini atingido (tentativa {tentativa}/{tentativas}). Aguardando {espera}s...")
        time.sleep(espera)
        espera *= 2

    resp.raise_for_status()
    texto_bruto = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()

    texto_limpo = re.sub(r"^```(json)?|```$", "", texto_bruto.strip(), flags=re.MULTILINE).strip()

    try:
        conteudo = json.loads(texto_limpo)
        titulo = conteudo.get("titulo", "").strip()
        descricao = conteudo.get("descricao", "").strip()

        if titulo and descricao:
            return {"gancho": titulo.upper(), "descricao": descricao}
    except (json.JSONDecodeError, KeyError):
        pass

    return {
        "gancho": str(produto.get("productName", "")).upper()[:120],
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


MAX_POSTS_POR_EXECUCAO = 15


def _palavras_significativas(nome: str) -> set:
    palavras = re.findall(r"[a-zà-ú]{4,}", nome.lower())
    return set(palavras)


def produtos_parecidos(nome1: str, nome2: str) -> bool:
    p1 = _palavras_significativas(nome1)
    p2 = _palavras_significativas(nome2)
    if not p1 or not p2:
        return False
    intersecao = p1 & p2
    menor = min(len(p1), len(p2))
    return len(intersecao) / menor >= 0.6


TOP_N_PARA_SORTEIO = 25


def postar_um_produto(keyword_hint_dia: str, estado: dict) -> bool:
    keyword = keyword_hint_dia
    print(f"Buscando produtos para a palavra-chave: '{keyword}'...")
    produtos = buscar_produtos_shopee(keyword, LIMIT)

    if not produtos:
        print("Nenhum produto encontrado para essa keyword.")
        return False

    antes = len(produtos)
    produtos = [p for p in produtos if not produto_bloqueado(p)]
    if len(produtos) < antes:
        print(f"{antes - len(produtos)} produto(s) removido(s) pela blacklist.")

    antes = len(produtos)
    produtos = [p for p in produtos if not produto_bloqueado_pela_categoria(p, keyword)]
    if len(produtos) < antes:
        print(f"{antes - len(produtos)} produto(s) removido(s) pela exclusão específica da categoria '{keyword}'.")

    if not produtos:
        print("Todos os produtos encontrados estavam na blacklist.")
        return False

    enviados = estado["produtos_enviados"]
    nomes_recentes = [info["nome"] for info in enviados.values() if isinstance(info, dict) and info.get("nome")]

    produtos_novos = []
    for p in produtos:
        if str(p["itemId"]) in enviados:
            continue
        if any(produtos_parecidos(p["productName"], nome_antigo) for nome_antigo in nomes_recentes):
            continue
        produtos_novos.append(p)

    if not produtos_novos:
        print(f"Todos os produtos encontrados já foram postados (ou são parecidos com algo postado) nos últimos {DIAS_SEM_REPETIR_PRODUTO} dias. Permitindo repetição.")
        produtos_novos = produtos

    produtos_novos.sort(key=lambda p: float(p.get("commissionRate", 0) or 0), reverse=True)
    candidatos = produtos_novos[:TOP_N_PARA_SORTEIO]
    produto = random.choice(candidatos)
    print(f"Produto escolhido (comissão {produto.get('commissionRate')}): {produto['productName']}")

    print("Gerando conteúdo com IA...")
    conteudo = gerar_conteudo_ia(produto)
    print(f"Título: {conteudo['gancho']}\nDescrição: {conteudo['descricao']}")

    print("Enviando para o Telegram...")
    resultado = enviar_para_telegram(produto, conteudo)
    print("Enviado com sucesso!", resultado.get("ok"))

    estado["produtos_enviados"][str(produto["itemId"])] = {
        "data": datetime.now().strftime("%Y-%m-%d"),
        "nome": produto["productName"],
    }
    return True


def main():
    agora_brt = datetime.utcnow() - timedelta(hours=3)
    agora_hm = agora_brt.strftime("%H:%M")

    estado = carregar_estado()
    limpar_produtos_antigos(estado)
    horarios_alvo = gerar_horarios_alvo()

    horarios_pendentes = [
        h for h in horarios_alvo
        if h <= agora_hm and h not in estado["horarios_postados"]
    ]

    if not horarios_pendentes:
        print(f"Nenhum horário pendente agora ({agora_hm} BRT). Encerrando sem postar.")
        return

    a_processar = horarios_pendentes[:MAX_POSTS_POR_EXECUCAO]
    print(
        f"{len(horarios_pendentes)} horário(s) pendente(s) (agora são {agora_hm} BRT). "
        f"Processando {len(a_processar)} nesta execução..."
    )

    for horario_disparado in a_processar:
        print(f"\n--- Horário-alvo '{horario_disparado}' ---")
        try:
            keyword = escolher_keyword_do_dia(estado)
            postou = postar_um_produto(keyword, estado)
        except Exception as erro:
            print(f"ERRO ao processar o horário '{horario_disparado}': {erro}")
            print("Pulando para o próximo horário pendente, se houver...")
            if horario_disparado != a_processar[-1]:
                time.sleep(8)
            continue

        estado["horarios_postados"].append(horario_disparado)
        salvar_estado(estado)

        if postou and horario_disparado != a_processar[-1]:
            time.sleep(8)


if __name__ == "__main__":
    main()
