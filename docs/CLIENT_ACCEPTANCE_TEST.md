# Teste de aceitação do cliente — HonestPool Scraper v3 (linha de comando)

> **Windows 11:** não use este roteiro. Siga `INSTALACAO_WINDOWS_11.md` (dois cliques em
> `setup_windows.bat`, `configurar_2captcha.bat` e `iniciar_honestpool.bat`) e preencha
> `CHECKLIST_ACEITACAO_WINDOWS_11.md`, que cobre a instalação, o 2Captcha, o salvamento do
> progresso, a retomada e as exportações pelo menu, sem arquivo `.env` e sem digitar comandos.
> Este roteiro é a versão por linha de comando, para Linux ou para uso avançado.

Este roteiro confirma, no seu computador e com o site real, o que os testes offline não
conseguem confirmar: a resolução automática do CAPTCHA, a gravação das páginas no SQLite, a
parada segura, a retomada sem repetir páginas e as exportações.

- **Pacote:** `honestpool-scraper-v3-20261013.zip`. Guia completo: `docs/CLIENT_GUIDE.md`.
- **Duração:** de 30 a 45 minutos, com uma pessoa acompanhando o tempo todo.
- **Alcance:** apenas as páginas 1 a 5, em um banco de teste separado
  (`data/acceptance_test.db`). O seu banco de trabalho não é usado.
- **Custo:** em geral um CAPTCHA por execução com `--live`. O roteiro tem 3 execuções com acesso
  ao site. O programa envia no máximo 42 imagens ao 2Captcha por execução (126 no total), mas
  normalmente são 3 ou menos no roteiro inteiro. A linha `CAPTCHA summary:` no fim de cada
  execução mostra quantas imagens foram enviadas.
- **Não** use `--headless` neste teste: com a janela visível, um CAPTCHA que o 2Captcha não
  resolver ainda pode ser resolvido por você.
- **Não** provoque CAPTCHAs (por exemplo, recarregando páginas no navegador). O objetivo é
  observar o comportamento quando o CAPTCHA aparece naturalmente.

## 1. Instalar

Extraia o ZIP e abra um terminal na pasta `honestpool-scraper-v3` (Linux).

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m playwright install chromium
python -m pytest -q
```

**Esperado:** a última linha mostra `765 passed, 9 skipped`. Os 9 testes pulados só rodam no
Windows. Os testes não acessam a internet.

Em todas as próximas sessões, ative o ambiente antes dos comandos (`source .venv/bin/activate`).

## 2. Configurar a chave do 2Captcha com segurança

No Windows, a chave é configurada com `configurar_2captcha.bat` (veja o aviso no início); os
passos abaixo são para o Linux.

1. Copie `.env.example` para `.env`, na mesma pasta.
2. Abra `.env` em um editor de texto e cole a sua chave depois de `CAPTCHA_API_KEY=`, sem aspas
   e sem espaços. Salve. Proteja o arquivo: `chmod 600 .env`.
3. Carregue o arquivo no terminal (repita sempre que abrir um terminal novo):

```bash
set -a; . ./.env; set +a
```

4. Confira, sem acessar nenhum serviço:

```bash
python main.py --captcha-status
```

**Esperado:**

```text
CAPTCHA provider: 2captcha | API key: set (hidden) | timeout: 120 s | automatic solving: enabled (only the CAPTCHA image is sent to the provider; manual verification in the browser window remains the fallback)
```

Se aparecer `API key: missing`, o `.env` não foi carregado ou a chave está vazia. Também é
possível digitar a chave sem arquivo, com `read -rsp` (seção 9A do guia).

## 3. Gravar a saída do terminal

A saída de cada comando das seções 4 a 9 deve ser gravada em `logs/acceptance_run.txt`, para
ser enviada depois (seção 11). Prepare o terminal uma vez:

```bash
export PYTHONUNBUFFERED=1
T=logs/acceptance_run.txt
```

Depois, **acrescente ao final de cada comando** `python main.py ...` das seções 4 a 9 o trecho:

```bash
 2>&1 | tee -a -i $T
```

Exemplo com o comando da seção 6:

```bash
python main.py --db data/acceptance_test.db --resume 2>&1 | tee -a -i $T
```

## 4. Planejar o teste (não acessa o site)

```bash
python main.py --db data/acceptance_test.db --start-page 1 --end-page 5
```

**Esperado:**

```text
Run range: pages 1..5 (5 pages)
Recovered interrupted pages: 0
Progress in range: {'pending': 5, 'processing': 0, 'completed': 0, 'failed': 0}
Pages to process: 5 (next: [1, 2, 3, 4, 5])
Verified records stored: 0
Planning only: add --live to fetch pages; no pages were fetched.
```

## 5. Execução supervisionada: páginas 1 e 2

```bash
python main.py --db data/acceptance_test.db --start-page 1 --end-page 5 --live --max-pages 2 --nav-timeout 60
```

**Esperado:**

- No início, a mesma linha `CAPTCHA provider: 2captcha | API key: set (hidden) | ...` da seção 2.
- Uma janela do Chromium abre e um relatório `Progress | current page: 1 | ...` aparece antes de
  cada página.
- No final: `Live run: 2 completed, 0 failed, ...` e `Verified records stored: ...`.
- Em 13/10/2026, a página 1 tinha os segmentos verificados 1 a 9 e a página 2 tinha o 101 (10
  registros no total). Se o site tiver verificado novos segmentos desde então, o número pode ser
  maior, mas esses 10 precisam estar presentes.

### Confirmar a verificação automática do CAPTCHA (se ele aparecer)

Não toque na janela do navegador enquanto o scraper trabalha. Uma verificação automática bem-
sucedida mostra, nesta ordem:

```text
CAPTCHA shown. Sending the CAPTCHA image to the CAPTCHA service (attempt 1 of 3).
Verification succeeded automatically: the browser reached the listing at https://honestpool.ru/puzzle?id=71...; continuing with the same browser session.
```

Depois disso a página solicitada é carregada de novo e a execução continua. Outras mensagens
possíveis:

| Mensagem | Significado | O que fazer |
|---|---|---|
| `The site rejected the automatic answer (attempt 1 of 3).` | Resposta errada; uma nova imagem é enviada | Aguardar |
| `Automatic CAPTCHA solving failed (...); falling back to manual verification.` | O 2Captcha não resolveu | Resolver o CAPTCHA na janela do navegador; anotar "automático: não" |
| `CAPTCHA shown. Please complete it manually in the browser window ...` | Verificação manual | Resolver na janela em até 10 minutos |
| `Run stopped: ...` | Parada segura (seção 13) | Anotar a mensagem completa e parar o teste |

Se nenhum CAPTCHA aparecer, anote "não apareceu" e continue.

## 6. Conferir o progresso salvo (não acessa o site)

```bash
python main.py --db data/acceptance_test.db --resume
```

**Esperado:**

```text
Run range: pages 1..5 (5 pages)
Recovered interrupted pages: 0
Progress in range: {'pending': 3, 'processing': 0, 'completed': 2, 'failed': 0}
Pages to process: 3 (next: [3, 4, 5])
```

## 7. Parar com segurança (Ctrl+C)

```bash
python main.py --db data/acceptance_test.db --resume --live --max-pages 3 --nav-timeout 60
```

Espere a página 3 terminar. Quando aparecer `Progress | current page: 4`, pressione **Ctrl+C uma
vez** e aguarde.

**Esperado:**

```text
Interrupted: the current page was returned to pending and saved records are kept. Continue with --resume --live.
```

Se essa última mensagem não entrar no arquivo de log, anote-a na lista da seção 12. Não feche a janela do terminal para parar o scraper: nesse caso a página fica marcada como
`processing`, é recuperada na próxima execução e a tentativa é contada.

Confira (não acessa o site):

```bash
python main.py --db data/acceptance_test.db --resume
```

**Esperado:** `Progress in range: {'pending': 2, 'processing': 0, 'completed': 3, 'failed': 0}` e
`Pages to process: 2 (next: [4, 5])`. A página 4 voltou para "pendente" sem contar a tentativa.
(Se a página 4 tiver terminado antes do Ctrl+C, o resultado será `completed: 4` e `next: [5]`;
anote isso e continue.)

## 8. Retomar sem repetir páginas concluídas

```bash
python main.py --db data/acceptance_test.db --resume --live --max-pages 5 --nav-timeout 60
```

**Esperado:** `Pages to process: 2 (next: [4, 5])`, relatórios de progresso apenas para as
páginas 4 e 5, e `Live run: 2 completed, 0 failed, ...`. As páginas 1 a 3 **não** são abertas de
novo. Esta é uma nova sessão do navegador, então um novo CAPTCHA é provável (seção 5).

Rode a retomada mais uma vez:

```bash
python main.py --db data/acceptance_test.db --resume --live
```

**Esperado:** `Pages to process: 0`, nenhuma janela do navegador é aberta e
`Verified records stored:` mostra o mesmo número de antes.

## 9. Exportar CSV e Excel (não acessa o site)

```bash
python main.py --db data/acceptance_test.db --export csv --output data/acceptance_test.csv
python main.py --db data/acceptance_test.db --export xlsx --output data/acceptance_test.xlsx
```

**Esperado** (com os 10 registros conhecidos; o número deve ser igual ao de
`Verified records stored`):

```text
Exported 10 verified records to data/acceptance_test.csv
Exported 10 verified records to data/acceptance_test.xlsx
```

## 10. Conferir as quatro colunas e os dados (não acessa o site)

```bash
python demo.py --db data/acceptance_test.db --csv data/acceptance_test.csv --xlsx data/acceptance_test.xlsx
```

O banco é aberto somente para leitura. **Esperado:** a tabela "Status das páginas" com as
páginas 1 a 5 como `completed`, e todas estas linhas com `[OK]`:

```text
  [OK] integridade do SQLite
  [OK] colunas Number, Start hex, End hex, Status
  [OK] CSV idêntico ao SQLite
  [OK] Excel idêntico ao SQLite
  [OK] Start/End hex gravados como texto no Excel (formato '@')
  [OK] Start/End hex completos (18 dígitos hexadecimais, sem máscara)
  [OK] hex confere com o número do segmento
  [OK] todos com status 'Проверено'
```

A última linha deve ser `todas as verificações passaram`. As seções "Exemplos de comandos" e
"Exemplo de exibição de progresso" são apenas ilustrativas.

Abra também `data/acceptance_test.xlsx` no Excel e confira: exatamente as colunas `Number`,
`Start hex`, `End hex` e `Status`; os valores hexadecimais com 18 dígitos (por exemplo,
`400000000000000000` para o Number 1), **sem** notação científica; Status `Проверено`.

## 11. Preparar os arquivos para envio

Faça isso somente com o scraper **parado** (nenhum terminal executando `main.py`).

### 11.1 Cópia consistente do banco SQLite

```bash
python -c "import sqlite3; s = sqlite3.connect('file:data/acceptance_test.db?mode=ro', uri=True); d = sqlite3.connect('acceptance_test_backup.db'); s.backup(d); d.close(); s.close(); print('backup OK')"
python -c "import sqlite3; print(sqlite3.connect('file:acceptance_test_backup.db?mode=ro', uri=True).execute('PRAGMA integrity_check').fetchone()[0])"
```

**Esperado:** `backup OK` e depois `ok`. O arquivo `acceptance_test_backup.db` é uma cópia única
e completa: não copie os arquivos `-wal` e `-shm`. O banco original é aberto somente para
leitura.

**Opcional, mas muito útil:** faça uma cópia do banco usado na execução das páginas 7020 e 7021
da mesma forma, trocando `data/acceptance_test.db` pelo caminho desse banco (o padrão é
`data/honestpool.db`) e `acceptance_test_backup.db` por `execucao_7020_backup.db`.

### 11.2 Saída do terminal sem dados sensíveis

O programa não mostra a chave nem as respostas do CAPTCHA. Mesmo assim, confira antes de enviar
(com o `.env` carregado no terminal, como na seção 2):

```bash
python -c "import os; k = os.environ.get('CAPTCHA_API_KEY', ''); b = open('logs/acceptance_run.txt', 'rb').read(); t = b.decode('utf-16') if b[:2] == b'\xff\xfe' else b.decode('utf-8', 'replace'); print('ATENCAO: a chave aparece no arquivo; nao envie' if k and k in t else ('OK: a chave nao aparece no arquivo' if k else 'Carregue o .env antes'))"
```

**Esperado:** `OK: a chave nao aparece no arquivo`. O comando não mostra a chave. Depois, abra
o arquivo em um editor de texto e substitua o seu nome de usuário ou caminhos pessoais, se
aparecerem, por `USUARIO`. Não apague as outras linhas.

### 11.3 Exemplo de exportação

Envie `data/acceptance_test.csv` (e, se quiser, `data/acceptance_test.xlsx`). Esses arquivos
contêm apenas os números de segmento, os intervalos hexadecimais e o Status.

### O que enviar e o que nunca enviar

| Enviar | Nunca enviar |
|---|---|
| `acceptance_test_backup.db` | O arquivo `.env` ou a chave do 2Captcha, em qualquer formato |
| `logs/acceptance_run.txt` (conferido em 11.2) | Capturas de tela do painel do 2Captcha que mostrem a chave |
| `data/acceptance_test.csv` | Cookies, pastas de perfil do navegador ou dados de sessão |
| A lista da seção 12 preenchida | Arquivos `-wal`, `-shm` ou `.lock` |
| Opcional: `execucao_7020_backup.db` | Logs de rede do navegador (NetLog) |

## 12. Lista de verificação

| # | Verificação | Resultado esperado | Sim / Não / Observação |
|---|---|---|---|
| 1 | Testes offline | `765 passed, 9 skipped` | |
| 2 | Configuração | `API key: set (hidden)` e `automatic solving: enabled` | |
| 3 | Páginas 1 e 2 | `Live run: 2 completed, 0 failed` | |
| 4 | CAPTCHA apareceu? Em qual execução? | Sim / Não | |
| 5 | Resolvido automaticamente? | `Verification succeeded automatically` | |
| 6 | Respostas recusadas | Número de `The site rejected the automatic answer` | |
| 7 | Progresso salvo após a seção 5 | `completed: 2`, `next: [3, 4, 5]` | |
| 8 | Ctrl+C | Mensagem `Interrupted: ...`; página 4 pendente | |
| 9 | Retomada | Só as páginas 4 e 5 processadas | |
| 10 | Nova retomada | `Pages to process: 0` | |
| 11 | Registros | Inclui 1 a 9 e 101 | |
| 12 | `demo.py` | `todas as verificações passaram` | |
| 13 | Excel | 4 colunas, hex com 18 dígitos, sem notação científica | |
| 14 | Saldo do 2Captcha | Valor gasto no teste (painel do 2Captcha) | |
| 15 | Mensagens inesperadas | Copiar o texto completo | |

## 13. Limitações que impedem garantir a operação sem supervisão

**Limites do CAPTCHA por execução** (cada execução de `main.py` é uma sessão do navegador):

- No máximo 3 tentativas do 2Captcha para um mesmo CAPTCHA.
- No máximo 3 verificações automáticas por execução; o quarto CAPTCHA vai para a verificação
  manual.
- No máximo 10 verificações manuais por execução (`--max-manual-verifications`, de 1 a 50), cada
  uma com espera de até 10 minutos (`--captcha-wait`).
- Se o site não confirmar uma resposta enviada dentro de `--nav-timeout` segundos, não há nova
  tentativa automática para esse CAPTCHA; ele vai para a verificação manual.

**CAPTCHAs repetidos:**

- Se o CAPTCHA aparecer de novo logo depois de uma verificação bem-sucedida, para a mesma
  página, a execução para (`CAPTCHA shown again right after verification`).
- Um CAPTCHA novo mais tarde na mesma execução pode ser resolvido à mão de novo. Se o limite de
  verificações manuais já tiver sido usado, a execução para (`a new CAPTCHA appeared and the limit
  of 10 manual verification(s) for this browser session is reached`) e mostra
  `Progress saved: ...` com o comando para continuar.
- No modo `--headless`, qualquer CAPTCHA que o 2Captcha não resolva encerra a execução.

**Erros do 2Captcha e saldo:**

- Saldo insuficiente (`ERROR_ZERO_BALANCE`), chave recusada, IP não permitido, IP bloqueado ou
  conta suspensa desligam a resolução automática até o fim da execução. Com janela, o próximo
  CAPTCHA precisa de uma pessoa; no `--headless`, a execução para.
- Imagem não resolvida (`ERROR_CAPTCHA_UNSOLVABLE`), tempo esgotado (`CAPTCHA_TIMEOUT`, padrão
  120 s) e erros temporários da API contam como tentativas, até 3 por CAPTCHA.
- O programa não consulta o saldo antes de começar; o problema só aparece no primeiro CAPTCHA.

**Restrições de acesso do site:**

- HTTP 401 ou 403: a execução para imediatamente, sem nova tentativa.
- HTTP 429: até `--retries` novas tentativas (padrão 2) com espera crescente; depois a execução
  para. Se o site pedir uma espera maior que 10 minutos, a execução para na hora.
- Os limites de ritmo do site não são publicados e não foram medidos.

**Páginas puladas e falhas repetidas:**

- Timeouts, erros de conexão e 5xx são repetidos até `--retries` vezes; depois a página fica com
  falha (sem registros).
- 3 páginas seguidas com falha encerram a execução (`Run aborted`, código de saída 3).
- Uma página que falhou 3 vezes é pulada até alguém aumentar `--max-attempts`. Um encerramento
  forçado (queda de energia, fechar o terminal) também conta como tentativa.

**Operação:**

- Cada execução processa no máximo `--max-pages` páginas. Não há reinício automático,
  agendamento nem aviso por e-mail ou mensagem: depois de qualquer parada, uma pessoa precisa
  executar `--resume --live` de novo.
- Cada nova execução abre uma nova sessão do navegador, que até agora tem exigido um CAPTCHA.
- Só uma execução por banco de dados ao mesmo tempo.

## 14. Situação de cada função

### Validado

- **Por 765 testes automáticos offline no Linux** (mais 9 que só rodam no Windows), com páginas e
  respostas do 2Captcha simuladas:
  - extração apenas do selo verde "Проверено" exato e conferência dos hexadecimais;
  - gravação de cada página com os seus registros em uma única transação;
  - interrupção simulada (encerramento forçado e Ctrl+C), retomada sem repetir páginas e
    recuperação da página interrompida;
  - o cliente do 2Captcha e o preenchimento do formulário do CAPTCHA;
  - novas tentativas, limites e paradas seguras;
  - exportação CSV/Excel com os hexadecimais como texto, e a importação offline;
  - ausência da chave e das respostas do CAPTCHA nos logs.
- **Em execuções ao vivo supervisionadas, antes do 2Captcha:** páginas 1 a 3 com verificação
  manual (10 registros) e a parada segura quando o CAPTCHA aparece de novo. Os números 9 e 101
  foram confirmados com os seus dados.

### Requer o teste de aceitação ao vivo (este roteiro)

- A resolução automática do CAPTCHA real e a continuação da extração depois dela. As capturas de
  tela enviadas mostram isso nas páginas 7020 e 7021, mas o banco dessa execução não foi
  inspecionado.
- A gravação das páginas e dos registros durante execuções com o 2Captcha.
- A parada com Ctrl+C e a retomada sem repetir páginas, no seu computador e com o site real.
- As exportações geradas a partir de uma execução com o 2Captcha.
- A instalação no Windows 11 com os arquivos `.bat` e a chave salva com o DPAPI
  (`CHECKLIST_ACEITACAO_WINDOWS_11.md`); nada disso foi executado no Windows ainda.
- O custo real e a taxa de acerto do 2Captcha (itens 6 e 14 da lista).

### Ainda não suportado

- Operação sem supervisão: reinício automático, agendamento e avisos.
- Continuar automaticamente depois de um CAPTCHA repetido, de um bloqueio (401/403/429) ou do fim
  do saldo do 2Captcha.
- Consulta do saldo do 2Captcha antes da execução.
- Mais de uma execução simultânea no mesmo banco.
- A extração completa das 671.089 páginas, que não foi feita e levaria meses (seção 11 do guia).

## 15. Depois do teste

Envie os arquivos da seção 11 e a lista da seção 12. Com eles conferimos: as páginas e os
registros salvos, as tentativas de cada página, a ausência de duplicados, os hexadecimais e as
exportações. Nenhum desses arquivos permite acessar a sua conta do 2Captcha ou o seu navegador.
