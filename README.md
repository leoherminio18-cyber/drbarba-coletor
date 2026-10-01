# drbarba-coletor

Coletor de odds pré-jogo da API-Football (Terminal Dr. Barba), rodando no GitHub Actions
de 15 em 15 minutos. Grava a movimentação das odds de 5 casas (Pinnacle, Bet365, 1xBet,
Betfair, Betano), em todos os mercados, num repositório de dados privado.

- `coletor.py` — a coleta (varredura de 3 em 3 h, jogos a < 3 h do apito a cada execução,
  fechamento a ≤ 20 min do apito; só grava o que mudou).
- `mercado_vocab.py` — vocabulário dos mercados (cópia; a fonte fica no projeto principal).
- `ligas.json` — ligas acompanhadas.
- `.github/workflows/coletor.yml` — agendamento; `manter_ativo.yml` — commit semanal para o
  GitHub não desligar o agendamento.

Secrets: `APIFOOTBALL_API_KEY`, `DADOS_TOKEN`. Variável: `DADOS_REPO`.
