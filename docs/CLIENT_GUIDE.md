# HonestPool Scraper — Guia do cliente

Versão 3 (Etapa 22), outubro de 2026, com a instalação para Windows 11 da Etapa 26. Substitui as
versões 1 (demonstração) e 2.

> **Windows 11:** siga `INSTALACAO_WINDOWS_11.md`. Basta extrair o ZIP e dar dois cliques em
> `setup_windows.bat`, `configurar_2captcha.bat` e `iniciar_honestpool.bat`; o menu faz as
> extrações, a retomada, as exportações e mostra o progresso, sem digitar comandos. Os comandos
> `python main.py ...` deste guia são para Linux ou para uso avançado. No Windows, quando um
> comando for necessário, use `.venv\Scripts\python.exe main.py ...` na pasta do programa.

> **Situação atual:** o scraper funciona para intervalos pequenos e controlados, com uma
> pessoa acompanhando. O CAPTCHA de imagem pode ser resolvido automaticamente pelo 2Captcha
> (seção 9A), com a verificação manual como alternativa. A integração foi validada por testes
> offline; capturas de tela enviadas pelo cliente mostram um CAPTCHA real sendo resolvido
> automaticamente, mas o banco de dados dessa execução ainda não foi inspecionado. A seção 14
> separa o que foi validado, o que foi demonstrado e o que ainda falta confirmar. A operação
> sem supervisão **não** é prometida. A extração completa das 671.089 páginas **não** foi
> feita e não é viável hoje (ver seção 11).

## Dois fluxos de trabalho

O programa oferece duas formas de obter registros verificados. As duas gravam no mesmo banco
SQLite e geram as mesmas colunas na exportação (`Number`, `Start hex`, `End hex`, `Status`).

| | Fluxo A — extração pelo site | Fluxo B — importação autorizada |
|---|---|---|
| De onde vêm os números | Páginas originais da listagem, abertas com Playwright | Arquivo TXT ou CSV entregue por uma fonte autorizada |
| Acessa o site | Sim, com `--live` | **Não**; funciona totalmente offline |
| CAPTCHA | Resolvido pelo 2Captcha, se configurado (seção 9A), ou manualmente por uma pessoa | Não se aplica (não há acesso ao site) |
| Valores hexadecimais | Lidos da página e conferidos com a fórmula | Calculados localmente pela fórmula |
| Comando | `python main.py --start-page N --end-page M --live` | `python main.py --import-verified arquivo.txt` |
| Seções | 4, 5, 6, 8, 9 | 7A |

**Fluxo A** — extrai os registros verificados diretamente das páginas originais da listagem,
usando um navegador Chromium controlado pelo Playwright. O CAPTCHA aparece no início de cada
sessão do navegador e, de tempos em tempos, de novo (num teste de 14/10/2026, depois de 19
páginas). Com o 2Captcha configurado (seção 9A), a imagem do CAPTCHA é enviada ao 2Captcha e a
resposta é digitada no formulário do site; sem essa configuração, ou se a resolução automática
falhar, uma pessoa resolve o CAPTCHA manualmente, até 10 vezes por execução
(`--max-manual-verifications`). Depois disso a execução para, o progresso fica salvo e
`python main.py --resume --live` continua das páginas pendentes.

**Fluxo B** — importa números de segmentos verificados de um arquivo TXT ou CSV fornecido por
uma fonte autorizada e calcula localmente os intervalos hexadecimais completos
(`início = 0x400000000000000000 + (N − 1) × 2^44`, `fim = início + 2^44`).

Pontos importantes:

- O Fluxo B **não resolve o CAPTCHA**. Ele apenas evita acessar o site quando já existe uma
  lista autorizada. No Fluxo A, o CAPTCHA continua sendo resolvido pelo 2Captcha ou por uma
  pessoa.
- Os números importados precisam vir de uma **fonte autorizada e confiável** (por exemplo, uma
  exportação fornecida pelo operador do site). O programa não consegue verificar a origem do
  arquivo; quem importa precisa declarar a fonte. Nunca importe números estimados ou
  não verificados.
- A extração completa das 671.089 páginas continua **incompleta**: até agora foram extraídas
  apenas as páginas 1 a 3 (10 registros verificados).
- A **API oficial não fornece uma lista de registros verificados**. Ela apenas distribui
  intervalos para varredura (`getRange`) e recebe resultados (`setRange`), sem consulta de
  status nem exportação em massa. Por isso, o Fluxo B depende de um arquivo entregue pelo
  operador ou por outra fonte autorizada.

## 1. O que o scraper faz

O site [honestpool.ru](https://honestpool.ru/puzzle?id=71) divide o espaço de chaves do
puzzle 71 em 67.108.864 segmentos, listados em 671.089 páginas de 100 linhas (a última tem 64).
Cada segmento aparece como "Проверено" (verificado) ou "Не проверено" (não verificado).

O scraper:

- abre as páginas da listagem em um navegador Chromium visível (Playwright), uma de cada vez;
- guarda **apenas os segmentos verificados**, ou seja, as linhas com o selo verde "Проверено";
- confere cada página antes de salvar: a página precisa ser a solicitada, ter todas as linhas e
  ter números de segmento coerentes. Uma página incompleta ou inesperada nunca é salva;
- grava o resultado em um banco SQLite local e exporta para CSV e Excel.

## 2. Colunas extraídas

| Coluna | Conteúdo | Exemplo |
|---|---|---|
| Number | Número do segmento (1 a 67.108.864) | `101` |
| Start hex | Início do intervalo, 18 dígitos hexadecimais | `400006400000000000` |
| End hex | Fim do intervalo, 18 dígitos hexadecimais | `400006500000000000` |
| Status | Sempre `Проверено` (verificado) | `Проверено` |

Os valores hexadecimais são completos. O scraper confere que eles correspondem ao número do
segmento pela fórmula do site: início = `0x400000000000000000 + (Number − 1) × 2^44`.
No Excel eles são gravados como texto, para que não sejam convertidos em número
(por exemplo, `4,00001E+17`).

## 3. Instalação

Requisitos: Python 3.11 ou 3.12. O Fluxo A também precisa de acesso ao site pelo
navegador; o Fluxo B funciona sem internet (o Chromium só é necessário para o Fluxo A).

### Windows 11 (recomendado)

Siga `INSTALACAO_WINDOWS_11.md`: extraia o ZIP para uma pasta nova (`C:\HonestPool-v3` no pacote
de teste de diagnóstico v3, nunca por cima de uma instalação anterior) e dê dois cliques em
`setup_windows.bat`. Ele verifica o Python 3.12 ou 3.11, cria o ambiente `.venv`, instala as
dependências e o Chromium e cria as pastas `data`, `logs` e `config`, com mensagens em português.
Para conferir a primeira instalação, use `CHECKLIST_ACEITACAO_WINDOWS_11.md`. Esses arquivos
ainda não foram executados num computador com Windows.

### Linux (Ubuntu)

```bash
cd honestpool-scraper
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m playwright install chromium
python -m pytest -q          # deve terminar com todos os testes aprovados
```

Se o Chromium não abrir, instale as bibliotecas do sistema:
`sudo .venv/bin/python -m playwright install-deps chromium`.

### Windows (PowerShell, instalação manual para uso avançado)

```powershell
cd C:\HonestPool-v3
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m pytest -q
```

Para usar o 2Captcha, configure a chave depois da instalação (seção 9A). Sem a chave, o
CAPTCHA é resolvido manualmente.

## 4. Fluxo A — começar a partir de uma página escolhida

O intervalo de páginas é sempre escolhido explicitamente. Não existe um intervalo padrão, então
o scraper nunca inicia sozinho a extração completa.

```bash
# 1) Apenas planejar: registra as páginas 350000..350100 e mostra o que falta. Nada é baixado.
python main.py --start-page 350000 --end-page 350100

# 2) Baixar 1 página desse intervalo (o padrão é 1 página por execução)
python main.py --start-page 350000 --end-page 350100 --live

# 3) Baixar até 20 páginas, com tempo maior para páginas lentas
python main.py --start-page 350000 --end-page 350100 --live --max-pages 20 --nav-timeout 60
```

- `--live` é obrigatório para acessar o site. Sem ele, o comando apenas planeja.
- `--max-pages` limita quantas páginas são baixadas por execução.
- Ao escolher um novo intervalo, ele substitui o intervalo salvo. Os registros e as páginas
  concluídas de intervalos anteriores continuam no banco, e páginas concluídas nunca são
  baixadas de novo.

## 5. Retomar após uma interrupção

```bash
python main.py --resume --live                                # continua o intervalo salvo (1 página)
python main.py --resume --live --max-pages 20 --nav-timeout 60
python main.py --resume                                       # só mostra o progresso, sem baixar
```

- `--resume` continua o último intervalo salvo. As páginas concluídas são puladas e as páginas
  interrompidas ou com falha são feitas primeiro, antes das páginas ainda não tentadas.
- Interrupções com Ctrl+C, queda de energia, travamento ou CAPTCHA são seguras. As páginas já
  concluídas continuam salvas com os seus registros; a página em andamento não deixa nada pela
  metade e é refeita na próxima execução.
- Depois de **Ctrl+C**, de um CAPTCHA não resolvido, de um acesso negado (401/403) ou de um
  limite 429 persistente, a página em andamento volta para "pendente" sem contar a tentativa.
- Depois de uma **queda de energia, travamento ou encerramento forçado**, a página fica marcada
  como `processing`. Na próxima execução ela volta para "pendente" (aparece
  `Recovered interrupted pages: 1`) e, nesse caso, a tentativa **é contada**.
- Páginas que falharam são tentadas de novo primeiro, até 3 tentativas cada
  (configurável com `--max-attempts`). Depois disso elas são puladas e listadas no resumo
  (seção 5A).

## 5A. Identificar e repetir páginas com falha

Uma página falha quando não carrega depois das novas tentativas automáticas (`--retries`),
quando a página recebida está incompleta ou é inesperada, ou quando uma linha tem um problema
diferente de "não verificado" (por exemplo, um selo "Проверено" que não é verde). Uma página com
falha nunca grava registros.

**1) Ver o resumo, sem acessar o site:**

```bash
python main.py --resume
```

```text
Run range: pages 350,000..350,100 (101 pages)
Recovered interrupted pages: 0
Progress in range: {'pending': 95, 'processing': 0, 'completed': 4, 'failed': 2}
Pages to process: 96 (next: [350002, 350007, 350005, 350006, ...])
Skipped after 3 failed attempts: 1 [350003]
Verified records stored: 10
Planning only: add --live to fetch pages; no pages were fetched.
```

- `failed` é o número de páginas com falha no intervalo.
- Em `Pages to process`, as páginas com falha ou interrompidas aparecem primeiro.
- `Skipped after 3 failed attempts` lista as páginas que já usaram as 3 tentativas.

**2) Ver o motivo de cada falha** (abre o banco somente para leitura; o mesmo comando funciona no
Linux e no PowerShell):

```bash
python -c "import sqlite3; c = sqlite3.connect('file:data/honestpool.db?mode=ro', uri=True); [print(r) for r in c.execute('SELECT page_number, status, attempts, last_error FROM page_progress WHERE attempts > 0 AND status <> ? ORDER BY page_number', ['completed'])]"
```

Cada linha mostra a página, o status, as tentativas usadas e a última mensagem de erro, por
exemplo `(350003, 'failed', 3, 'TransientFetchError: navigation timeout after 60.0s: Timeout 60000ms
exceeded. (gave up after 2 retries)')`. Se usar outro banco com `--db`, troque o caminho
`data/honestpool.db` no comando.
`python demo.py` também mostra a tabela de todas as páginas, útil para bancos pequenos.

**3) Repetir:**

```bash
python main.py --resume --live --max-pages 5                   # repete primeiro as páginas com falha
python main.py --resume --live --max-pages 5 --max-attempts 4  # permite mais 1 tentativa às páginas puladas
```

Antes de aumentar `--max-attempts`, leia o motivo da falha. Falhas repetidas na mesma página
costumam indicar lentidão do servidor (tente `--nav-timeout 90`) ou uma mudança no site, e não
devem ser repetidas indefinidamente.

## 6. Como o progresso é salvo

- Banco: `data/honestpool.db` (SQLite).
- Cada página passa por `pending` → `processing` → `completed` ou `failed`.
- Os registros de uma página e a marcação "concluída" são gravados juntos, em uma única
  transação. Nunca existe uma página marcada como concluída sem os seus registros, nem o contrário.
- Registros existentes nunca são apagados nem sobrescritos.
- Só uma instância do scraper pode gravar no banco por vez. Uma segunda execução simultânea
  é recusada.
- Antes de cada página aparece um relatório de progresso:

```
Progress | current page: 350,003 | range 350,000..350,100 (101 pages)
  pages: 3 completed, 98 pending, 0 failed | verified records stored: 10 | last successful page: 350,002
  session runtime: 0:01:06 | average per completed page: 22.0 s (3 measured, CAPTCHA waits excluded)
  estimated remaining (approximate, not guaranteed; excludes CAPTCHA waits and server slowdowns): this run 0:06:14 for 17 page(s); whole range 0:35:56 for 98 page(s)
  estimate basis: 22.0 s per page; retries, slow server responses and CAPTCHA waits can make the real time longer
```

  A estimativa de tempo é aproximada e não é garantida. Ela usa somente as páginas concluídas
  na sessão atual e não inclui o tempo de CAPTCHA nem possíveis lentidões do servidor. A
  primeira página de cada sessão não tem pausa antes dela; por isso ela é contada com pelo
  menos a pausa média configurada (`--delay` + metade de `--jitter`). Antes da primeira página
  concluída a estimativa aparece como "unknown".
- No final, o resumo separa as páginas planejadas, tentadas, concluídas, com falha,
  interrompidas e não tentadas, por exemplo:

```
Live run: 2 completed, 0 failed, 2 new verified records (attempted 3 of 4 planned pages; 1 stopped before completion and returned to pending; 1 not attempted; 5 unfinished pages in range at start)
Page requests sent: 3 (retries, CAPTCHA answer submissions and reloads after a CAPTCHA are extra requests within the same page attempt; the database counts one attempt per page)
```

## 7. Exportar CSV e Excel

```bash
python main.py --export csv                                  # data/verified_records.csv
python main.py --export xlsx                                 # data/verified_records.xlsx
python main.py --export xlsx --output relatorio/verificados.xlsx
```

- As exportações leem o banco e não acessam o site. Elas podem ser feitas a qualquer momento.
- **CSV automático:** durante uma execução com `--live`, depois de cada página concluída a tela
  mostra os registros verificados novos dessa página (`Number`, `Start hex`, `End hex`,
  `Status`) e o arquivo `verified_records.csv`, na pasta do banco (`data/` por padrão), é
  regravado a partir do banco por substituição atômica (`CSV updated: ...`). O banco SQLite
  continua sendo a fonte principal; o CSV tem sempre todos os registros verificados salvos, sem
  repetições, com o mesmo conteúdo de `--export csv`. Uma interrupção deixa o último CSV completo,
  e a próxima execução o sincroniza com o banco.
- **Excel (XLSX):** só é gerado sob demanda, com `--export xlsx` (opção 4 do menu do Windows).
- O CSV usa UTF-8 com BOM, para que o Excel mostre "Проверено" corretamente.
- O Excel tem cabeçalho fixo, filtro automático e colunas hexadecimais em formato de texto.
- Os registros são ordenados por `Number`.
- Se houver registros importados (seção 7A), eles entram na exportação com as mesmas colunas.
  Use `--export-source website` para exportar só os do site, ou `--export-source imported`
  para exportar só os importados.

## 7A. Fluxo B — importar números verificados de uma fonte autorizada (opcional)

Se uma **fonte confiável e autorizada** (por exemplo, o operador do site) fornecer uma lista de
números de segmentos verificados, ela pode ser importada sem acessar o site:

```bash
python main.py --import-verified verified_numbers.txt
python main.py --import-verified verified_numbers.csv --source "lista enviada pelo operador, 10/2026"
python main.py --import-verified verified_numbers.csv --source "..." --export xlsx
python main.py --export csv --export-source imported      # exporta só os registros importados
```

Sem `--source`, o programa pergunta na tela qual é a fonte autorizada. Exemplo de saída:

```
Import file: verified_numbers.txt (txt, SHA-256 f766...0e3f)
Segment numbers: 6 valid, 1 duplicate(s) removed, 5 unique
Imported 5 new verified record(s) from 'lista enviada pelo operador, 10/2026' (import batch 1); skipped 0 already stored from the website and 0 already imported
Website page progress was not changed; imported records are stored separately from scraped records and are included in --export.
Verified records stored: 10 from the website, 5 imported
```

Para ver o fluxo completo com dados sintéticos, sem tocar no banco real, use
`python demo_import.py` (seção 12).

**Formatos aceitos** (texto UTF-8, com ou sem BOM):

| Formato | Conteúdo | Exemplo |
|---|---|---|
| `.txt` | Um número de segmento por linha; linhas vazias são ignoradas | `150` |
| `.csv` | Linha de cabeçalho com uma coluna chamada exatamente `Number` | `Number` / `150` |

Outras colunas no CSV são permitidas. Se o CSV tiver as colunas `Start hex`, `End hex` ou
`Status` (como o arquivo gerado por `--export csv`), os valores precisam bater com o intervalo
calculado e com `Проверено`.

**Validação:**

- Só são aceitos números inteiros decimais de 1 a 67.108.864. Sinais (`+5`, `-1`), decimais
  (`1.0`), separadores de milhar (`1,000`), hexadecimal (`0x10`) e valores vazios são recusados.
- CSV malformado (aspas sem fechamento, coluna `Number` ausente ou repetida, linhas com número
  de campos diferente do cabeçalho), arquivos que não são UTF-8, arquivos binários e arquivos
  vazios são recusados.
- Se **qualquer** valor for inválido, o arquivo inteiro é recusado e nada é gravado. A
  mensagem mostra o número das linhas com problema.
- Números repetidos no arquivo são contados uma única vez.
- Para cada número N são calculados `início = 0x400000000000000000 + (N − 1) × 2^44` e
  `fim = início + 2^44`, com 18 dígitos hexadecimais minúsculos, e o Status `Проверено`.

**Fonte autorizada:** o programa não consegue verificar de onde o arquivo veio. Como os números
importados são gravados como verificados, a pessoa que importa precisa informar a fonte
autorizada com `--source` ou responder à pergunta exibida na tela. Sem essa informação (ou em
um terminal não interativo) a importação é recusada. Nunca importe números estimados,
adivinhados ou não verificados.

**O que acontece no banco:**

- Os registros importados ficam em tabelas próprias, separadas dos dados extraídos do site.
  Cada importação registra a fonte informada, o nome do arquivo, o SHA-256, o formato, as
  contagens e a data.
- Registros já existentes nunca são alterados: números já salvos (do site ou de uma importação
  anterior) são ignorados.
- O progresso das páginas do site não muda: nenhuma página é marcada como concluída por causa
  de uma importação. Se o número aparecer depois no site, ele é exportado uma única vez.
- O código de saída 4 indica que a importação foi recusada.

A importação **não** resolve a exigência de CAPTCHA e não substitui o acesso autorizado para a
extração pelo site.

## 8. Configurar o intervalo entre requisições

| Opção | Padrão | Significado |
|---|---|---|
| `--delay S` | 15 | Segundos entre o início de uma página e o da seguinte (mínimo 5) |
| `--jitter S` | 5 | Espera extra aleatória de 0 a S segundos |
| `--nav-timeout S` | 30 | Tempo máximo de carregamento de cada página, de 5 a 300 (recomendado: 60) |
| `--retries R` | 2 | Novas tentativas automáticas após timeout, erro de conexão, 429 ou 5xx (0 a 5) |
| `--captcha-wait S` | 600 | Tempo para resolver o CAPTCHA manualmente |
| `--max-pages P` | 1 | Máximo de páginas por execução |
| `--max-attempts K` | 3 | Tentativas por página antes de pulá-la |
| `--headless` | desligado | Sem janela; nesse modo um CAPTCHA encerra a execução, a menos que o 2Captcha o resolva |

Exemplos:

```bash
# Mais conservador (cerca de 2 páginas por minuto, no máximo)
python main.py --resume --live --max-pages 20 --delay 30 --jitter 10 --nav-timeout 60

# Servidor lento: mais tempo por página e no máximo uma nova tentativa
python main.py --resume --live --max-pages 10 --nav-timeout 90 --retries 1
```

Recomendamos não reduzir os intervalos. Os limites do site não são publicados. A API oficial,
por exemplo, permite 60 requisições por hora e bloqueia o IP por 24 horas quando o limite é
excedido repetidamente.

## 9. Quando aparece um CAPTCHA

### Com o 2Captcha configurado (seção 9A)

1. O scraper mostra `CAPTCHA shown. Sending the CAPTCHA image to the CAPTCHA service (attempt 1 of 3).`
2. Apenas a imagem do CAPTCHA é capturada da página aberta (sem nova requisição ao site) e
   enviada ao 2Captcha. Nenhum endereço de página, cookie ou outro dado da sessão é enviado.
3. O scraper consulta o resultado a cada 5 segundos, por até `CAPTCHA_TIMEOUT` segundos.
4. A resposta é digitada no campo do CAPTCHA e o botão "Отправить" é clicado, respeitando o
   mesmo intervalo mínimo entre requisições usado para as páginas (`--delay`).
5. A verificação só é aceita quando o site redireciona o navegador para a listagem do puzzle.
   Então aparece `Verification succeeded automatically`, a página solicitada é carregada de novo
   na mesma sessão do navegador e a extração continua. Essa página só é salva depois de passar
   pelas mesmas conferências de qualquer outra página.
6. A resposta só conta como recusada quando o site mostra um CAPTCHA com uma imagem **diferente**
   (um novo desafio). Então o 2Captcha é avisado (`reportIncorrect`) e a nova imagem é enviada,
   até 3 tentativas por CAPTCHA. Uma resposta aceita é confirmada (`reportCorrect`). Qualquer
   outro resultado (a mesma imagem continua na tela, a página não muda, a imagem não pode ser
   comparada) fica como **incerto**: nada é informado ao 2Captcha e o CAPTCHA segue para o
   passo 7. O log mostra cada etapa como `CAPTCHA N attempt M/3: ...` (imagem capturada ou não
   encontrada, tarefa enviada ao 2Captcha, resposta recebida, aceitação ou recusa confirmada pelo
   site, resultado incerto, verificação manual); a resposta em si nunca aparece.
7. Se a resolução automática falhar (respostas recusadas, tempo esgotado, erro da API, saldo
   insuficiente, chave recusada ou imagem não encontrada):
   - com janela, o scraper passa para a verificação manual descrita abaixo;
   - no modo `--headless`, a execução para com segurança.

   Saldo insuficiente, chave recusada, IP não permitido ou conta suspensa desligam a resolução
   automática até o fim da execução.

### Sem o 2Captcha, ou quando a resolução automática falha

1. O scraper mostra a mensagem `CAPTCHA shown. Please complete it manually in the browser window`.
2. Uma pessoa resolve o CAPTCHA na janela do Chromium.
3. O scraper espera até a listagem correta aparecer, por até 10 minutos (`--captcha-wait`).
   Se a resposta estiver errada e o CAPTCHA voltar, ele continua esperando.
4. Depois da verificação, a mesma sessão do navegador continua.

### Quando a execução para

A execução para com segurança, a página atual volta para "pendente" (sem contar como
tentativa) e os dados salvos são mantidos nestes casos:

- um CAPTCHA logo depois de uma verificação bem-sucedida, para a mesma página;
- um CAPTCHA que não foi resolvido automaticamente (ou que aparece depois das 3 verificações
  automáticas da sessão) quando não há verificação manual possível: no modo `--headless` ou
  quando a verificação manual da sessão já foi usada;
- acesso negado (HTTP 401/403);
- limite de requisições persistente (HTTP 429).

Para continuar, use `python main.py --resume --live`.

## 9A. Resolução automática do CAPTCHA com o 2Captcha

Com `CAPTCHA_PROVIDER=2captcha` e uma chave em `CAPTCHA_API_KEY`, o CAPTCHA de imagem do site é
resolvido pela API oficial do 2Captcha (tipo "Normal captcha", `ImageToTextTask`). Sem a chave,
ou com `CAPTCHA_PROVIDER` vazio ou `manual`, nada é enviado ao 2Captcha e o CAPTCHA é resolvido
manualmente (seção 9).

### Variáveis de ambiente

| Variável | Valores | Padrão | Significado |
|---|---|---|---|
| `CAPTCHA_PROVIDER` | vazio, `manual` ou `2captcha` | `manual` | Quem resolve o CAPTCHA |
| `CAPTCHA_API_KEY` | chave do painel do 2Captcha | vazio | Guardada apenas na memória; nunca é exibida |
| `CAPTCHA_TIMEOUT` | 10 a 600 (segundos) | 120 | Tempo máximo de espera pela resposta de cada tentativa |

As variáveis são lidas do ambiente do terminal. O programa **não** abre arquivos `.env` sozinho.
Assim que o cliente 2Captcha recebe a chave, o `main.py` a retira do próprio ambiente, antes de
abrir o navegador: nem o driver do Playwright nem o Chromium recebem a chave.

**No Windows 11 — `configurar_2captcha.bat` (recomendado).** Dê dois cliques no arquivo e cole a
chave; ela não aparece na tela. A chave é cifrada com a proteção de dados do Windows (DPAPI) em
`config\2captcha.cfg`, que só o mesmo usuário do Windows, no mesmo computador, consegue decifrar.
O arquivo também define `CAPTCHA_PROVIDER=2captcha` e `CAPTCHA_TIMEOUT=120`. O menu
`iniciar_honestpool.bat` passa essas variáveis ao programa automaticamente, só nas extrações e na
verificação da configuração (não nas exportações nem no progresso), e substitui a chave por
`[CHAVE OCULTA]` se ela aparecer em algum texto. No Windows **não** é preciso criar `.env`.
Detalhes em `INSTALACAO_WINDOWS_11.md`, seção 5.

**No Linux, ou para uso manual,** há duas formas seguras de definir as variáveis; a chave fica
apenas na memória do terminal em que ela foi carregada.

**Opção 1 — arquivo `.env` local (Linux).** O pacote inclui o modelo
`.env.example`, que contém apenas valores de exemplo e nenhuma chave.

1. Copie `.env.example` para `.env`, na mesma pasta do `main.py`.
2. Abra `.env` em um editor de texto e cole a sua chave depois de `CAPTCHA_API_KEY=`, sem aspas
   e sem espaços. Não altere as outras linhas, a menos que queira outro tempo de espera.
3. Proteja o arquivo: `chmod 600 .env`.
4. Carregue o arquivo no terminal **antes** de cada sessão de trabalho:

```bash
set -a; . ./.env; set +a
python main.py --captcha-status
```

Confira sempre o resultado com `--captcha-status`: ele deve mostrar `API key: set (hidden)`.

O arquivo `.env` já está no `.gitignore`. Nunca o envie por mensagem, nunca o coloque em um
pacote de entrega e não o abra durante uma reunião com compartilhamento de tela.

**Opção 2 — digitar a chave no terminal, sem arquivo (Linux).** A chave não aparece na tela nem
fica no histórico:

```bash
export CAPTCHA_PROVIDER=2captcha
read -rsp "Chave do 2Captcha: " CAPTCHA_API_KEY && export CAPTCHA_API_KEY && echo
export CAPTCHA_TIMEOUT=120
python main.py --captcha-status
```

`read -s` evita que a chave apareça na tela e no histórico do terminal. As variáveis valem apenas
para aquele terminal. No Windows, use `configurar_2captcha.bat` em vez destas opções.

### Conferir a configuração

`python main.py --captcha-status` confere as três variáveis e encerra, sem acessar o site, o
2Captcha ou o banco de dados. Exemplo:

```text
CAPTCHA provider: 2captcha | API key: set (hidden) | timeout: 120 s | automatic solving: enabled (only the CAPTCHA image is sent to the provider; manual verification in the browser window remains the fallback)
```

A mesma linha aparece no início de cada execução com `--live`. O saldo e a chave só são
verificados pelo 2Captcha quando o primeiro CAPTCHA aparece.

### Custos e ritmo das requisições

- **Custo:** o 2Captcha cobra por imagem enviada (cada tentativa), conforme a tabela de preços
  atual do tipo "Normal captcha" no site do 2Captcha. O custo **não** depende do número de
  páginas, e sim do número de CAPTCHAs. Imagens que o 2Captcha não consegue resolver
  (`ERROR_CAPTCHA_UNSOLVABLE`) são devolvidas ao saldo.
- **Limite por execução:** no máximo 3 imagens por CAPTCHA. A resolução automática deixa de ser
  tentada depois de 3 verificações automáticas confirmadas na execução, mas os CAPTCHAs em que ela
  falha não entram nessa conta: eles passam para a verificação manual (no máximo 10 por execução; a
  execução para no 11º). Por isso, com as configurações padrão, uma execução de `main.py` envia
  **no máximo 42 imagens** ao 2Captcha (3 × (3 + 10 + 1)), um limite fixo aplicado pelo programa.
  No modo `--headless`, na prática no máximo 12, porque a execução para no primeiro CAPTCHA que
  não for resolvido automaticamente.
- **Resumo no fim da execução:** a linha `CAPTCHA summary:` mostra quantos CAPTCHAs apareceram,
  quantas verificações automáticas foram confirmadas, quantas imagens foram enviadas ao 2Captcha,
  quantas respostas o site recusou, quantos resultados ficaram incertos (não informados ao
  2Captcha) e quantas vezes houve verificação manual.
- **Como reduzir o custo:** cada execução abre uma nova sessão do navegador, e até agora cada
  nova sessão tem exigido um CAPTCHA. Poucas execuções com `--max-pages` maior custam menos do que
  muitas execuções de 1 página.
- **Custo real ainda não medido:** a taxa de acerto do 2Captcha para este CAPTCHA, e portanto o
  custo médio por CAPTCHA aceito, ainda não foi medida. Acompanhe o saldo no painel do 2Captcha
  nas primeiras execuções.
- **Ritmo:** cada página e cada resposta enviada ao site respeitam o mesmo intervalo mínimo
  (`--delay`, padrão 15 s, mais 0 a `--jitter` segundos, padrão 5). Com os valores padrão isso
  dá, em média, no máximo cerca de 205 requisições por hora (17,5 s entre requisições), e
  menos quando o servidor é lento. Cada resposta enviada conta como uma
  requisição em `Page requests sent`. As requisições ao 2Captcha não acessam o site.

### Comportamento em caso de problema

- **Chave ausente:** aparece o aviso `CAPTCHA_API_KEY is not set; manual CAPTCHA verification
  is used` e o scraper continua com o fluxo manual.
- **Chave sem provedor:** se `CAPTCHA_API_KEY` estiver definida e `CAPTCHA_PROVIDER` vazio ou
  `manual`, a chave é ignorada e descartada, com um aviso.
- **Configuração inválida** (provedor desconhecido, tempo fora de 10–600 s, não numérico ou
  chave com espaços ou caracteres inválidos): `--live` e `--captcha-status` param com código
  de saída 2 **antes** de abrir o navegador ou o banco. A mensagem nunca mostra a chave.
- **Saldo insuficiente** (`ERROR_ZERO_BALANCE`), **chave recusada**, **IP não permitido** ou
  **conta suspensa:** a resolução automática fica desligada até o fim da execução; com janela,
  a verificação passa a ser manual; no modo `--headless`, a execução para.
- **Resposta recusada, tempo esgotado ou erro temporário da API:** nova tentativa, até 3.
- Comandos que não acessam o site (planejar sem `--live`, `--export`, `--import-verified`)
  ignoram essas variáveis.

### Segurança da chave e dos dados

- A chave é enviada somente para `https://api.2captcha.com`, dentro do corpo das requisições.
  Ela nunca é gravada no código, no banco, nos logs, nas exportações ou no pacote de entrega,
  e nunca é exibida (aparece apenas como `set (hidden)`).
- As respostas do CAPTCHA também não aparecem nos logs nem nas mensagens.
- Ao 2Captcha são enviados apenas a chave e a imagem do CAPTCHA.
- Não coloque a chave em arquivos versionados nem a envie por mensagem. Os arquivos `.env` e a
  pasta `config` (chave cifrada do Windows) já estão no `.gitignore` e nunca entram no pacote de
  entrega.
- Se a chave for exposta, gere uma nova no painel do 2Captcha.

### Autorização

O cliente confirmou que usa o 2Captcha com o HonestPool e que tem autorização para isso.
Recomendamos guardar essa autorização por escrito. Os termos do 2Captcha exigem uso apenas
para fins autorizados e legais, sob a responsabilidade do cliente.

## 9B. Os limites de 3 tentativas e as limitações da recuperação

Existem três limites diferentes, todos com o valor 3:

| Limite | Onde vale | O que acontece ao atingir | Como mudar |
|---|---|---|---|
| 3 tentativas por **página** | Entre execuções, gravado no banco | A página é pulada e listada em `Skipped after 3 failed attempts` | `--max-attempts K` (seção 5A) |
| 3 tentativas por **CAPTCHA** | Respostas do 2Captcha para um mesmo CAPTCHA | Passa para a verificação manual; no `--headless`, a execução para | Fixo nesta versão |
| 3 verificações automáticas por **sessão** | CAPTCHAs resolvidos pelo 2Captcha em uma execução | O próximo CAPTCHA vai para a verificação manual (uma por sessão); sem ela, a execução para | Fixo nesta versão |

Além disso, `--retries` (padrão 2) repete automaticamente o carregamento de uma página após um
timeout, erro de conexão, 429 ou 5xx, **dentro** da mesma tentativa da página.

O que conta como tentativa de página:

- Conta: falha de carregamento depois das novas tentativas, página incompleta ou inesperada, e
  encerramento forçado ou queda de energia durante a página.
- Não conta: Ctrl+C, CAPTCHA não resolvido, acesso negado (401/403) e limite 429 persistente.
  Nesses casos a execução para e a página volta para "pendente".

Limitações da recuperação:

- A recuperação protege o que já foi salvo, mas não continua sozinha. Depois de qualquer parada
  alguém precisa executar `python main.py --resume --live` de novo.
- A página que estava em andamento é baixada outra vez desde o início (nada dela fica salvo pela
  metade).
- Um encerramento forçado conta como tentativa. Se a mesma página for interrompida 3 vezes, ela é
  pulada até alguém aumentar `--max-attempts`.
- `--resume` continua apenas o **último** intervalo salvo. Escolher um novo intervalo com
  `--start-page`/`--end-page` substitui o intervalo salvo (as páginas e os registros anteriores
  continuam no banco).
- A recuperação depende do arquivo do banco estar intacto. O scraper não protege contra disco
  cheio, arquivo apagado ou corrompido. Faça cópias de segurança com o scraper **parado**,
  copiando `data/honestpool.db` junto com os arquivos `honestpool.db-wal` e `honestpool.db-shm`,
  se existirem, ou exporte para CSV/Excel com frequência.
- Só uma execução pode usar o banco por vez; uma segunda é recusada (código de saída 1). No
  Windows, logo depois de um travamento ou de fechar a janela pelo X, o sistema pode levar alguns
  segundos para liberar o banco; se aparecer `another process is writing`, aguarde e tente de novo.
- A recuperação foi validada com interrupções simuladas (encerramento forçado durante o
  carregamento e durante a gravação de uma página, e Ctrl+C), não com uma queda real durante uma
  execução ao vivo. Essas simulações rodaram no Linux; no Windows elas ainda não foram executadas.

## 10. O que é automático e o que exige uma pessoa

| Automático | Exige intervenção manual |
|---|---|
| Navegação página a página com intervalos configurados | Resolver o CAPTCHA quando o 2Captcha não está configurado ou falha |
| Resolução do CAPTCHA de imagem pelo 2Captcha, quando configurado (seção 9A) | Manter saldo no 2Captcha e acompanhar as execuções |
| Validação da página e extração dos segmentos verificados | Reiniciar a execução após um CAPTCHA repetido, um bloqueio ou um limite |
| Gravação segura e retomada após interrupções | Escolher o próximo intervalo de páginas |
| Novas tentativas limitadas em erros temporários | Decidir o ritmo, respeitando os limites do site |
| Relatório de progresso e exportação CSV/Excel | Obter do operador do site uma autorização para acesso automatizado |
| Fluxo B: validação do arquivo, cálculo dos hexadecimais e registro da origem | Fluxo B: obter o arquivo de uma fonte autorizada e declarar essa fonte |

A operação sem supervisão não é prometida nem suportada nesta versão.

## 11. Por que extrair as 671.089 páginas é inviável hoje

As estimativas usam os tempos medidos nos testes. Elas supõem execução contínua, 24 horas por
dia, **sem** CAPTCHA, bloqueios ou limites. Na prática, a duração seria maior.

| Cenário | Por página | 1.000 páginas | 100.000 páginas | 671.089 páginas |
|---|---|---|---|---|
| Melhor caso (limitado pelo intervalo configurado) | 17,5 s | 4,9 h | 20,3 dias | 136 dias |
| Típico (tempo medido da página 2) | 24,4 s | 6,8 h | 28,2 dias | 190 dias |
| Lento (um timeout e uma nova tentativa por página) | ~100 s | 27,9 h | 116 dias | 780 dias |
| Se o site limitar a 60 páginas por hora | 60 s | 16,7 h | 69 dias | 466 dias |

Além do tempo:

- Cada nova sessão do navegador tem exigido um CAPTCHA. Sem o 2Captcha, alguém precisa estar
  presente; com ele, cada CAPTCHA tem custo, e a taxa de acerto e a frequência dos CAPTCHAs em
  execuções longas ainda não foram medidas.
- O ritmo do melhor caso (cerca de 206 páginas por hora) é mais de 3 vezes o limite publicado
  para a API oficial. O site pode aplicar limites parecidos ou bloquear o acesso.
- O servidor demorou cerca de 24 s para gerar uma única página.
- Apenas cerca de 1 em cada 2.900 segmentos está verificado (23.398 de 67.108.864, segundo o
  site). Quase todas as páginas baixadas não trazem nenhum registro.

**Caminho recomendado:** pedir ao operador do site uma exportação autorizada dos números dos
segmentos verificados (ver `docs/OPERATOR_REQUEST.md`). Os valores hexadecimais completos podem
ser calculados a partir do número do segmento, então essa lista sozinha já seria suficiente e
evitaria baixar centenas de milhares de páginas. O Fluxo B (seção 7A) já está pronto para
importar essa lista assim que ela for fornecida.

## 12. Demonstração offline

```bash
python demo.py
```

Mostra, sem acessar o site:

- o status das páginas e os registros guardados no SQLite (aberto somente para leitura);
- a conferência das exportações CSV e Excel com o banco, incluindo os hexadecimais como texto;
- exemplos de comandos;
- um exemplo do relatório de progresso, simulado em uma cópia temporária do banco.

O pacote de entrega não inclui o banco de dados. Nele, a demonstração usa os arquivos de
exemplo em `examples/` (os 10 registros verificados extraídos nos testes):

```bash
python demo.py --csv examples/verified_records.csv --xlsx examples/verified_records.xlsx
```

O pacote de teste do Windows 11 não traz esses dois arquivos, porque contêm registros reais.
Nele, use `python demo.py` depois do teste de extração supervisionado.

### Demonstração do Fluxo B (importação)

```bash
python demo_import.py
```

Executa os comandos reais do `main.py` em um banco temporário, com os arquivos **sintéticos**
de `examples/import/` (números escolhidos apenas para teste, que não são segmentos
verificados). A demonstração importa um TXT e um CSV com a fonte
"synthetic demonstration data" e confere: o cálculo dos hexadecimais, o Status `Проверено`, a
remoção de números repetidos, a recusa do arquivo inteiro quando há um número inválido, o
registro da origem, o progresso das páginas sem alteração e as exportações CSV/Excel
(`--export-source imported` e `all`). Cada verificação aparece como `PASS` ou `FAIL`. O banco
real nunca é aberto.

## 13. Limitações

- **CAPTCHA:** no Fluxo A, o CAPTCHA é resolvido pelo 2Captcha (seção 9A) ou, sem ele ou
  quando ele falha, por uma pessoa. O Fluxo B não envolve CAPTCHA.
- **2Captcha não verificado de forma independente:** a integração foi validada offline. As
  capturas de tela do cliente mostram um CAPTCHA real resolvido automaticamente, mas o banco
  dessa execução não foi inspecionado. Ainda não foram confirmados: a página mostrada depois de
  uma resposta recusada, a taxa de acerto do 2Captcha para este CAPTCHA e o custo real
  (seção 14).
- **Sem operação desacompanhada:** a extração pelo site não é prometida sem supervisão.
- **Extração incompleta:** no banco que inspecionamos foram extraídas apenas as páginas 1 a 3
  (10 registros). O banco da execução feita pelo cliente não foi inspecionado. As
  671.089 páginas levariam de 136 a 780 dias de execução contínua, sem contar CAPTCHAs e
  bloqueios (seção 11).
- **Limites do site desconhecidos:** o ritmo permitido não é publicado; pode haver bloqueios.
- **API oficial:** não fornece a lista de segmentos verificados.
- **Fluxo B depende de um arquivo autorizado:** o programa não verifica a origem do arquivo;
  a responsabilidade é de quem declara a fonte. Ainda não existe uma lista autorizada.
- **Desfazer uma importação:** não há comando para isso; seria necessário editar o banco
  manualmente.
- **Formatos de importação:** apenas TXT e CSV (não XLSX).

## 14. O que foi validado, o que foi demonstrado e o que ainda falta confirmar

### Validado por testes automáticos offline

Os testes do pacote rodam sem internet, com páginas e respostas do 2Captcha simuladas
(`python -m pytest -q`). No Linux (Ubuntu): 765 aprovados e 9 pulados, porque esses 9 só
funcionam no Windows (`cmd.exe`, trava `msvcrt`, DPAPI e o "job object" do Windows). No Windows,
os mesmos testes rodam esses 9 e pulam 5 verificações exclusivas do Linux (duas delas abrem um
Chromium real, sem janela, com uma página local); eles ainda não foram executados no Windows.
Eles cobrem:

- extração apenas das linhas com o selo verde "Проверено" exato, incluindo linhas parecidas
  ("проверено", "Проверено частично", "Не проверено" com selo verde) que nunca são salvas;
- conferência dos hexadecimais pela fórmula e recusa de páginas incompletas ou inesperadas;
- gravação de cada página junto com os seus registros em uma única transação;
- um teste de ponta a ponta com 10 páginas: interrupção na página 6 (encerramento forçado durante
  o carregamento, encerramento forçado durante a gravação e Ctrl+C), retomada com `--resume` sem
  repetir páginas concluídas, recuperação da página interrompida, um CAPTCHA resolvido pela API
  simulada do 2Captcha e exportação para CSV/Excel conferida com o banco;
- novas tentativas, limites de tentativas e paradas seguras em 401/403/429;
- o cliente do 2Captcha (envio da imagem, consulta do resultado, erros de saldo e de chave,
  tempo esgotado) e o preenchimento do formulário do CAPTCHA;
- exportação CSV/Excel com hexadecimais completos como texto, e a importação do Fluxo B;
- ausência da chave e das respostas do CAPTCHA nos logs e mensagens;
- a trava de gravação entre processos, inclusive a liberação depois de um processo encerrado à
  força;
- a lógica do menu do Windows (armazenamento da chave, ocultação, comandos, Ctrl+C) e a estrutura
  dos arquivos `.bat`.

### Requer o primeiro teste no Windows 11

- A instalação, a configuração da chave com o DPAPI, a trava do banco, o Ctrl+C pelo menu e os
  caracteres russos na janela, conforme `CHECKLIST_ACEITACAO_WINDOWS_11.md`.

### Verificado em execuções ao vivo supervisionadas (antes do 2Captcha)

- As páginas 1 a 3 foram baixadas do site real com verificação manual do CAPTCHA e geraram 10
  registros verificados, conferidos com a fórmula e exportados para CSV/Excel.
- A parada segura quando o CAPTCHA aparece de novo na mesma sessão foi confirmada ao vivo.
- Os números 9 e 101 foram confirmados de forma independente com os dados do cliente.

### Demonstrado nas capturas de tela do cliente

- Um CAPTCHA real do HonestPool resolvido automaticamente pelo 2Captcha, com o scraper
  continuando nas páginas 7020 e 7021.
- Nós não acompanhamos essa execução e o banco de dados dela não foi inspecionado. As capturas
  de tela não comprovam que os registros dessas páginas foram salvos.

### Ainda não verificado de forma independente com o site real

- Que os registros das execuções com o 2Captcha foram gravados corretamente (precisa do banco do
  cliente, copiado com o scraper parado, sem o arquivo `.env`).
- A página que o site mostra depois de uma resposta recusada.
- A taxa de acerto do 2Captcha para este CAPTCHA e o custo real por CAPTCHA aceito.
- Com que frequência o CAPTCHA aparece em execuções longas e quanto dura uma sessão verificada.
- Os limites de requisições do site e o comportamento em execuções de centenas de páginas.
- A recuperação depois de uma queda real durante uma execução ao vivo e o modo `--headless` ao
  vivo.
- A operação sem supervisão, que não é suportada.
