# -*- coding: utf-8 -*-
"""Gravador ao vivo da API-Football (Terminal Dr. Barba): uma foto por minuto de cada jogo rolando
nas ligas de ligas.json — estatística dos times, eventos e odds ao vivo dos mercados principais.

Base do gráfico de pressão (índice de domínio) e dos estudos do live. Grava SÓ o que mudou desde a
foto anterior (como o coletor de odds), em pacotes a cada PACOTE_MIN minutos, no repositório PRIVADO
de dados:
    aovivo/estat/AAAA/MM/DD/HHMMSS.csv.gz    1 linha por jogo × time quando algum número muda
    aovivo/eventos/AAAA/MM/DD/HHMMSS.csv.gz  1 linha por evento novo (gol, cartão, substituição, VAR)
    aovivo/odds/AAAA/MM/DD/HHMMSS.csv.gz     1 linha por cotação que mudou (mercados principais)
A hora gravada é a do servidor da API (cabeçalho Date), não a do relógio da máquina.

Roda em loop por até DURACAO_MIN minutos e sai (o GitHub encerra trabalhos com 6 h); o workflow
dispara de novo e a fila emenda uma execução na outra. Ao recomeçar, a 1ª foto de cada jogo é gravada
inteira (a memória do que já foi visto não atravessa execuções, e não precisa).

Requisições por minuto com jogo rolando: 1 (/fixtures?live=) + 1 por 20 jogos (/fixtures?ids=, se a
lista ao vivo não trouxer a estatística) + 1 (/odds/live). Para com RESERVA livres no dia.
"""
import base64
import csv
import email.utils
import gzip
import io
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests

BASE = 'https://v3.football.api-sports.io'
H = {'x-apisports-key': os.environ.get('APIFOOTBALL_API_KEY', '')}
INTERVALO_S = int(os.environ.get('INTERVALO_S', '60'))
PACOTE_MIN = int(os.environ.get('PACOTE_MIN', '15'))
DURACAO_MIN = int(os.environ.get('DURACAO_MIN', '340'))
RESERVA = int(os.environ.get('RESERVA', '800'))
VIVO = {'1H', 'HT', '2H', 'ET', 'BT', 'P', 'INT', 'LIVE', 'SUSP'}

EST = {'Shots on Goal': 'alvo', 'Shots off Goal': 'fora_alvo', 'Total Shots': 'chutes', 'Blocked Shots': 'bloqueados',
       'Shots insidebox': 'chutes_area', 'Shots outsidebox': 'chutes_fora_area', 'Fouls': 'faltas',
       'Corner Kicks': 'escanteios', 'Offsides': 'impedimentos', 'Ball Possession': 'posse',
       'Yellow Cards': 'amarelos', 'Red Cards': 'vermelhos', 'Goalkeeper Saves': 'defesas',
       'Total passes': 'passes', 'Passes accurate': 'passes_certos', 'Free Kicks': 'tiros_livres',
       'expected_goals': 'xg'}
# somados dos jogadores (o índice de domínio usa os passes decisivos)
JOG = {'passes_decisivos': ('passes', 'key'), 'desarmes': ('tackles', 'total'), 'duelos': ('duels', 'total'),
       'duelos_ganhos': ('duels', 'won'), 'dribles': ('dribbles', 'attempts'), 'dribles_certos': ('dribbles', 'success')}
COL_EST = (['servidor', 'fixture', 'liga', 'status', 'minuto', 'acrescimo', 'lado', 'time_id', 'gols']
           + list(dict.fromkeys(EST.values())) + list(JOG))
COL_EV = ['servidor', 'fixture', 'minuto', 'acrescimo', 'lado', 'time_id', 'tipo', 'detalhe', 'jogador', 'assistencia']
COL_ODDS = ['servidor', 'fixture', 'minuto', 'segundos', 'mercado_id', 'mercado', 'selecao', 'linha', 'principal',
            'odd', 'suspensa', 'parado', 'bloqueado']
# mercados principais do ao vivo (ids da API-Football, medidos no teste de 30/09/2026)
MERCADOS = {59, 36, 25, 33, 69, 19, 17, 20, 37, 32}
PROXIMO_GOL = re.compile(r'Which team will score the \d+\w* goal', re.I)
INFO = {'req': 0, 'resta': None, 'servidor': None}


def log(m):
    print(f'{datetime.now(timezone.utc):%H:%M:%S} {m}', flush=True)


class SemCota(Exception):
    pass


def get(rota, **params):
    if INFO['resta'] is not None and INFO['resta'] <= RESERVA:
        raise SemCota()
    for tentativa in range(3):
        try:
            r = requests.get(BASE + rota, params=params, headers=H, timeout=60)
            INFO['req'] += 1
            INFO['resta'] = int(r.headers.get('x-ratelimit-requests-remaining') or INFO['resta'] or 0)
            if 'Date' in r.headers:
                INFO['servidor'] = email.utils.parsedate_to_datetime(r.headers['Date']).astimezone(timezone.utc)
            return r.json()
        except requests.RequestException:
            time.sleep(3 * (tentativa + 1))
    return {}


def _num(v):
    if v is None or v == '':
        return None
    try:
        return float(str(v).rstrip('%'))
    except ValueError:
        return None


def linhas_estat(j, servidor):
    f, casa = j['fixture'], j['teams']['home']['id']
    base = {'servidor': servidor, 'fixture': f['id'], 'liga': j['league']['id'], 'status': f['status']['short'],
            'minuto': f['status'].get('elapsed'), 'acrescimo': f['status'].get('extra')}
    out = {}
    for lado, tid, g in (('c', casa, j['goals']['home']), ('f', j['teams']['away']['id'], j['goals']['away'])):
        out[lado] = {**base, 'lado': lado, 'time_id': tid, 'gols': g}
    for st in j.get('statistics') or []:
        lado = 'c' if st['team']['id'] == casa else 'f'
        for s in st['statistics']:
            if s['type'] in EST:
                out[lado][EST[s['type']]] = _num(s['value'])
    for tm in j.get('players') or []:
        lado = 'c' if tm['team']['id'] == casa else 'f'
        for nome, (a, b) in JOG.items():
            out[lado][nome] = sum(((p['statistics'][0].get(a) or {}).get(b) or 0) for p in tm['players'])
    return out


def chave_ev(e):
    return f"{e['time'].get('elapsed')}|{e['time'].get('extra')}|{e['type']}|{e.get('detail')}|{(e.get('player') or {}).get('id')}"


class Gravador:
    def __init__(self, ligas):
        self.ligas = ligas
        self.ult_est, self.ult_odd, self.evs = {}, {}, {}
        self.buf = {'estat': [], 'eventos': [], 'odds': []}

    def foto(self):
        servidor = None
        vivos = get('/fixtures', live='-'.join(map(str, sorted(self.ligas)))).get('response') or []
        vivos = [j for j in vivos if j['league']['id'] in self.ligas and j['fixture']['status']['short'] in VIVO]
        servidor = INFO['servidor'].strftime('%Y-%m-%d %H:%M:%S') if INFO['servidor'] else ''
        if not vivos:
            return 0
        # a lista ao vivo nem sempre traz estatística e jogadores: completa por /fixtures?ids= (20 por vez)
        if any(not j.get('statistics') for j in vivos):
            completos = {}
            ids = [str(j['fixture']['id']) for j in vivos]
            for i in range(0, len(ids), 20):
                for j in get('/fixtures', ids='-'.join(ids[i:i + 20])).get('response') or []:
                    completos[j['fixture']['id']] = j
            vivos = [completos.get(j['fixture']['id'], j) for j in vivos]
        for j in vivos:
            fid = j['fixture']['id']
            for lado, l in linhas_estat(j, servidor).items():
                assinatura = {k: v for k, v in l.items() if k not in ('servidor', 'minuto', 'acrescimo')}
                if self.ult_est.get((fid, lado)) != assinatura:
                    self.ult_est[(fid, lado)] = assinatura
                    self.buf['estat'].append(l)
            casa = j['teams']['home']['id']
            vistos = self.evs.setdefault(fid, set())
            for e in j.get('events') or []:
                k = chave_ev(e)
                if k in vistos:
                    continue
                vistos.add(k)
                tid = (e.get('team') or {}).get('id')
                self.buf['eventos'].append({'servidor': servidor, 'fixture': fid, 'minuto': e['time'].get('elapsed'),
                                            'acrescimo': e['time'].get('extra'), 'lado': 'c' if tid == casa else 'f',
                                            'time_id': tid, 'tipo': e['type'], 'detalhe': e.get('detail'),
                                            'jogador': (e.get('player') or {}).get('name'),
                                            'assistencia': (e.get('assist') or {}).get('name')})
        vivos_ids = {j['fixture']['id'] for j in vivos}
        self.odds(vivos_ids)
        # esquece jogo que saiu da lista ao vivo (acabou): a memória não cresce
        self.evs = {k: v for k, v in self.evs.items() if k in vivos_ids}
        self.ult_est = {k: v for k, v in self.ult_est.items() if k[0] in vivos_ids}
        self.ult_odd = {k: v for k, v in self.ult_odd.items() if k[0] in vivos_ids}
        return len(vivos)

    def odds(self, fids):
        for o in get('/odds/live').get('response') or []:
            fid = o['fixture']['id']
            if fid not in fids:
                continue
            servidor = INFO['servidor'].strftime('%Y-%m-%d %H:%M:%S') if INFO['servidor'] else ''
            st = o.get('status') or {}
            for b in o.get('odds') or []:
                if b['id'] not in MERCADOS and not PROXIMO_GOL.search(b.get('name') or ''):
                    continue
                for v in b['values']:
                    k = (fid, b['id'], b['name'], v.get('value'), v.get('handicap'))
                    estado = (v.get('odd'), v.get('suspended'), st.get('stopped'), st.get('blocked'))
                    if self.ult_odd.get(k) == estado:
                        continue
                    self.ult_odd[k] = estado
                    self.buf['odds'].append({'servidor': servidor, 'fixture': fid,
                                             'minuto': o['fixture']['status'].get('elapsed'),
                                             'segundos': o['fixture']['status'].get('seconds'),
                                             'mercado_id': b['id'], 'mercado': b['name'], 'selecao': v.get('value'),
                                             'linha': v.get('handicap'), 'principal': v.get('main'), 'odd': v.get('odd'),
                                             'suspensa': v.get('suspended'), 'parado': st.get('stopped'),
                                             'bloqueado': st.get('blocked')})

    def descarregar(self):
        agora = datetime.now(timezone.utc)
        for tipo, cols in (('estat', COL_EST), ('eventos', COL_EV), ('odds', COL_ODDS)):
            linhas = self.buf[tipo]
            if not linhas:
                continue
            bio = io.BytesIO()
            with gzip.GzipFile(fileobj=bio, mode='wb') as gzf:
                txt = io.TextIOWrapper(gzf, encoding='utf-8', newline='')
                w = csv.DictWriter(txt, fieldnames=cols, extrasaction='ignore')
                w.writeheader()
                w.writerows(linhas)
                txt.flush()
            enviar(f'aovivo/{tipo}/{agora:%Y/%m/%d/%H%M%S}.csv.gz', bio.getvalue(),
                   f'ao vivo {tipo} {agora:%Y-%m-%d %H:%M} ({len(linhas)})')
            self.buf[tipo] = []


def enviar(caminho, conteudo, mensagem):
    repo, tok = os.environ.get('DADOS_REPO'), os.environ.get('DADOS_TOKEN')
    if not repo or not tok:
        os.makedirs(os.path.dirname(os.path.join('saida_local', caminho)), exist_ok=True)
        open(os.path.join('saida_local', caminho), 'wb').write(conteudo)
        return
    for tentativa in range(4):
        r = requests.put(f'https://api.github.com/repos/{repo}/contents/{caminho}', timeout=60,
                         headers={'Authorization': f'Bearer {tok}', 'Accept': 'application/vnd.github+json'},
                         json={'message': mensagem, 'content': base64.b64encode(conteudo).decode()})
        if r.status_code in (200, 201):
            return
        time.sleep(3 * (tentativa + 1))
    log(f'AVISO: não gravou {caminho} ({r.status_code}: {r.text[:150]})')


def main():
    ligas = set(json.load(open('ligas.json', encoding='utf-8'))['ligas'])
    try:   # /status é grátis
        st = requests.get(BASE + '/status', headers=H, timeout=30).json()['response']['requests']
        INFO['resta'] = int(st['limit_day']) - int(st['current'])
    except Exception:  # noqa: BLE001
        pass
    g = Gravador(ligas)
    t0 = ult_pacote = time.time()
    fotos = jogos_max = 0
    status = 'ok'
    try:
        while time.time() - t0 < DURACAO_MIN * 60:
            ini = time.time()
            n = g.foto()
            fotos += 1 if n else 0
            jogos_max = max(jogos_max, n)
            if time.time() - ult_pacote >= PACOTE_MIN * 60:
                g.descarregar()
                ult_pacote = time.time()
            # sem jogo rolando: consulta a cada 5 min (a lista ao vivo é 1 requisição)
            espera = INTERVALO_S if n else 300
            time.sleep(max(1, espera - (time.time() - ini)))
    except SemCota:
        status = 'sem_cota'
    finally:
        g.descarregar()
    log(json.dumps({'status': status, 'minutos': round((time.time() - t0) / 60), 'fotos_com_jogo': fotos,
                    'max_jogos_simultaneos': jogos_max, 'requisicoes': INFO['req'], 'cota_resta': INFO['resta']},
                   ensure_ascii=False))


if __name__ == '__main__':
    main()
