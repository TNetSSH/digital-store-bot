# Digital Store Bot 1.1.0

Bot de vendas para conversas privadas no Telegram, com catálogo personalizável,
pagamento único e entrega automática de textos, arquivos e links.

## Recursos

- Categorias e produtos com ordem e visibilidade configuráveis.
- Botões nas cores nativas padrão, azul, verde e vermelho.
- Emoji comum no texto e emoji premium pelo `icon_custom_emoji_id`.
- Pagamento único por Telegram Stars.
- PIX dinâmico pelo Mercado Pago, com QR Code, copia e cola e webhook assinado.
- Validação de usuário, moeda, valor e identificador antes da entrega.
- Entrega idempotente de vários textos, arquivos e links na ordem definida.
- Área **Minhas compras** com reenvio do conteúdo.
- Estoque ilimitado, por quantidade ou composto por itens únicos.
- Reserva transacional, bloqueio automático de produtos esgotados e alerta de estoque baixo.
- Painel administrativo dentro do próprio Telegram.
- Banco SQLite em modo WAL e serviço HTTP local para healthcheck/webhook.
- Operação somente em conversa privada; não há lógica de grupos ou moderação.

## Regra do Telegram sobre produtos digitais

O Telegram exige Stars para bens e serviços digitais vendidos dentro dos bots. O módulo PIX
fica isolado, desligado por padrão e pode ser ativado no `.env` e no painel para usos
compatíveis com as regras aplicáveis. Consulte a documentação oficial antes de ativá-lo para
um produto.

## Requisitos

- Ubuntu 22.04 ou superior.
- Python 3.11 ou superior.
- Token criado no `@BotFather`.
- ID numérico do administrador.
- Domínio HTTPS público para receber notificações do Mercado Pago, caso use PIX.
- Conta Mercado Pago com uma chave PIX cadastrada.

## Instalação no Ubuntu

```bash
sudo apt update
sudo apt install -y python3 python3-venv nginx git
sudo useradd --system --home /opt/digital-store-bot --shell /usr/sbin/nologin digitalstore
sudo git clone https://github.com/TNetSSH/digital-store-bot.git /opt/digital-store-bot
sudo chown -R digitalstore:digitalstore /opt/digital-store-bot
sudo -u digitalstore python3 -m venv /opt/digital-store-bot/.venv
sudo -u digitalstore /opt/digital-store-bot/.venv/bin/pip install -r /opt/digital-store-bot/requirements.txt
sudo -u digitalstore cp /opt/digital-store-bot/.env.example /opt/digital-store-bot/.env
sudo chmod 600 /opt/digital-store-bot/.env
sudo mkdir -p /opt/digital-store-bot/data
sudo chown digitalstore:digitalstore /opt/digital-store-bot/data
```

Edite as configurações:

```bash
sudo nano /opt/digital-store-bot/.env
```

Campos obrigatórios para iniciar:

```dotenv
BOT_TOKEN=token_do_bot
ADMIN_IDS=123456789
```

É possível informar vários administradores separados por vírgula. Não coloque o token ou
credenciais do Mercado Pago no código-fonte.

### Serviço systemd

```bash
sudo cp /opt/digital-store-bot/deploy/digital-store-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now digital-store-bot
sudo systemctl status digital-store-bot --no-pager
```

Logs em tempo real:

```bash
sudo journalctl -u digital-store-bot -f
```

O bot inicia por long polling. Não é necessário configurar webhook do Telegram.

## Configuração do PIX

Preencha no `.env`:

```dotenv
PUBLIC_BASE_URL=https://bot.seudominio.com
PIX_ENABLED=true
MERCADO_PAGO_ACCESS_TOKEN=APP_USR-...
MERCADO_PAGO_WEBHOOK_SECRET=...
MERCADO_PAGO_PAYER_EMAIL=pagamentos@seudominio.com
PIX_EXPIRATION_MINUTES=30
```

`MERCADO_PAGO_PAYER_EMAIL` é um e-mail técnico da loja usado somente na requisição à API.
O cliente não precisa informar e-mail; o primeiro nome vem automaticamente do Telegram.

Copie o exemplo do Nginx, troque o domínio e ative-o:

```bash
sudo cp /opt/digital-store-bot/deploy/nginx.conf /etc/nginx/sites-available/digital-store-bot
sudo nano /etc/nginx/sites-available/digital-store-bot
sudo ln -s /etc/nginx/sites-available/digital-store-bot /etc/nginx/sites-enabled/digital-store-bot
sudo nginx -t
sudo systemctl reload nginx
```

No painel do Mercado Pago, configure as notificações de **Pagamentos** para:

```text
https://bot.seudominio.com/webhooks/mercadopago
```

Copie a assinatura secreta fornecida pelo painel para `MERCADO_PAGO_WEBHOOK_SECRET` e reinicie:

```bash
sudo systemctl restart digital-store-bot
curl http://127.0.0.1:8081/health
```

Se o domínio estiver no Cloudflare com SSL **Flexível**, o bloco Nginx em HTTP funciona como
origem. Mantenha o proxy ativo e confirme que a URL pública do healthcheck responde.

Depois, acesse `/admin` no privado, abra **Personalização → Pagamentos** e ative o PIX.
O painel não permite ativá-lo enquanto alguma configuração obrigatória estiver ausente.

## Primeiro cadastro

1. Envie `/admin` no privado.
2. Cadastre uma categoria; ela começa oculta.
3. Cadastre um produto e defina os preços.
4. Abra **Estoque** e escolha o modo desejado.
5. Abra **Conteúdos de entrega** e adicione textos, arquivos ou links.
6. Publique o produto.
7. Publique a categoria.

Um produto só pode ser publicado se possuir conteúdo (ou itens únicos) e ao menos um preço
habilitado.

## Controle de estoque

Cada produto possui um dos seguintes modos:

- **Ilimitado:** não controla quantidade e permite vendas contínuas.
- **Por quantidade:** o administrador informa quantas unidades estão disponíveis.
- **Itens únicos:** cada linha cadastrada representa um código, conta, licença ou link exclusivo.

No modo de itens únicos, envie até 500 itens por mensagem, usando uma linha para cada item. O
bot reserva uma linha diferente para cada pedido e entrega esse conteúdo automaticamente após
a aprovação. Em **Minhas compras**, o comprador sempre recebe novamente o mesmo item.

Uma unidade é reservada antes da cobrança. Pedidos expirados, cancelados ou que falharem
devolvem automaticamente a unidade ao estoque; pagamentos aprovados consomem a reserva. Isso
impede duas compras simultâneas da última unidade. Quando o saldo chega a zero, os botões de
pagamento são bloqueados e o produto aparece como esgotado.

O limite de estoque baixo é configurável por produto. O aviso é enviado uma única vez aos
administradores e volta a ser habilitado quando o estoque é reabastecido. Itens reservados ou
vendidos são preservados e não podem ser excluídos pelo painel.

## Personalização de botões

No painel é possível editar texto, cor e ID do emoji premium dos botões principais,
categorias, produtos e links entregues. As cores aceitas pelo Telegram são:

- `default`: aparência do aplicativo.
- `primary`: azul.
- `success`: verde.
- `danger`: vermelho.

O Telegram não aceita RGB ou hexadecimal em botões. Para emoji premium, envie somente o ID
numérico. O proprietário do bot precisa atender às regras de elegibilidade do Telegram para
uso de custom emoji pelo bot.

Nas mensagens e descrições, o painel aceita tanto o HTML oficial quanto a marcação usada no
bot anterior. Exemplo compatível:

```html
<emoji id='5345952995991363418'>👋</> <i>Olá, {NAME}!</i>
```

Antes do envio, ela é convertida para a tag oficial `<tg-emoji>` do Telegram.

## Atualização

```bash
cd /opt/digital-store-bot
sudo systemctl stop digital-store-bot
sudo -u digitalstore git pull --ff-only
sudo -u digitalstore .venv/bin/pip install -r requirements.txt
sudo systemctl start digital-store-bot
sudo systemctl status digital-store-bot --no-pager
```

O banco fica em `/opt/digital-store-bot/data/store.db`. Faça backup dessa pasta antes de
atualizações relevantes.

## Desenvolvimento e testes

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
ruff check .
pytest -q
python -m bot
```

## Segurança implementada

- Credenciais exclusivamente no `.env`.
- Comparação HMAC em tempo constante para o webhook do Mercado Pago.
- Consulta do pagamento diretamente na API antes da aprovação.
- Conferência de referência externa, método, moeda e valor.
- Chaves de idempotência na criação do PIX.
- Identificadores únicos e registro por item para impedir entregas repetidas.
- Reserva de estoque em transação SQLite antes de gerar a cobrança.
- Entrega idempotente do item exclusivo associado ao pedido.
- Painel limitado aos IDs presentes em `ADMIN_IDS`.
