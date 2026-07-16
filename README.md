# Bot Shopee -> Telegram (100% automático e gratuito)

Este projeto busca produtos com comissão na API oficial de afiliados da
Shopee, gera um texto de divulgação com IA e posta automaticamente no seu
canal do Telegram, usando o GitHub Actions como agendador gratuito.

## O que você precisa antes de começar

1. Conta no **GitHub** (gratuita): https://github.com
2. **App ID** e **App Secret** da API de afiliados da Shopee (você já tem)
3. **Token do bot do Telegram** (veja abaixo como criar)
4. **ID do seu canal do Telegram**
5. **Chave de API da Anthropic** (para gerar os textos): https://console.anthropic.com

---

## Passo 1 — Criar o bot no Telegram

1. Abra o Telegram e procure por **@BotFather**
2. Envie `/newbot` e siga as instruções (nome e username do bot)
3. O BotFather vai te dar um **token**, algo como:
   `123456789:AAExemploDeTokenAquiXXXXXXXXXXXXXXXXX`
4. Guarde esse token — é o `TELEGRAM_BOT_TOKEN`

## Passo 2 — Adicionar o bot como admin do seu canal

1. Vá até o seu canal no Telegram
2. Administradores → Adicionar Administrador
3. Procure pelo username do bot que você criou e adicione
4. Dê permissão de **postar mensagens**

## Passo 3 — Descobrir o ID do seu canal

- Se o canal for público, o ID é simplesmente `@nomedocanal`
- Se for privado, encaminhe uma mensagem do canal para o bot
  **@userinfobot** — ele vai te mostrar o ID (algo como `-1001234567890`)

## Passo 4 — Criar o repositório no GitHub

1. Crie um novo repositório (pode ser privado) no GitHub
2. Faça upload de todos os arquivos desta pasta (`main.py`,
   `requirements.txt`, e a pasta `.github/workflows/postar.yml`)
   - Pode arrastar os arquivos direto pela interface web do GitHub, em
     "Add file" → "Upload files"

## Passo 5 — Configurar as chaves secretas (Secrets)

No repositório criado:

1. Vá em **Settings** → **Secrets and variables** → **Actions**
2. Clique em **New repository secret** e adicione, um por um:

| Nome do Secret         | Valor                                  |
|------------------------|-----------------------------------------|
| `SHOPEE_APP_ID`        | Seu App ID da Shopee                    |
| `SHOPEE_APP_SECRET`    | Seu App Secret da Shopee                |
| `TELEGRAM_BOT_TOKEN`   | Token do bot (Passo 1)                  |
| `TELEGRAM_CHAT_ID`     | ID do canal (Passo 3)                   |
| `ANTHROPIC_API_KEY`    | Sua chave de API da Anthropic           |

## Passo 6 — Ativar e testar

1. Vá na aba **Actions** do repositório
2. Clique no workflow **"Postar oferta Shopee no Telegram"**
3. Clique em **Run workflow** para testar manualmente agora
4. Veja os logs — se tudo estiver certo, a mensagem vai aparecer no seu canal
5. Depois disso, ele roda sozinho nos horários configurados (12h e 18h UTC,
   ajustável no arquivo `.github/workflows/postar.yml`)

## Como ajustar

- **Horários de postagem**: edite os valores de `cron` no arquivo
  `.github/workflows/postar.yml`. Use https://crontab.guru para gerar novos
  horários.
- **Palavra-chave / nicho de produtos**: altere `SHOPEE_KEYWORD` no mesmo
  arquivo (ex: "eletronicos", "beleza", "casa").
- **Quantidade de produtos por rodada**: hoje o script busca 5 e posta o de
  maior comissão. Ajuste a variável `LIMIT` em `main.py` se quiser mudar.
- **Frequência**: para postar mais vezes ao dia, adicione mais linhas de
  `cron` no workflow.

## Observações importantes

- O GitHub Actions gratuito dá 2.000 minutos/mês — este script roda em
  segundos, então você está muito longe do limite.
- Nunca coloque suas chaves direto no código — use sempre os **Secrets** do
  GitHub, como configurado aqui.
- Se a Shopee retornar erro de autenticação, confira se o relógio do
  servidor não está muito distante do horário atual (o timestamp da
  assinatura precisa estar próximo do horário real).
