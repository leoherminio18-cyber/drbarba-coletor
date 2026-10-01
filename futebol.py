# -*- coding: utf-8 -*-
"""Robô de futebol da API-Football no GitHub Actions (Terminal Dr. Barba), de hora em hora.

Duas tarefas, nesta ordem de prioridade:

1. RESULTADOS (fluxo do projeto, sempre roda): descobre os jogos encerrados de ontem e hoje
   (/fixtures?date=, 1 requisição por dia consultado) nas ligas de ligas.json e grava, de cada um:
     - o jogo completo (/fixtures?ids=, lotes de 20: placar, eventos, estatística, escalação, jogadores);
     - a estatística por tempo (/fixtures/statistics?half=true, 1 por jogo).
   Revisão: o jogo é pedido de novo ~24 h depois (xG e estatística às vezes chegam atrasados) e,
   só o lote completo, ~72 h depois. Depois disso sai da memória.

2. HISTÓRICO DE 1º/2º TEMPO (sem pressa): percorre tempos_pendentes.json (jogos já encerrados da
   temporada 2024/25 em diante, o mais novo primeiro) com a SOBRA da cota:
     - só pede se o dia (da API, vira 00:00 UTC) ainda tem mais que RESERVA_PROJETO requisições;
     - no máximo MAX_TEMPOS_DIA por dia e MAX_TEMPOS_EXEC por execução.

Grava no repositório PRIVADO de dados (mesmo das odds), pela API do GitHub:
    futebol/jogos/AAAA/MM/DD/HHMMSS.json.gz    resposta crua de /fixtures?ids=  {ids: resposta}
    futebol/tempos/AAAA/MM/DD/HHMMSS.json.gz   {fixture_id: resposta de /fixtures/statistics?half=true}
    futebol/_estado.json                       memória (pendências e ponteiro do histórico)
A memória fica no próprio repositório de dados (não no cache do Actions): não se perde.
"""
import base64
import gzip
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

BASE = 'https://v3.football.api-sports.io'
H = {'x-apisports-key': os.environ.get('APIFOOTBALL_API_KEY', '')}
FIM = ('FT', 'AET', 'PEN')
RESERVA_PROJETO = int(os.environ.get('RESERVA_PROJETO', '3000'))  # nunca deixa o dia com menos que isso
MAX_TEMPOS_DIA = int(os.environ.get('MAX_TEMPOS_DIA', '1500'))
MAX_TEMPOS_EXEC = int(os.environ.get('MAX_TEMPOS_EXEC', '300'))
REVISOES_H = (24, 72)        # horas depois da 1ª coleta; na de 72 h só o lote completo
INFO = {'req': 0, 'usadas': None, 'limite': 7500}
ESTADO = 'futebol/_estado.json'


def log(m):
    print(f'{datetime.now(timezone.utc):%H:%M:%S} {m}', flush=True)


def status():
    r = requests.get(BASE + '/status', headers=H, timeout=30).json()['response']['requests']   # grátis
    INFO['usadas'], INFO['limite'] = int(r['current']), int(r['limit_day'])


def sobra():
    return INFO['limite'] - INFO['usadas']


class SemCota(Exception):
    pass


def get(rota, **params):
    if sobra() <= 50:
        raise SemCota()
    for tentativa in range(3):
        try:
            r = requests.get(BASE + rota, params=params, headers=H, timeout=60)
            INFO['req'] += 1
            INFO['usadas'] = INFO['limite'] - int(r.headers.get('x-ratelimit-requests-remaining') or sobra())
            time.sleep(0.25)
            return r.json()
        except requests.RequestException:
            time.sleep(5 * (tentativa + 1))
    raise RuntimeError(f'falha em {rota}')


# ---------------------------------------------------------------- repositório de dados
def _gh(metodo, caminho, **kw):
    repo, tok = os.environ.get('DADOS_REPO'), os.environ.get('DADOS_TOKEN')
    return requests.request(metodo, f'https://api.github.com/repos/{repo}/contents/{caminho}', timeout=60,
                            headers={'Authorization': f'Bearer {tok}', 'Accept': 'application/vnd.github+json'}, **kw)


def local():
    return not (os.environ.get('DADOS_REPO') and os.environ.get('DADOS_TOKEN'))


def ler_estado():
    if local():
        try:
            return json.load(open(os.path.join('saida_local', ESTADO), encoding='utf-8')), None
        except FileNotFoundError:
            return {}, None
    r = _gh('GET', ESTADO)
    if r.status_code == 404:
        return {}, None
    r.raise_for_status()
    j = r.json()
    return json.loads(base64.b64decode(j['content'])), j['sha']


def enviar(caminho, conteudo, mensagem, sha=None):
    if local():
        os.makedirs(os.path.dirname(os.path.join('saida_local', caminho)), exist_ok=True)
        open(os.path.join('saida_local', caminho), 'wb').write(conteudo)
        return
    for tentativa in range(4):   # 409 = o robô de odds gravou no mesmo instante; tenta de novo
        corpo = {'message': mensagem, 'content': base64.b64encode(conteudo).decode()}
        if sha:
            corpo['sha'] = sha
        r = _gh('PUT', caminho, json=corpo)
        if r.status_code in (200, 201):
            return
        if r.status_code == 409 and caminho == ESTADO:
            sha = _gh('GET', ESTADO).json().get('sha')
        time.sleep(3 * (tentativa + 1))
    raise RuntimeError(f'GitHub {r.status_code}: {r.text[:200]}')


def gz(obj):
    return gzip.compress(json.dumps(obj, ensure_ascii=False, separators=(',', ':')).encode())


# ---------------------------------------------------------------- tarefas
def descobrir(est, ligas):
    """Jogos encerrados de ontem e hoje (UTC) nas nossas ligas, ainda não coletados."""
    hoje = datetime.now(timezone.utc).date()
    novos = 0
    for d in (hoje - timedelta(days=1), hoje):
        for j in get('/fixtures', date=d.isoformat()).get('response') or []:
            fid = str(j['fixture']['id'])
            if j['league']['id'] in ligas and j['fixture']['status']['short'] in FIM and fid not in est['jogos']:
                est['jogos'][fid] = {'liga': j['league']['id'], 'fim': j['fixture']['timestamp'], 'coletas': 0, 'prox': 0}
                novos += 1
    return novos


def coletar_resultados(est):
    """Coleta e revisões que venceram: lote completo (20 por requisição) + estatística por tempo."""
    agora = time.time()
    vez = [fid for fid, g in est['jogos'].items() if g['prox'] <= agora]
    if not vez:
        return 0, {}, {}
    lotes, tempos = {}, {}
    for i in range(0, len(vez), 20):
        ids = vez[i:i + 20]
        lotes['-'.join(ids)] = get('/fixtures', ids='-'.join(ids))
    for fid in vez:
        g = est['jogos'][fid]
        if g['coletas'] < 2:          # 1ª coleta e revisão de 24 h: estatística por tempo também
            tempos[fid] = get('/fixtures/statistics', fixture=fid, half='true')
        g['coletas'] += 1
        if g['coletas'] > len(REVISOES_H):
            del est['jogos'][fid]
        else:
            g['prox'] = agora + REVISOES_H[g['coletas'] - 1] * 3600
    return len(vez), lotes, tempos


def historico_tempos(est, pendentes):
    """Histórico de 1º/2º tempo só com a sobra do dia."""
    t = est.setdefault('tempos', {'ponteiro': 0, 'dia': '', 'gasto_dia': 0})
    dia = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    if t['dia'] != dia:
        t['dia'], t['gasto_dia'] = dia, 0
    feitos = {}
    while (t['ponteiro'] < len(pendentes) and len(feitos) < MAX_TEMPOS_EXEC
           and t['gasto_dia'] < MAX_TEMPOS_DIA and sobra() > RESERVA_PROJETO):
        fid = str(pendentes[t['ponteiro']])
        feitos[fid] = get('/fixtures/statistics', fixture=fid, half='true')
        t['ponteiro'] += 1
        t['gasto_dia'] += 1
    return feitos


def main():
    t0 = time.time()
    status()
    inicio_usadas = INFO['usadas']
    est, sha = ler_estado()
    est.setdefault('jogos', {})
    ligas = set(json.load(open('ligas.json', encoding='utf-8'))['ligas'])
    pendentes = json.load(open('tempos_pendentes.json', encoding='utf-8'))['ids']
    agora = datetime.now(timezone.utc)
    pasta = f'{agora:%Y/%m/%d/%H%M%S}'
    resumo = {'inicio_utc': agora.strftime('%Y-%m-%d %H:%M:%S'), 'status': 'ok', 'erro': ''}
    try:
        resumo['jogos_novos'] = descobrir(est, ligas)
        n, lotes, tempos = coletar_resultados(est)
        resumo['resultados_coletados'] = n
        if lotes:
            enviar(f'futebol/jogos/{pasta}.json.gz', gz(lotes), f'jogos {agora:%Y-%m-%d %H:%M} ({n})')
        hist = historico_tempos(est, pendentes)
        tempos.update(hist)
        resumo['historico_tempos'] = len(hist)
        if tempos:
            enviar(f'futebol/tempos/{pasta}.json.gz', gz(tempos), f'tempos {agora:%Y-%m-%d %H:%M} ({len(tempos)})')
    except SemCota:
        resumo['status'] = 'sem_cota'
    except Exception as e:  # noqa: BLE001
        resumo['status'], resumo['erro'] = 'erro', repr(e)[:300]
    t = est.get('tempos', {})
    resumo.update({'requisicoes': INFO['req'], 'usadas_dia_antes': inicio_usadas, 'usadas_dia_depois': INFO['usadas'],
                   'jogos_na_memoria': len(est['jogos']),
                   'historico_feito': f"{t.get('ponteiro', 0)} de {len(pendentes)}",
                   'duracao_s': round(time.time() - t0)})
    log(json.dumps(resumo, ensure_ascii=False))
    if resumo['status'] == 'sem_cota':   # dia sem cota: não grava nada, tenta na próxima hora
        return
    if resumo['status'] == 'erro':
        # memória NÃO é gravada: a próxima execução refaz o que ficou no meio (nada se perde)
        sys.exit(1)
    est['ultima_execucao'] = resumo
    enviar(ESTADO, json.dumps(est, ensure_ascii=False, indent=0).encode(), f'estado {agora:%Y-%m-%d %H:%M}', sha)


if __name__ == '__main__':
    main()
