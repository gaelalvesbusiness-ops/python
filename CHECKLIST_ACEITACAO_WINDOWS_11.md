# HonestPool — Lista de aceitação no Windows 11 (pacote de teste v3, diagnóstico)

Use esta lista para instalar e testar o pacote **`honestpool-windows11-test-v3`** num computador
com Windows 11. A v3 é uma **versão de diagnóstico**: ela registra cada etapa da resolução
automática do CAPTCHA, para descobrir onde ela falha no seu computador. A lista confirma a
instalação numa pasta nova (sem mexer na instalação atual), a configuração do 2Captcha, os
registros na tela, o CSV atualizado sozinho, o salvamento do progresso, a retomada, as exportações
e o registro (log) de diagnóstico. As instruções detalhadas estão em `INSTALACAO_WINDOWS_11.md`.

> **Importante:**
>
> - Esta é uma **versão de diagnóstico** para um teste curto e supervisionado. Ela **não** é uma
>   solução garantida para rodar sozinha, sem ninguém acompanhando.
> - Os arquivos `.bat` e as mudanças da v3 foram testados automaticamente no Ubuntu (Linux), sem
>   acessar o site, mas **ainda não foram executados no Windows**. Esta lista é o teste real no
>   Windows.
> - A resolução automática do CAPTCHA pelo 2Captcha **não é garantida**. Se ela falhar, resolva o
>   CAPTCHA à mão na janela do navegador, ou a extração para por segurança. Anote o que aconteceu.
> - A operação contínua por longos períodos, sem uma pessoa acompanhando, **não é garantida**.
> - A sua instalação atual (`C:\HonestPool` ou `C:\HonestPool-v2`) e o banco de dados dela **não
>   são alterados**. Não copie o banco de dados antigo para a v3 durante este teste.
> - Nunca envie a sua chave 2Captcha, o arquivo `config\2captcha.cfg` ou um arquivo `.env` a
>   ninguém, nem ao desenvolvedor. Ninguém vai pedir a sua chave.
> - É **obrigatório** ter o **Python 3.11 ou 3.12** e **internet** durante a instalação (bibliotecas
>   e navegador Chromium) e durante as extrações (site HonestPool e 2Captcha).
> - O teste busca **só 2 páginas** no site (mais 3, opcionalmente, no item 20).

**Como preencher:** marque `OK` quando o resultado for o esperado. Quando não for, escreva o que
apareceu na coluna "Observações" e tire uma foto ou captura da janela. As mensagens do programa de
extração aparecem em inglês; os textos esperados abaixo estão exatamente como aparecem na tela.

Tempo estimado: 30 a 60 minutos (a instalação leva de 5 a 15 minutos; cada página leva cerca de
20 segundos ou mais).

---

## A. Antes de instalar (somente se você já usa a v1 ou a v2)

Se você nunca instalou o HonestPool, marque os itens 1 e 2 como "não se aplica" e vá para o item 3.

| Nº | O que fazer | Resultado esperado | OK? | Observações |
|---|---|---|---|---|
| 1 | Feche o programa antigo: se uma extração estiver em andamento, pressione **Ctrl+C** uma vez e aguarde o resumo; depois digite `6` no menu e feche todas as janelas do HonestPool. | Nenhuma janela preta nem janela do Chromium do HonestPool fica aberta. | | |
| 2 | Copie as pastas `data` e `config` da instalação atual (por exemplo `C:\HonestPool\data` e `C:\HonestPool\config`) para uma pasta nova, por exemplo `C:\HonestPool-backup`. Não apague nada da instalação atual. | `C:\HonestPool-backup\data\honestpool.db` existe (se você já fez extrações). A instalação atual continua igual. | | |

## B. Instalação

| Nº | O que fazer | Resultado esperado | OK? | Observações |
|---|---|---|---|---|
| 3 | Extraia o ZIP para uma pasta **nova**, `C:\HonestPool-v3` (botão direito → **Extrair tudo...**), e abra a pasta. Não extraia dentro de `C:\HonestPool` nem de `C:\HonestPool-v2`. | Aparecem `START_HERE_WINDOWS.txt`, `setup_windows.bat`, `configurar_2captcha.bat`, `iniciar_honestpool.bat` e `main.py` diretamente em `C:\HonestPool-v3`. | | |
| 4 | Se o Python 3.11 ou 3.12 ainda não estiver instalado, instale o 3.12 (guia, seção 3), marcando **Add python.exe to PATH**. | O instalador do Python termina com sucesso. | | |
| 5 | Dê dois cliques em `setup_windows.bat`. Se aparecer "O Windows protegeu o computador", clique em **Mais informações** → **Executar assim mesmo**. | A janela preta abre e mostra `[1/6] Procurando Python 3.12 ou 3.11...` e depois `Encontrado:`. | | |
| 6 | Aguarde até o fim, sem fechar a janela. | Aparece `INSTALACAO CONCLUIDA COM SUCESSO`. Anote quanto tempo levou. | | |
| 7 | Abra a pasta `C:\HonestPool-v3`. | Existem as pastas `data`, `logs`, `config` e `.venv`. A pasta `data` não tem `honestpool.db` (a v3 começa vazia). | | |

## C. Configuração do 2Captcha

A chave salva na instalação anterior não é copiada: configure-a de novo nesta pasta.

| Nº | O que fazer | Resultado esperado | OK? | Observações |
|---|---|---|---|---|
| 8 | Copie a sua chave do painel da 2Captcha (campo **API Key**). Dê dois cliques em `configurar_2captcha.bat`, cole com **Ctrl+V** ou com o botão direito do mouse e pressione **Enter**. | Nada aparece enquanto você cola (é proposital). | | |
| 9 | Leia as linhas seguintes. | `Chave recebida (32 caracteres; o conteúdo não é mostrado).` e `Chave salva e cifrada em config\2captcha.cfg.` | | |
| 10 | Leia a verificação final. | `API key: set (hidden)` e `CONFIGURAÇÃO OK`. A chave **não** aparece em nenhum lugar da janela. | | |
| 11 | Dê dois cliques em `iniciar_honestpool.bat`. | O menu abre e mostra `2Captcha: configurado (chave oculta)`, as opções 1 a 6 e, em `Onde os arquivos são salvos:`, os caminhos `C:\HonestPool-v3\data\honestpool.db`, `C:\HonestPool-v3\data\verified_records.csv`, `C:\HonestPool-v3\data\verified_records.xlsx` e `C:\HonestPool-v3\logs\honestpool_AAAAMMDD.log`. | | |

## D. Salvamento do progresso: teste supervisionado de 2 páginas, CAPTCHA, registros na tela e CSV automático

Faça os itens 12 a 19 no menu aberto no item 11, sem sair da frente do computador.

| Nº | O que fazer | Resultado esperado | OK? | Observações |
|---|---|---|---|---|
| 12 | Digite `1`. Página inicial `1`, página final `2`, número de páginas `2`, e confirme com `S`. | Uma janela do Chromium abre. A janela preta mostra `Run range: pages 1..2 (2 pages)`, `CSV synchronized with the database: 0 verified record(s) in C:\HonestPool-v3\data\verified_records.csv` e um relatório de progresso que começa com `Progress`. Não feche a janela do navegador. | | |
| 13 | Se aparecer um CAPTCHA, **não mexa** na janela do Chromium e aguarde até 2 minutos por tentativa. Só resolva à mão se aparecer `Please complete it manually`. | Aparece `CAPTCHA 1 shown for page ...` e depois linhas que começam com `CAPTCHA 1 attempt 1/3:`. No fim, uma destas: `Verification succeeded automatically` (resolvido pelo 2Captcha); ou `falling back to manual verification` seguido do pedido para resolver à mão; ou a parada por segurança com o motivo. Anote qual foi. Se nenhum CAPTCHA aparecer, escreva "sem CAPTCHA". | | |
| 14 | Depois que a página 1 terminar, leia a janela preta. | `Page 1: N new verified record(s) saved:` seguido de uma tabela com as colunas `Number`, `Start hex`, `End hex` e `Status` (Status `Проверено`), ou `Page 1: no new verified records.` se a página não tiver registros verificados. Logo abaixo: `CSV updated: C:\HonestPool-v3\data\verified_records.csv (N verified record(s)).` | | |
| 15 | Aguarde o fim da sessão. | A página 2 mostra as mesmas linhas (`Page 2: ...` e `CSV updated: ...`). No fim: `Live run: 2 completed, 0 failed`, a linha `CAPTCHA summary: ...`, as linhas `Database`, `CSV (rewritten automatically after every completed page)` e `Excel XLSX (on demand only ...)`, e, em português, `Concluído sem erros.` (ou o motivo da parada, se o item 13 parou). Anote os números da linha `CAPTCHA summary:` (ou tire uma foto). | | |
| 16 | Abra `C:\HonestPool-v3\data\verified_records.csv` no Excel **sem usar a opção 3**. | O arquivo existe e tem as colunas `Number`, `Start hex`, `End hex` e `Status`, com os mesmos registros mostrados nos itens 14 e 15, cada um uma só vez. O Status aparece como `Проверено` (letras russas legíveis). Feche o Excel. | | |
| 17 | Abra a pasta `C:\HonestPool-v3\data`. | Existe `verified_records.csv`. **Não** existe `verified_records.xlsx` (o Excel só é criado pela opção 4). | | |
| 18 | Digite `5` (Mostrar progresso). | `Progress in range: {'pending': 0, 'processing': 0, 'completed': 2, 'failed': 0}` e `Verified records stored:` com o mesmo número de registros do CSV. Anote esse número. | | |
| 19 | Digite `6` para sair e pressione uma tecla. Abra `iniciar_honestpool.bat` de novo e digite `5`. | O mesmo progresso do item 18: nada foi perdido ao fechar e abrir o programa. | | |

## E. Retomada (não busca páginas novas) e segunda amostra opcional

| Nº | O que fazer | Resultado esperado | OK? | Observações |
|---|---|---|---|---|
| 20 | Opcional, para uma segunda amostra do CAPTCHA: digite `1`, página inicial `3`, página final `5`, número de páginas `3`, e confirme com `S`. Acompanhe como nos itens 13 a 15. Não faça mais execuções do que esta. | As páginas 3 a 5 são processadas como as anteriores e no fim aparece outra linha `CAPTCHA summary: ...`. Anote os números. Se não fez, escreva "não feito". | | |
| 21 | Digite `2` (Retomar). Quando perguntar o número de páginas, pressione **Enter** sem digitar nada. | Antes de perguntar aparece `Pages to process: 0`: as páginas já concluídas **não** são buscadas de novo. O menu volta a aparecer sem abrir o Chromium. | | |
| 22 | Opcional: abra `iniciar_honestpool.bat` numa segunda janela e digite `5` enquanto a primeira janela está no menu; depois feche a segunda janela. | O progresso aparece normalmente. (Se uma extração estiver em andamento na primeira janela, aparece `another process is writing` e a explicação em português; nada é danificado.) | | |

## F. Exportação

| Nº | O que fazer | Resultado esperado | OK? | Observações |
|---|---|---|---|---|
| 23 | Digite `3` (Exportar CSV agora). | O menu mostra `Exportar CSV para C:\HonestPool-v3\data\verified_records.csv` e depois `Exported N verified records to ...\data\verified_records.csv`, com N igual a `Verified records stored:`. | | |
| 24 | Digite `4` (Exportar Excel). | `Exportar XLSX para C:\HonestPool-v3\data\verified_records.xlsx` e `Exported N verified records to ...\data\verified_records.xlsx`, com o mesmo N. Agora o arquivo `verified_records.xlsx` existe. | | |
| 25 | Abra `data\verified_records.xlsx` no Excel. | As colunas `Start hex` e `End hex` mostram 18 dígitos completos (por exemplo `400000000000000000`), e não algo como `4,00001E+17`. | | |
| 26 | Com o arquivo ainda aberto no Excel, digite `4` de novo no menu. | A exportação falha e o menu sugere fechar o arquivo no Excel. Feche o Excel, digite `4` de novo e a exportação funciona. | | |

## G. Registro, caracteres e encerramento

| Nº | O que fazer | Resultado esperado | OK? | Observações |
|---|---|---|---|---|
| 27 | Digite `6` para sair. Se aparecer "Deseja finalizar o arquivo em lotes (S/N)?", responda `S`. | A janela mostra `Menu encerrado.` ou fecha depois de pressionar uma tecla. | | |
| 28 | Abra `C:\HonestPool-v3\logs\honestpool_AAAAMMDD.log` (data de hoje) com o Bloco de Notas. | O arquivo mostra tudo o que apareceu na tela, inclusive as tabelas de registros, com acentos e `Проверено` legíveis. A sua chave **não** aparece. | | |
| 29 | No Gerenciador de Tarefas (Ctrl+Shift+Esc), procure processos do programa. | Não sobra nenhum `node.exe` nem processo do Chromium aberto pelo programa. | | |

## H. Diagnóstico do CAPTCHA no registro

Use o arquivo aberto no item 28. Para procurar um texto, pressione **Ctrl+F** no Bloco de Notas.
Você não precisa entender as linhas: só confira se elas existem e anote o que encontrou.

| Nº | O que fazer | Resultado esperado | OK? | Observações |
|---|---|---|---|---|
| 30 | Procure `CAPTCHA summary`. | Há uma linha `CAPTCHA summary: ...` para cada extração que terminou (itens 15 e 20), com os mesmos números anotados. | | |
| 31 | Procure `attempt 1/3`. | Se apareceu algum CAPTCHA: há linhas `CAPTCHA N attempt 1/3: ...` (por exemplo `image captured`, `image not found`, `answer received from 2Captcha`, `website acceptance confirmed`, `website rejection confirmed` ou `verification outcome uncertain`). Anote quais aparecem. Se não apareceu CAPTCHA, escreva "sem CAPTCHA". | | |
| 32 | Confira que o registro não tem a sua chave. Não cole a chave na busca: apenas confira que, se aparecer, está como `[CHAVE OCULTA]`. | A chave não aparece. As respostas do CAPTCHA também não aparecem (o registro mostra só quantos caracteres a resposta tinha). | | |

---

## O que enviar ao desenvolvedor

Com o menu **fechado**:

- `C:\HonestPool-v3\logs\honestpool_AAAAMMDD.log` (o registro de diagnóstico; envie como está,
  sem editar; veja a seção 11 do guia);
- esta lista preenchida (ou uma foto dela);
- `C:\HonestPool-v3\data\verified_records.csv` e/ou `C:\HonestPool-v3\data\verified_records.xlsx`;
- fotos ou capturas das janelas em que algo foi diferente do esperado;
- se for pedido: uma cópia de `data\honestpool.db` (e de `honestpool.db-wal` e
  `honestpool.db-shm`, se existirem), feita com o menu fechado.

**Nunca envie:** a chave 2Captcha (em nenhum formato), `config\2captcha.cfg`, arquivos `.env`, a
pasta `.venv`, a cópia de segurança da pasta `config` ou senhas. Ninguém precisa da sua chave para
analisar o teste.
