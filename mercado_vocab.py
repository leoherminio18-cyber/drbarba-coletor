# -*- coding: utf-8 -*-
"""Vocabulário da base de mercado — SEM dependências (só a biblioteca padrão).

É a fonte única do vocabulário: `drbarba/mercado.py` (PC) importa daqui, e o coletor da
nuvem (repositório drbarba-coletor no GitHub) recebe uma CÓPIA deste arquivo por
`backend/manutencao/publicar_coletor_github.py`. Mudou aqui -> publicar de novo.
Ver `drbarba/mercado.py` para a explicação de cada campo.
"""
import re

COLUNAS = ['jogo_data', 'id_fs', 'id_af', 'liga', 'mandante', 'visitante', 'casa', 'mercado', 'periodo',
           'linha', 'selecao', 'odd', 'momento', 'coletado_em', 'horas_ate_jogo', 'atualizado_casa', 'fonte', 'obs']
CHAVE = ['casa', 'mercado', 'periodo', 'linha', 'selecao']

# pasta -> nomes da casa em cada fonte
CASAS = {
    'pinnacle': {'nome': 'Pinnacle', 'fd': ['PS', 'P'], 'fs': 'pin', 'af': 'Pinnacle'},
    'bet365': {'nome': 'Bet365', 'fd': ['B365'], 'fs': 'b365', 'af': 'Bet365'},
    '1xbet': {'nome': '1xBet', 'fd': ['1XB'], 'fs': '1x', 'af': '1xBet'},
    'betfair': {'nome': 'Betfair', 'fd': ['BF', 'BFE'], 'fs': 'bf', 'af': 'Betfair'},
    'betano': {'nome': 'Betano', 'fd': [], 'fs': None, 'af': 'Betano'},
}
PASTA_DA_CASA_AF = {v['af']: k for k, v in CASAS.items()}

# API-Football: id do mercado -> (mercado, periodo)
AF_MERCADOS = {
    1: ('1x2', 'jogo'), 13: ('1x2', '1t'), 3: ('1x2', '2t'),
    12: ('dupla_chance', 'jogo'), 20: ('dupla_chance', '1t'), 33: ('dupla_chance', '2t'),
    5: ('gols_ou', 'jogo'), 6: ('gols_ou', '1t'), 26: ('gols_ou', '2t'),
    4: ('ah', 'jogo'), 19: ('ah', '1t'), 104: ('ah', '2t'),
    8: ('ambas', 'jogo'), 34: ('ambas', '1t'), 35: ('ambas', '2t'),
    10: ('placar', 'jogo'), 31: ('placar', '1t'), 62: ('placar', '2t'),
    16: ('gols_time_casa_ou', 'jogo'), 17: ('gols_time_fora_ou', 'jogo'),
    105: ('gols_time_casa_ou', '1t'), 106: ('gols_time_fora_ou', '1t'),
    107: ('gols_time_casa_ou', '2t'), 108: ('gols_time_fora_ou', '2t'),
    45: ('escanteios_ou', 'jogo'), 77: ('escanteios_ou', '1t'), 127: ('escanteios_ou', '2t'),
    56: ('escanteios_ah', 'jogo'), 80: ('cartoes_ou', 'jogo'), 7: ('ht_ft', 'jogo'),
}
_SEL = {'home': 'casa', 'draw': 'empate', 'away': 'fora', 'over': 'over', 'under': 'under', 'yes': 'sim', 'no': 'nao',
        'home/draw': '1x', 'home/away': '12', 'draw/away': 'x2'}
NAN = float('nan')


def _slug(s):
    return re.sub(r'[^a-z0-9]+', '_', str(s).lower()).strip('_')[:40]


def normalizar_af(bet_id, bet_nome, valor):
    """(mercado, periodo, linha, selecao) a partir de um valor da API-Football."""
    v = str(valor).strip()
    if bet_id not in AF_MERCADOS:
        return f'af{bet_id}_{_slug(bet_nome)}', 'jogo', NAN, v
    mercado, periodo = AF_MERCADOS[bet_id]
    if mercado in ('placar', 'ht_ft'):
        return mercado, periodo, NAN, v.replace('/', '-').lower() if mercado == 'ht_ft' else v
    m = re.match(r'^(Home|Away|Over|Under|Draw|Yes|No)\s*([+-]?\d+(?:\.\d+)?)?$', v, re.I)
    if m:
        sel = _SEL[m.group(1).lower()]
        linha = float(m.group(2)) if m.group(2) is not None else NAN
        return mercado, periodo, linha, sel
    return mercado, periodo, NAN, _SEL.get(v.lower(), v)
