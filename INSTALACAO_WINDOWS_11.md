# HonestPool — Instalação no Windows 11

Este guia é para quem não tem experiência com programação. Ele acompanha o pacote de teste
**`honestpool-windows11-test-v3`**, uma **versão de diagnóstico**.

## O que é a v3 (versão de diagnóstico)

A v3 serve para um **teste curto e supervisionado** que mostre onde a resolução automática do
CAPTCHA falha no seu computador. Ela **não é uma solução garantida para rodar sozinha**, sem
ninguém acompanhando.

- Cada tentativa de resolver um CAPTCHA aparece na janela preta e no registro (`logs`), etapa por
  etapa: imagem encontrada ou não, envio ao 2Captcha, resposta recebida, aceitação ou recusa
  confirmada pelo site, resultado incerto e verificação manual (seção 6).
- No fim de cada execução aparece a linha **`CAPTCHA summary:`**, com quantos CAPTCHAs apareceram,
  quantas imagens foram enviadas ao 2Captcha, quantas respostas o site recusou, quantos resultados
  ficaram incertos e quantas vezes foi preciso resolver à mão.
- No registro da v1 que você enviou, a imagem do CAPTCHA nunca chegou a ser enviada ao 2Captcha:
  a falha aconteceu antes, ao procurar a imagem na página. A correção dessa busca (já presente na
  v2) e o novo registro ainda precisam ser confirmados no seu computador.
- Uma resposta só conta como **recusada** quando o site mostra uma **imagem de CAPTCHA nova**.
  Qualquer outro resultado fica como **incerto** e não é informado ao 2Captcha como resposta errada.
- Os limites de segurança não mudaram (seção 8). Uma execução envia **no máximo 42 imagens** ao
  2Captcha.
- Continua igual à v2: os registros verificados novos aparecem na janela preta, o arquivo
  `data\verified_records.csv` é atualizado sozinho depois de cada página, e o Excel (XLSX) só é
  criado pela opção 4.

## Mantenha a sua instalação atual e o seu banco de dados

A sua instalação atual (`C:\HonestPool` para a v1, ou `C:\HonestPool-v2` se você instalou a v2)
e o banco de dados dela **continuam exatamente como estão**. A v3 é instalada **numa pasta nova**
e não copia, não apaga e não substitui nada das instalações anteriores.

1. **Feche o programa antigo.** Se uma extração estiver em andamento, pressione **Ctrl+C uma vez**
   na janela preta e aguarde o resumo. Saia do menu com a opção `6` e feche todas as janelas do
   HonestPool (a janela preta e a do Chromium).
2. **Faça uma cópia de segurança** das pastas `data` e `config` da instalação atual (por exemplo
   `C:\HonestPool\data` e `C:\HonestPool\config`): copie as duas para uma pasta nova, por exemplo
   `C:\HonestPool-backup` (botão direito na pasta → **Copiar**; depois, dentro de
   `C:\HonestPool-backup`, botão direito → **Colar**). Essa cópia fica só no seu computador; não a
   envie a ninguém, porque a pasta `config` contém a sua chave cifrada.
3. **Não extraia a v3 dentro de `C:\HonestPool` nem de `C:\HonestPool-v2`** e não apague nada das
   pastas antigas.
4. A v3 começa com um **banco de dados vazio**, e o teste de diagnóstico deve ser feito assim: o
   progresso e os registros antigos **não aparecem nela**, e nada é migrado automaticamente.
   **Não copie o banco de dados antigo para a v3 durante o teste.** Continue usando a instalação
   atual para o trabalho normal até o resultado do diagnóstico ser analisado. A **seção 10**
   explica a cópia manual, para depois.

## Resumo: seis passos

Antes: instale o **Python 3.11 ou 3.12** (obrigatório; seção 3) e mantenha a **internet ligada**
durante a instalação e durante as extrações (seção 1). O arquivo `START_HERE_WINDOWS.txt` traz
este mesmo resumo para abrir no Bloco de Notas.

1. **Extraia o ZIP** para uma pasta **nova**, `C:\HonestPool-v3` (botão direito no ZIP →
   **Extrair tudo...**). O arquivo deve ficar em `C:\HonestPool-v3\setup_windows.bat`.
2. Dê dois cliques em **`setup_windows.bat`** e espere `INSTALACAO CONCLUIDA COM SUCESSO`.
3. Dê dois cliques em **`configurar_2captcha.bat`** e cole a sua chave 2Captcha de novo (nada
   aparece na tela; é normal). A chave salva na instalação anterior não é copiada para a pasta
   nova.
4. Dê dois cliques em **`iniciar_honestpool.bat`** para abrir o menu.
5. Faça um **teste pequeno** e supervisionado de **só 2 páginas**: opção `1`, páginas `1` a `2`,
   `2` páginas nesta sessão, e confirme com `S`. Não feche a janela do navegador que abrir.
   Confira que os registros aparecem na janela preta depois de cada página e que aparece
   `CSV updated: ...\data\verified_records.csv`. Se surgir um CAPTCHA, aguarde sem mexer no
   navegador: com a chave configurada, o programa tenta resolvê-lo sozinho e mostra cada etapa
   (seção 6). No fim aparece a linha `CAPTCHA summary:`.
6. **Exporte** o Excel, se quiser: opção `4` cria `data\verified_records.xlsx`. O CSV
   `data\verified_records.csv` já foi atualizado sozinho no passo 5. Depois, **envie o registro**
   `logs\honestpool_AAAAMMDD.log` (seção 11).

Para conferir tudo passo a passo, use a lista **`CHECKLIST_ACEITACAO_WINDOWS_11.md`**.

> **Nunca envie a sua chave 2Captcha a ninguém**, nem ao desenvolvedor. Ninguém precisa dela
> para ajudar você; as fotos da tela e o arquivo de registro (`logs`) não contêm a chave.

> **Situação atual:** estes arquivos foram preparados e testados automaticamente num computador
> Ubuntu (Linux), mas **ainda não foram executados no Windows**. A seção 9 lista o que precisa ser
> confirmado num computador com Windows 11.
>
> A resolução automática do CAPTCHA pelo 2Captcha **não é garantida**: quando ela falha, o CAPTCHA
> precisa ser resolvido à mão na janela do navegador, ou a extração para por segurança. O programa
> também **não** foi feito para rodar sozinho por longos períodos: alguém precisa acompanhar e
> retomar depois de cada parada.

| Ordem | Arquivo | Para que serve | Quando usar |
|---|---|---|---|
| 1 | `setup_windows.bat` | Instala o programa | Uma vez (ou de novo se algo falhar) |
| 2 | `configurar_2captcha.bat` | Salva a sua chave 2Captcha com segurança | Uma vez (ou para trocar a chave) |
| 3 | `iniciar_honestpool.bat` | Abre o menu do programa | Sempre que for usar o programa |

---

## 1. O que você precisa

- Windows 11.
- **Python 3.11 ou 3.12 (obrigatório).** O Python 3.13 ou mais novo **não** é aceito pelo
  instalador. A seção 3 explica como instalar.
- **Internet (obrigatória):**
  - durante a instalação (`setup_windows.bat`), para baixar as bibliotecas do Python e o
    navegador Chromium do Playwright;
  - durante cada extração, para acessar o site HonestPool e, se configurado, o serviço 2Captcha.

  A configuração da chave, o progresso e as exportações funcionam sem internet.
- Cerca de 1,5 GB livres no disco (Python, bibliotecas e o navegador Chromium).
- Opcional: uma conta 2Captcha com saldo. Sem ela, os CAPTCHAs são resolvidos à mão na janela do
  navegador.

## 2. Extrair o programa

1. Clique com o botão direito no arquivo ZIP e escolha **Extrair tudo...**.
2. No campo do destino, digite **`C:\HonestPool-v3`** e clique em **Extrair**. Os arquivos do ZIP
   ficam diretamente nessa pasta (o ZIP não contém outra pasta dentro).
3. Abra `C:\HonestPool-v3`. Você deve ver `START_HERE_WINDOWS.txt`, `setup_windows.bat`, `main.py`
   e outros arquivos, ou seja, `C:\HonestPool-v3\setup_windows.bat`.

Evite:

- extrair a v3 **por cima de uma instalação anterior** (`C:\HonestPool` ou `C:\HonestPool-v2`):
  use sempre a pasta nova;
- executar os arquivos **de dentro do ZIP** sem extrair (o instalador avisa e para);
- pastas dentro do **OneDrive** (a sincronização pode bloquear o banco de dados; o instalador avisa);
- caminhos com mais de 100 caracteres, por exemplo várias pastas dentro de `Downloads` (o
  instalador avisa, porque o Windows pode recusar arquivos em caminhos longos).

Espaços, acentos e parênteses no nome da pasta são aceitos, por exemplo
`C:\Users\João Silva\HonestPool (1)`.

## 3. Instalar o Python 3.12

O programa **precisa** do **Python 3.11 ou 3.12**; recomendamos o 3.12. O 3.13 ou mais novo
**não** é aceito pelo instalador.

1. Abra <https://www.python.org/downloads/windows/>.
2. Baixe o **Windows installer (64-bit)** da versão 3.12 mais recente.
3. Na primeira tela do instalador, marque **Add python.exe to PATH** e clique em **Install Now**.
4. Aguarde a mensagem de sucesso e feche o instalador.

Se o Python já estiver instalado, pule esta etapa: o `setup_windows.bat` verifica a versão sozinho.

## 4. Instalar o programa: `setup_windows.bat`

1. Dê dois cliques em **`setup_windows.bat`**.
2. Se aparecer **"O Windows protegeu o computador"**, clique em **Mais informações** e depois em
   **Executar assim mesmo**. Isso acontece com arquivos baixados da internet.
3. Aguarde. A instalação leva de 5 a 15 minutos, conforme a internet, e mostra seis etapas:

   ```text
   [1/6] Procurando Python 3.12 ou 3.11...
   [2/6] Preparando o ambiente virtual .venv...
   [3/6] Instalando as dependencias - pode levar alguns minutos...
   [4/6] Instalando o navegador Chromium do Playwright - pode levar alguns minutos...
   [5/6] Criando as pastas data, logs e config...
   [6/6] Verificando a instalacao...
   ```

4. No final deve aparecer:

   ```text
   ============================================================
    INSTALACAO CONCLUIDA COM SUCESSO
   ============================================================
   ```

5. Pressione qualquer tecla para fechar a janela.

A instalação **não inicia nenhuma extração** e não pede a chave 2Captcha. As mensagens das janelas
pretas aparecem sem acentos de propósito, para evitar caracteres estranhos no Windows.

### Se a instalação falhar

A janela não fecha sozinha: leia a mensagem que começa com `ERRO:` ou `AVISO:`.

| Mensagem | O que fazer |
|---|---|
| `este arquivo foi aberto de dentro do ZIP` | Extraia o ZIP (seção 2) e execute de novo a partir da pasta extraída. |
| `arquivos do programa nao foram encontrados` | Extraia o ZIP completo de novo; não copie só os arquivos `.bat`. |
| `o Python 3.12 ou 3.11 nao foi encontrado` | Instale o Python (seção 3), marcando **Add python.exe to PATH**. Feche a janela e execute de novo. |
| `AVISO: ... OneDrive` ou `AVISO: o caminho desta pasta tem mais de 100 caracteres` | Feche a janela, mova a pasta para `C:\HonestPool-v3` e execute de novo. |
| `a pasta .venv existente nao usa Python 3.11 ou 3.12` | Apague a pasta `.venv` dentro da pasta do programa e execute de novo. |
| `a instalacao das dependencias falhou` | Verifique a internet; antivírus, proxy da empresa ou um caminho longo podem bloquear. Execute de novo. |
| `a instalacao do navegador Chromium do Playwright falhou` | Verifique a internet e o espaço em disco. Execute de novo. |
| `nao foi possivel criar as pastas` | A pasta pode estar protegida. Use `C:\HonestPool-v3`. |
| `a verificacao final da instalacao falhou` | Execute de novo; se continuar, apague a pasta `.venv` e execute mais uma vez. |

Executar o `setup_windows.bat` de novo é seguro: ele reaproveita o que já foi instalado e nunca apaga
o banco de dados, as exportações nem a chave salva. Se precisar de ajuda, envie uma **foto ou
captura da janela** (ela não contém a sua chave).

Se `configurar_2captcha.bat` ou `iniciar_honestpool.bat` disserem
`a instalacao nao foi concluida`, execute `setup_windows.bat` de novo até aparecer
`INSTALACAO CONCLUIDA COM SUCESSO`.

## 5. Configurar a chave 2Captcha: `configurar_2captcha.bat`

Faça isto uma vez, depois da instalação. Na v3, faça de novo mesmo que já tenha configurado a
chave na v1 ou na v2: a chave fica na pasta `config` de cada instalação e não é copiada. Não copie
a pasta `config` da instalação anterior; digite a chave de novo aqui. Se você não usa a 2Captcha, pule esta etapa: o menu
funciona com CAPTCHAs manuais.

1. Entre no site da 2Captcha, abra o painel da sua conta e copie o valor do campo **API Key**.
2. Dê dois cliques em **`configurar_2captcha.bat`**.
3. Quando aparecer
   `Cole a chave da API 2Captcha e pressione Enter (nada aparece na tela):`, cole a chave com
   **clique com o botão direito** ou **Ctrl+V** e pressione **Enter**.
   **Nada aparece enquanto você cola: isso é normal e proposital.**
4. O programa confirma o tamanho, sem mostrar a chave:

   ```text
   Chave recebida (32 caracteres; o conteúdo não é mostrado).
   Chave salva e cifrada em config\2captcha.cfg.
   Configurado: CAPTCHA_PROVIDER=2captcha e CAPTCHA_TIMEOUT=120.
   ```

5. Em seguida ele verifica a configuração, **sem contatar nenhum serviço**. O resultado esperado é:

   ```text
   CAPTCHA provider: 2captcha | API key: set (hidden) | timeout: 120 s | automatic solving: enabled (...)
   CONFIGURAÇÃO OK: a chave está salva e será usada automaticamente pelo menu.
   ```

Essa verificação confere apenas que a chave foi salva. A chave e o saldo só são conferidos pela
2Captcha quando o primeiro CAPTCHA aparecer numa extração.

Se aparecer `Chave recebida (0 caracteres...)` ou `Nenhuma chave foi digitada`, a colagem não
funcionou: execute de novo e cole com o botão direito do mouse. Se a chave tiver um tamanho diferente
de 32, o programa pergunta antes de salvar; normalmente basta copiar a chave de novo, completa.

### Como a chave é protegida

- A chave fica somente em **`config\2captcha.cfg`**, cifrada com a proteção de dados do Windows
  (DPAPI). Só **o mesmo usuário do Windows, no mesmo computador**, consegue decifrá-la. Uma cópia
  desse arquivo não funciona em outro computador nem em outro usuário.
- A chave **nunca aparece na tela** e **nunca é gravada nos registros** (`logs`). Se algum texto
  contiver a chave, ele é substituído por `[CHAVE OCULTA]` antes de aparecer.
- A chave só é enviada ao serviço **2Captcha**, e somente quando um CAPTCHA precisa ser resolvido
  durante uma extração. Ela não é enviada ao desenvolvedor nem a ninguém mais.
- Dentro do computador, a chave só é entregue ao programa de extração nas operações que a usam
  (extração e verificação da configuração), e não às exportações nem ao progresso. O programa a
  retira do próprio ambiente antes de abrir o navegador: nem o Chromium nem o componente do
  Playwright que o controla recebem a chave.
- **Nunca envie** a chave nem o arquivo `config\2captcha.cfg` a ninguém, nem ao desenvolvedor.
- Para **trocar** a chave, execute `configurar_2captcha.bat` de novo e responda `S` quando ele
  perguntar se deseja substituí-la. Para **remover** a chave, apague o arquivo
  `config\2captcha.cfg`.
- O arquivo `.env` usado no Linux **não** é necessário no Windows.
- Atenção: um programa malicioso que rode com o seu usuário do Windows conseguiria usar a chave.
  Mantenha o Windows e o antivírus atualizados.

## 6. Usar o programa: `iniciar_honestpool.bat`

Dê dois cliques em **`iniciar_honestpool.bat`**. O menu aparece:

```text
================================================================
 HonestPool - menu principal
================================================================
 2Captcha: configurado (chave oculta)
 Onde os arquivos são salvos:
   Banco de dados (fonte principal do progresso e dos registros): C:\HonestPool-v3\data\honestpool.db
   CSV (atualizado automaticamente após cada página concluída): C:\HonestPool-v3\data\verified_records.csv
   Excel XLSX (gerado somente pela opção 4): C:\HonestPool-v3\data\verified_records.xlsx
   Registro (log) desta sessão: C:\HonestPool-v3\logs\honestpool_AAAAMMDD.log
 Nenhuma extração começa sem a sua confirmação.

  1) Iniciar extração supervisionada (escolher página inicial e final)
  2) Retomar extração interrompida
  3) Exportar CSV agora (durante a extração ele já é atualizado após cada página concluída)
  4) Exportar Excel (XLSX) - o XLSX só é gerado por esta opção
  5) Mostrar progresso
  6) Sair
Escolha uma opção (1-6):
```

Digite o número da opção e pressione **Enter**. Os números são digitados **sem pontos**
(por exemplo `350000`, e não `350.000`).

### 1) Iniciar extração supervisionada

1. Digite a **página inicial** e a **página final** (de 1 a 671089).
2. Digite **quantas páginas buscar nesta sessão**. Comece com poucas (por exemplo 2 a 5).
3. Confira o resumo e digite **`S`** para começar, ou **`N`** para voltar sem buscar nada.

Uma janela do Chromium abre. **Não feche essa janela** durante a sessão. A faixa escolhida passa a
ser a faixa salva para a opção 2. Páginas já concluídas nunca são buscadas de novo e os registros
já salvos nunca são apagados.

**Se aparecer um CAPTCHA:** com a chave 2Captcha configurada, o programa tenta resolvê-lo sozinho.
Aguarde **sem mexer na janela do Chromium** (pode levar 1 a 2 minutos). A janela preta mostra:

| Mensagem | Significado |
|---|---|
| `CAPTCHA shown. Sending the CAPTCHA image to the CAPTCHA service (attempt 1 of 3).` | A imagem foi enviada ao 2Captcha. |
| `Verification succeeded automatically: ...` | O 2Captcha resolveu; a mesma página é carregada de novo e a extração continua. |
| `Automatic CAPTCHA solving failed (...); falling back to manual verification.` e `CAPTCHA shown. Please complete it manually ...` | A resolução automática não funcionou: resolva o CAPTCHA à mão na janela do Chromium. |

Só resolva o CAPTCHA à mão quando o programa pedir. Anote qual das mensagens apareceu: isso
mostra se a integração automática com o 2Captcha funcionou no seu computador.

**Linhas de diagnóstico da v3.** Além dessas mensagens, cada etapa aparece numa linha que começa
com `CAPTCHA N attempt M/3:` (N é o número do CAPTCHA na execução, M a tentativa). Elas também
ficam no registro `logs\honestpool_AAAAMMDD.log`. Você não precisa fazer nada com elas; servem para
o diagnóstico:

| Linha (em inglês) | Significado |
|---|---|
| `CAPTCHA 1 shown for page 1` | O site mostrou um CAPTCHA. |
| `CAPTCHA 1 attempt 1/3: image not found; nothing sent to 2Captcha` | O programa não encontrou a imagem do CAPTCHA na página; nada foi enviado nem cobrado. |
| `CAPTCHA 1 attempt 1/3: image captured (... bytes, hash ...)` | A imagem foi encontrada e capturada. |
| `2Captcha task ... submitted` | A imagem foi enviada ao 2Captcha (uma tarefa foi criada). |
| `CAPTCHA 1 attempt 1/3: answer received from 2Captcha (...)` | O 2Captcha devolveu uma resposta (a resposta em si nunca aparece). |
| `CAPTCHA 1 attempt 1/3: no answer from 2Captcha: ...` | O 2Captcha não resolveu a imagem a tempo ou deu erro; o motivo aparece em seguida. |
| `CAPTCHA 1 attempt 1/3: 2Captcha account problem ...` | Problema na conta (chave recusada, saldo insuficiente...): a resolução automática fica desligada até o fim da execução. |
| `CAPTCHA 1 attempt 1/3: answer submitted to the website` | A resposta foi digitada no site. |
| `CAPTCHA 1 attempt 1/3: website acceptance confirmed ...` | O site aceitou a resposta. |
| `CAPTCHA 1 attempt 1/3: website rejection confirmed ...` | O site mostrou uma imagem nova, ou seja, recusou a resposta; o 2Captcha é avisado e há uma nova tentativa. |
| `CAPTCHA 1 attempt 1/3: verification outcome uncertain (...)` | Não foi possível confirmar se o site aceitou ou recusou; nada é informado ao 2Captcha. |
| `CAPTCHA 1: manual fallback (...)` | A resolução automática não deu certo; o programa pede a verificação manual. |
| `CAPTCHA summary: ...` | Resumo da execução (aparece no fim). |

Depois de cada página concluída, a janela preta mostra os registros verificados novos dessa
página, com as colunas do arquivo exportado, e confirma que o CSV foi atualizado:

```text
Page 5: 2 new verified record(s) saved:
  Number  Start hex           End hex             Status
     450  40001c100000000000  40001c200000000000  Проверено
     499  40001f200000000000  40001f300000000000  Проверено
CSV updated: C:\HonestPool-v3\data\verified_records.csv (3 verified record(s)).
```

Uma página sem registros verificados mostra `Page N: no new verified records.` Os registros são
gravados primeiro no banco de dados (`data\honestpool.db`), que é a fonte principal; em seguida o
CSV `data\verified_records.csv` é regravado inteiro a partir do banco, de forma atômica (um arquivo
temporário completo substitui o anterior). Se a extração for interrompida, o CSV continua sendo o
último arquivo completo, nunca um arquivo pela metade, e é sincronizado com o banco no início da
próxima extração. Se o CSV estiver aberto no Excel, o Windows impede a substituição: aparece um
aviso, a extração continua e o CSV é gravado de novo na página seguinte (feche o arquivo).

### 2) Retomar extração interrompida

O programa mostra primeiro a situação da faixa salva, sem buscar nada no site. Depois pergunta
quantas páginas buscar e pede confirmação com **`S`**. Páginas interrompidas ou com falha vêm
primeiro.

### 3) Exportar CSV e 4) Exportar Excel

Criam `data\verified_records.csv` ou `data\verified_records.xlsx` com todos os registros
verificados (o menu mostra o caminho completo). Um arquivo anterior com o mesmo nome é substituído;
copie-o antes, se quiser guardá-lo. **Feche o arquivo no Excel antes de exportar de novo**: o
Windows não deixa substituir um arquivo aberto.

- **CSV:** já é atualizado automaticamente depois de cada página concluída; a opção 3 só o grava de
  novo na hora, com o mesmo conteúdo.
- **Excel (XLSX):** **não** é gerado automaticamente; só a opção 4 cria ou atualiza
  `data\verified_records.xlsx`. Use-a depois de uma extração para ter a planilha atualizada.

### 5) Mostrar progresso

Mostra quantas páginas da faixa salva estão concluídas, pendentes ou com falha, e quantos registros
estão salvos. Nada é buscado no site.

### 6) Sair

Fecha o menu. Pressione qualquer tecla para fechar a janela.

### Parar uma extração com segurança

- Pressione **Ctrl+C uma única vez** na janela preta e **aguarde** o resumo final. A página atual
  volta para pendente, todos os registros já salvos são mantidos e só então a janela do Chromium
  é fechada. Depois use a opção 2.
- Não feche a janela preta pelo **X** durante uma extração. Mesmo assim, se o computador desligar
  ou a janela for fechada, a página interrompida é recuperada automaticamente na próxima vez
  (aguarde alguns segundos antes de abrir o menu de novo).
- O menu encerra qualquer processo do navegador que continue aberto depois do fim de uma
  extração, e o Windows encerra todos eles quando o menu é fechado, inclusive pelo **X**.
- Se aparecer **"Deseja finalizar o arquivo em lotes (S/N)?"** ao sair do menu, responda **S**.

### Mensagens do programa

As mensagens do próprio programa de extração aparecem em inglês; o menu explica o resultado em
português no final de cada operação:

| Mensagem do menu | Significado |
|---|---|
| `Concluído sem erros.` | A operação terminou normalmente. |
| `Interrompido com Ctrl+C...` | Você parou a extração; continue com a opção 2. |
| `A extração parou por segurança...` | O site mostrou o CAPTCHA de novo logo depois de resolvido, o limite de 10 CAPTCHAs resolvidos à mão na mesma execução foi atingido, o site recusou o acesso, ou 3 páginas seguidas falharam. O motivo aparece em `Run stopped:` e a linha `Progress saved:` mostra quantas páginas já estão salvas. As páginas concluídas não são baixadas de novo: continue com a opção 2. |
| `O programa informou um erro...` | Por exemplo, outra janela do programa já está usando o banco de dados (`another process is writing`). Feche a outra janela. |

## 7. Onde ficam os arquivos

| Arquivo | Conteúdo | Pode enviar ao suporte? |
|---|---|---|
| `data\honestpool.db` | Banco de dados com o progresso e os registros (a fonte principal; começa vazio numa instalação nova, veja a seção 10) | Somente se for pedido, com o menu fechado |
| `data\verified_records.csv` | Todos os registros verificados, atualizado automaticamente após cada página concluída (e pela opção 3) | Sim |
| `data\verified_records.xlsx` | Planilha Excel, gerada somente pela opção 4 | Sim |
| `logs\honestpool_AAAAMMDD.log` | Registro de tudo o que apareceu na tela, por dia (sem a chave) | Sim |
| `config\2captcha.cfg` | A sua chave 2Captcha cifrada | **Nunca** |
| `.venv\` | Python e bibliotecas instalados | Não (é recriado pelo `setup_windows.bat`) |

## 8. O que os arquivos `.bat` não mudam

Os três arquivos só instalam, configuram e chamam o programa existente (`main.py`). Os limites de
segurança continuam os mesmos do programa:

- pausa de 15 segundos, mais até 5 segundos aleatórios, entre páginas;
- no máximo 2 novas tentativas automáticas por página após erros temporários, e 3 tentativas por
  página no total antes de ela ficar marcada como falha;
- no máximo 3 tentativas automáticas por CAPTCHA, 3 verificações automáticas e 10 verificações
  manuais por sessão;
- no máximo 42 imagens enviadas ao 2Captcha por execução (limite fixo do programa);
- uma resposta só é informada ao 2Captcha como errada quando o site mostra uma imagem nova;
- a extração para sozinha após 3 páginas seguidas com falha;
- a janela do navegador fica sempre visível (o menu nunca usa o modo sem janela);
- tempo limite de carregamento de 60 segundos por página, o valor recomendado no guia do cliente;
- recuperação automática das páginas interrompidas em toda nova execução.

O uso sem supervisão contínua **não é garantido**: o programa pode parar por segurança e precisa de
alguém para retomar.

## 9. Validação necessária no Windows 11

Os testes automáticos rodaram no Ubuntu, onde o `cmd.exe`, o Windows DPAPI e o `msvcrt` não
existem. Por isso, **os itens abaixo ainda não foram executados no Windows** e precisam ser
confirmados num computador com Windows 11 antes da entrega definitiva. A lista
`CHECKLIST_ACEITACAO_WINDOWS_11.md` cobre os mesmos pontos, passo a passo.

1. `setup_windows.bat` com o Python 3.12 instalado pelo site python.org (com e sem o `py`), e sem
   nenhum Python (deve mostrar a mensagem de instalação do Python).
2. Execução a partir de uma pasta com espaços, acentos e parênteses, por exemplo
   `C:\Users\João Silva\HonestPool (1)`, e a partir de dentro do ZIP (deve mostrar o aviso).
3. Aviso do SmartScreen ao abrir um `.bat` baixado da internet.
4. Instalação das dependências e do Chromium do Playwright, inclusive com antivírus ativo.
5. `configurar_2captcha.bat`: digitação oculta e colagem com **Ctrl+V** e com o botão direito.
6. Cifragem com o Windows DPAPI: salvar, ler de novo, e confirmar que outro usuário do Windows não
   consegue decifrar o arquivo `config\2captcha.cfg`.
7. Trava do banco de dados no Windows (`msvcrt`): abrir uma segunda janela de extração deve mostrar
   que o banco já está em uso.
8. **Ctrl+C** durante uma extração pelo menu: resumo final exibido, página atual de volta a
   pendente, a janela do Chromium fechada, e o aviso "Deseja finalizar o arquivo em lotes (S/N)?".
   Depois de Ctrl+C e depois de fechar a janela preta pelo **X**, nenhum processo do Chromium do
   programa nem `node.exe` deve continuar no Gerenciador de Tarefas.
9. Acentos e caracteres russos (`Проверено`) na janela e no arquivo `logs\honestpool_AAAAMMDD.log`.
10. Uma extração curta e supervisionada pelo menu (2 páginas), a exportação Excel (opção 4) e a
    opção de progresso.
11. Os registros verificados novos aparecendo na janela preta depois de cada página, e o arquivo
    `data\verified_records.csv` atualizado sozinho depois de cada página (inclusive o aviso quando
    o arquivo está aberto no Excel).
12. A resolução automática de um CAPTCHA pelo 2Captcha, se um CAPTCHA aparecer naturalmente
    durante o teste (o teste não deve provocar CAPTCHAs), com as linhas de diagnóstico
    `CAPTCHA N attempt M/3: ...` e o resumo `CAPTCHA summary:` no registro.
13. A cópia manual do banco de dados da instalação anterior (seção 10), somente depois que o
    resultado do diagnóstico for analisado.
14. Opcional, para quem tem experiência técnica: os testes automáticos no Windows, com
    `.venv\Scripts\python.exe -m pytest -q` na pasta do programa. Eles não acessam a internet.

## 10. Copiar o banco de dados da instalação anterior (opcional, depois do diagnóstico)

**Não faça isto durante o teste de diagnóstico.** Use esta seção somente depois que o resultado do
diagnóstico for analisado, e somente se quiser continuar na v3 a extração feita na instalação
anterior. Nada é migrado automaticamente, e estes passos só **copiam** arquivos: a pasta da
instalação anterior (`C:\HonestPool` para a v1, ou `C:\HonestPool-v2` para a v2) não é alterada e
continua servindo de cópia de segurança. O banco de dados da v1 e o da v2 funcionam na v3 sem
conversão (a estrutura do banco não mudou).

Nos passos abaixo, **"pasta anterior"** é a pasta da instalação que você usa hoje para o trabalho
normal: `C:\HonestPool` ou `C:\HonestPool-v2`.

**Antes de copiar**

1. Anote o progresso da instalação anterior: dê dois cliques em `iniciar_honestpool.bat` da pasta
   anterior, digite `5` e anote (ou fotografe) as linhas `Run range:`, `Progress in range:` e
   `Verified records stored:`. Saia com `6`.
2. Feche **todas** as janelas do HonestPool, de todas as versões. Nenhuma extração pode estar em
   andamento. Aguarde alguns segundos.
3. Confira que a cópia de segurança existe: `C:\HonestPool-backup\data\honestpool.db`.
4. No Explorador de Arquivos, ative **Exibir → Mostrar → Extensões de nomes de arquivos**. Assim
   você vê os nomes completos, como `honestpool.db-wal`.

**Copiar**

5. Abra `C:\HonestPool-v3\data`. Se já existir um `honestpool.db` ali (por exemplo, do teste de
   2 páginas), **não o apague**: crie dentro de `data` uma pasta chamada `teste_v3` e **mova** para
   ela todos os arquivos cujo nome começa com `honestpool.db` (`honestpool.db`,
   `honestpool.db-wal`, `honestpool.db-shm`, `honestpool.db.lock`) e também `verified_records.csv`
   e `verified_records.xlsx`, se existirem.
6. Abra a pasta `data` da pasta anterior (por exemplo `C:\HonestPool\data`). Selecione
   `honestpool.db` e, se existirem, `honestpool.db-wal` e `honestpool.db-shm`. Esses arquivos
   precisam ser copiados **juntos**. Clique com o botão direito → **Copiar**.
7. Abra `C:\HonestPool-v3\data`, clique com o botão direito num espaço vazio → **Colar**.
   Se o Windows perguntar se deseja **substituir** algum arquivo, escolha **Ignorar** (não
   substitua) e volte ao passo 5: algum arquivo antigo ficou na pasta da v3.

Não copie `honestpool.db.lock`, a pasta `config` nem os arquivos `verified_records` da pasta
anterior: a chave é configurada de novo com `configurar_2captcha.bat`, e o CSV da v3 é recriado a
partir do banco.

**Conferir**

8. Dê dois cliques em `C:\HonestPool-v3\iniciar_honestpool.bat`. O menu deve mostrar
   `Banco de dados (...): C:\HonestPool-v3\data\honestpool.db`.
9. Digite `5`. `Run range:`, `Progress in range:` e `Verified records stored:` devem ser **iguais**
   ao que você anotou no passo 1. Se forem diferentes, não extraia nada e peça ajuda.
10. Para continuar, use a opção `2` (Retomar). As páginas já concluídas **não** são buscadas de
    novo. No início da extração aparece
    `CSV synchronized with the database: N verified record(s) in C:\HonestPool-v3\data\verified_records.csv`:
    o CSV da v3 passa a ter todos os registros antigos e os novos.

**Depois da cópia**

- Use **somente a v3**. Não continue extraindo na pasta anterior: os dois bancos são cópias
  separadas e passariam a ter progressos diferentes.
- Guarde a pasta anterior e a cópia de segurança até confirmar que a v3 funciona. O programa nunca
  apaga essas pastas; a decisão de apagá-las depois é sua.
- Se algo der errado, feche o menu da v3 e apague apenas os arquivos `honestpool.db*` que você
  colou em `C:\HonestPool-v3\data` (são cópias: os originais continuam na pasta anterior e na
  cópia de segurança). Depois repita a partir do passo 5, ou peça ajuda enviando uma foto da
  tela. A foto não contém a sua chave.

## 11. Encontrar e enviar o registro (log) do teste

Tudo o que aparece na janela preta durante uma operação do menu, inclusive as linhas de
diagnóstico do CAPTCHA, é gravado num registro diário:

```text
C:\HonestPool-v3\logs\honestpool_AAAAMMDD.log
```

`AAAAMMDD` é a data do teste: por exemplo, `honestpool_20261015.log` para 15/10/2026. O menu
mostra o caminho completo em `Registro (log) desta sessão:`. Cada operação começa no arquivo com
uma linha `=== data hora | python main.py ... ===` e termina com `=== código de saída N ===`.

**Como encontrar**

1. Feche o menu (opção `6`), para que o registro esteja completo.
2. Abra a pasta `C:\HonestPool-v3\logs` no Explorador de Arquivos.
3. Dê dois cliques no arquivo com a data do teste (ele abre no Bloco de Notas).
4. Para conferir o resumo, pressione **Ctrl+F** e procure `CAPTCHA summary`. Há uma linha dessas
   no fim de cada extração que terminou normalmente ou parou por segurança. Se a extração foi
   interrompida com Ctrl+C, o resumo aparece numa linha `run ended early; CAPTCHA summary: ...`.

**O que o registro contém (e o que não contém)**

- Contém as mensagens da tela, as linhas de diagnóstico `CAPTCHA N attempt M/3: ...`, o número
  das tarefas do 2Captcha e um código curto (`hash`) de cada imagem, que só serve para saber se
  duas tentativas viram a mesma imagem.
- **Não** contém a chave 2Captcha: se ela aparecer em algum texto, é trocada por `[CHAVE OCULTA]`
  antes de ser gravada.
- **Não** contém as respostas do CAPTCHA, cookies nem dados da sessão do navegador. Os endereços
  do site aparecem reduzidos (por exemplo `https://honestpool.ru/puzzle?id=71`).
- Os caminhos de pastas do seu computador aparecem (por exemplo `C:\HonestPool-v3\data\...`).

**Como enviar**

- Envie o arquivo **como está**, sem editar, junto com a lista
  `CHECKLIST_ACEITACAO_WINDOWS_11.md` preenchida.
- Se fez mais de um teste no mesmo dia, todos estão no mesmo arquivo. Se o teste passou da
  meia-noite, envie também o arquivo do dia seguinte.
- **Nunca envie** a pasta `config`, o arquivo `config\2captcha.cfg`, a pasta `.venv` nem a sua
  chave. O banco `data\honestpool.db` só deve ser enviado se for pedido.
