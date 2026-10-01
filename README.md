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

- `futebol.py` + `.github/workflows/futebol.yml` — robô de futebol, de hora em hora: jogos encerrados
  (jogo completo + estatística por tempo, revisão em 24 h e 72 h) e, com a sobra da cota do dia
  (dia com mais de 3.000 livres; até 1.500 por dia), o histórico de 1º/2º tempo de `tempos_pendentes.json`.
  Memória em `futebol/_estado.json` no repositório de dados.

Secrets: `APIFOOTBALL_API_KEY`, `DADOS_TOKEN`. Variável: `DADOS_REPO`.
