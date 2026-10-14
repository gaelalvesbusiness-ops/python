# Exemplos de configuração

Todas as configurações são opções de linha de comando, exceto as três variáveis do 2Captcha
(`CAPTCHA_PROVIDER`, `CAPTCHA_API_KEY`, `CAPTCHA_TIMEOUT`), lidas do ambiente do terminal. No
Windows 11 elas são definidas por `configurar_2captcha.bat` e passadas pelo menu
`iniciar_honestpool.bat` (`INSTALACAO_WINDOWS_11.md`); no Linux, o modelo está em `.env.example`
(seção 9A do guia). Os comandos abaixo são para uso avançado no terminal; no Windows, o menu já
usa os valores recomendados. Os perfis abaixo só acessam o site quando
`--live` está presente, e nunca baixam mais que `--max-pages` páginas por execução.

| Perfil | Comando |
|---|---|
| Planejar, sem acessar o site | `python main.py --start-page 350000 --end-page 350100` |
| Teste de uma página (padrão) | `python main.py --start-page 350000 --end-page 350100 --live` |
| Retomar, recomendado | `python main.py --resume --live --max-pages 20 --nav-timeout 60` |
| Conservador | `python main.py --resume --live --max-pages 20 --delay 30 --jitter 10 --nav-timeout 60` |
| Servidor lento | `python main.py --resume --live --max-pages 10 --nav-timeout 90 --retries 1` |
| Sem novas tentativas automáticas | `python main.py --resume --live --max-pages 5 --nav-timeout 60 --retries 0` |
| Ver progresso e páginas com falha, sem acessar o site | `python main.py --resume` |
| Repetir páginas puladas após 3 tentativas | `python main.py --resume --live --max-pages 5 --max-attempts 4` |
| Conferir a configuração do 2Captcha | `python main.py --captcha-status` |
| Sem janela, com 2Captcha (para se o CAPTCHA não for resolvido) | `python main.py --resume --live --max-pages 20 --headless` |
| Exportar | `python main.py --export csv` / `python main.py --export xlsx` |
| Exportar para outro arquivo | `python main.py --export xlsx --output relatorio/verificados.xlsx` |
| Outro banco de dados | `python main.py --db /caminho/projeto.db --resume --live` |
| Demonstração offline | `python demo.py --csv examples/verified_records.csv --xlsx examples/verified_records.xlsx` |

O pacote de teste do Windows 11 não traz `examples/verified_records.*` (registros reais); nele, use
`python demo.py` depois de uma extração de teste.

## Valores padrão e limites

| Opção | Padrão | Limites |
|---|---|---|
| `--delay` | 15 s | mínimo 5 s |
| `--jitter` | 5 s | 0 ou mais |
| `--nav-timeout` | 30 s | 5 a 300 s (60 s recomendado) |
| `--retries` | 2 | 0 a 5 |
| `--captcha-wait` | 600 s | maior que 0 |
| `--max-pages` | 1 | 1 ou mais |
| `--max-attempts` | 3 | 1 ou mais |
| `--max-manual-verifications` | 10 por sessão do navegador | 1 a 50 |
| `--headless` | desligado | um CAPTCHA que o 2Captcha não resolve encerra a execução |
| `CAPTCHA_TIMEOUT` | 120 s | 10 a 600 s |

## Códigos de saída

| Código | Significado |
|---|---|
| 0 | Concluído |
| 1 | Outra instância está usando o banco de dados |
| 2 | Opções inválidas ou configuração do 2Captcha inválida |
| 3 | Execução interrompida com segurança (CAPTCHA não resolvido, acesso negado, limite 429 ou 3 páginas seguidas com falha) |
| 4 | Importação recusada (nada foi gravado) |
| 130 | Interrompido com Ctrl+C |

Após os códigos 3 e 130, continue com `python main.py --resume --live`.
