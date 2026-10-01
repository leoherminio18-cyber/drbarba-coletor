# -*- coding: utf-8 -*-
"""Coletor de odds da API-Football rodando no GitHub Actions (Terminal Dr. Barba).

Versão nuvem de backend/coleta/coletor_odds_af.py (mesma regra; ver lá). Diferenças:
  - sem pandas (instala em segundos): só `requests` + biblioteca padrão;
  - não conhece a base da FootyStats: grava só o id_af. O id_fs é preenchido no PC, na
    importação (backend/coleta/importar_odds_github.py);
  - cada execução grava UM arquivo pequeno só com o que mudou, no repositório PRIVADO de
    dados, pela API do GitHub: dados/AAAA/MM/DD/HHMMSS.csv.gz;
  - a memória (última odd vista de cada jogo) fica em estado/estado.json.gz, que o
    workflow guarda no cache do Actions entre uma execução e a outra. Se o cache se
    perder, a execução seguinte regrava tudo como 'abertura' e a importação no PC tira a
    repetição (só entra o que muda).

Variáveis de ambiente (secrets do repositório):
  APIFOOTBALL_API_KEY   chave da API-Football
  DADOS_TOKEN           token com permissão de escrita no repositório de dados
  DADOS_REPO            dono/nome do repositório de dados (ex.: leo/drbarba-odds)
"""
import base64
import csv
import gzip
import io
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

from mercado_vocab import CASAS, COLUNAS, PASTA_DA_CASA_AF, normalizar_af

BASE = 'https://v3.football.api-sports.io'
H = {'x-apisports-key': os.environ.get('APIFOOTBALL_API_KEY', '')}
ESTADO = 'estado/estado.json.gz'
VARREDURA_H, PERTO_H, FECHA_MIN = 3, 3, 20
RESERVA = int(os.environ.get('RESERVA', '800'))
INFO = {'req': 0, 'resta': None}


class CotaAcabou(Exception):
    pass


def log(m):
    print(f'{datetime.now(timezone.utc):%H:%M:%S} {m}', flush=True)


def get(rota, **params):
    if INFO['resta'] is not None and INFO['resta'] <= RESERVA:
        raise CotaAcabou()
    r = requests.get(BASE + rota, params=params, headers=H, timeout=60)
    INFO['req'] += 1
    INFO['resta'] = int(r.headers.get('x-ratelimit-requests-remaining') or 0)
    time.sleep(0.25)
    return r.json()


def carregar_estado():
    if os.path.exists(ESTADO):
        with gzip.open(ESTADO, 'rt', encoding='utf-8') as f:
            return json.load(f)
    return {'ultima_varredura': 0, 'temporadas': {}, 'jogos': {}}


def salvar_estado(e):
    os.makedirs(os.path.dirname(ESTADO), exist_ok=True)
    with gzip.open(ESTADO, 'wt', encoding='utf-8') as f:
        json.dump(e, f, ensure_ascii=False, separators=(',', ':'))


def temporada(est, lid):
    t = est['temporadas'].get(str(lid))
    if t and time.time() - t['em'] < 86400:
        return t['ano']
    r = get('/leagues', id=lid)['response']
    ano = next((s['year'] for s in r[0]['seasons'] if s['current']), None) if r else None
    est['temporadas'][str(lid)] = {'ano': ano, 'em': time.time()}
    return ano


def registrar_jogos(est, fixtures):
    agora = time.time()
    for j in fixtures:
        if j['fixture']['status']['short'] not in ('NS', 'TBD') or j['fixture']['timestamp'] < agora:
            continue
        g = est['jogos'].setdefault(str(j['fixture']['id']), {'odds': {}, 'fech': False})
        g.update({'ts': j['fixture']['timestamp'], 'liga_id': j['league']['id'],
                  'liga': f"{j['league']['country']} · {j['league']['name']}",
                  'mandante': j['teams']['home']['name'], 'visitante': j['teams']['away']['name'],
                  'casa_id_af': j['teams']['home']['id'], 'fora_id_af': j['teams']['away']['id']})


def linhas_do_jogo(est, jo, fechamento):
    fid = str(jo['fixture']['id'])
    g = est['jogos'].get(fid)
    if not g:
        return []
    agora = datetime.now(timezone.utc)
    horas = round((g['ts'] - agora.timestamp()) / 3600, 2)
    if horas <= 0:
        return []
    base = {'jogo_data': datetime.fromtimestamp(g['ts'], timezone.utc).strftime('%Y-%m-%d %H:%M'), 'id_fs': '',
            'id_af': fid, 'liga': g.get('liga'), 'mandante': g.get('mandante'), 'visitante': g.get('visitante'),
            'coletado_em': agora.strftime('%Y-%m-%d %H:%M:%S'), 'horas_ate_jogo': horas,
            'atualizado_casa': jo.get('update'), 'fonte': 'af', 'obs': ''}
    out = []
    for bk in jo['bookmakers']:
        if bk['name'] not in PASTA_DA_CASA_AF:
            continue
        nome = CASAS[PASTA_DA_CASA_AF[bk['name']]]['nome']
        for bet in bk['bets']:
            for v in bet['values']:
                try:
                    odd = float(v['odd'])
                except (TypeError, ValueError):
                    continue
                if odd <= 1:
                    continue
                merc, per, lin, sel = normalizar_af(bet['id'], bet['name'], v['value'])
                chave = f'{nome}|{merc}|{per}|{lin}|{sel}'
                antes = g['odds'].get(chave)
                if fechamento:
                    momento = 'fechamento'
                elif antes is None:
                    momento = 'abertura'
                elif antes != odd:
                    momento = 'foto'
                else:
                    continue
                g['odds'][chave] = odd
                out.append({**base, 'casa': nome, 'mercado': merc, 'periodo': per,
                            'linha': '' if lin != lin else lin, 'selecao': sel, 'odd': odd, 'momento': momento})
    if fechamento:
        g['fech'] = True
    return out


def enviar(caminho, conteudo, mensagem):
    """Cria um arquivo no repositório de dados pela API do GitHub (um commit, sem clonar)."""
    repo, tok = os.environ.get('DADOS_REPO'), os.environ.get('DADOS_TOKEN')
    if not repo or not tok:            # teste local: grava em saida_local/
        os.makedirs(os.path.dirname(os.path.join('saida_local', caminho)), exist_ok=True)
        with open(os.path.join('saida_local', caminho), 'wb') as f:
            f.write(conteudo)
        return
    r = requests.put(f'https://api.github.com/repos/{repo}/contents/{caminho}',
                     headers={'Authorization': f'Bearer {tok}', 'Accept': 'application/vnd.github+json'},
                     json={'message': mensagem, 'content': base64.b64encode(conteudo).decode()}, timeout=60)
    if r.status_code not in (200, 201):
        raise RuntimeError(f'GitHub {r.status_code}: {r.text[:200]}')


def teste():
    """Confere os dois secrets sem gastar cota: chave da API (/status) e escrita no repositório de dados."""
    st = requests.get(BASE + '/status', headers=H, timeout=30).json()
    if st.get('errors'):
        raise SystemExit(f'chave da API recusada: {st["errors"]}')
    r = st['response']
    log(f"API-Football ok: plano {r['subscription']['plan']}, ativo={r['subscription']['active']}, "
        f"usadas hoje {r['requests']['current']} de {r['requests']['limit_day']}")
    agora = datetime.now(timezone.utc)
    enviar(f'teste/{agora:%Y%m%d_%H%M%S}.txt', f'teste de escrita {agora:%Y-%m-%d %H:%M:%S} UTC\n'.encode(),
           'teste de escrita do coletor')
    log(f"escrita no repositório de dados ok ({os.environ.get('DADOS_REPO')})")


def main():
    if os.environ.get('TESTE'):
        return teste()
    est = carregar_estado()
    ligas = json.load(open('ligas.json', encoding='utf-8'))['ligas']
    t0, linhas, status, erro, varreu = time.time(), [], 'ok', '', False
    try:
        try:   # /status não gasta cota
            st = requests.get(BASE + '/status', headers=H, timeout=30).json()['response']['requests']
            INFO['resta'] = int(st['limit_day']) - int(st['current'])
        except Exception:  # noqa: BLE001
            pass
        if time.time() - est['ultima_varredura'] >= VARREDURA_H * 3600 or os.environ.get('FORCAR_VARREDURA'):
            hoje = datetime.now(timezone.utc).date()
            for lid in ligas:
                ano = temporada(est, lid)
                if not ano:
                    continue
                fx = get('/fixtures', league=lid, season=ano,
                         **{'from': hoje.isoformat(), 'to': (hoje + timedelta(days=8)).isoformat()})
                registrar_jogos(est, fx.get('response') or [])
                pag, total = 1, 1
                while pag <= total:
                    r = get('/odds', league=lid, season=ano, page=pag)
                    total = r.get('paging', {}).get('total', 1)
                    for jo in r.get('response') or []:
                        linhas += linhas_do_jogo(est, jo, False)
                    pag += 1
            est['ultima_varredura'] = time.time()
            varreu = True
        agora = time.time()
        for fid, g in list(est['jogos'].items()):
            falta = g.get('ts', 0) - agora
            if falta <= 0 or falta > PERTO_H * 3600 or g.get('fech'):
                continue
            fecha = falta <= FECHA_MIN * 60
            if not fecha and varreu:
                continue
            r = get('/odds', fixture=fid)
            for jo in r.get('response') or []:
                linhas += linhas_do_jogo(est, jo, fecha)
    except CotaAcabou:
        status = 'reserva'
    except Exception as e:  # noqa: BLE001
        status, erro = 'erro', repr(e)[:300]
    agora = datetime.now(timezone.utc)
    if linhas:
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode='wb') as gz:
            txt = io.TextIOWrapper(gz, encoding='utf-8', newline='')
            w = csv.DictWriter(txt, fieldnames=COLUNAS)
            w.writeheader()
            w.writerows(linhas)
            txt.flush()
        enviar(f'dados/{agora:%Y/%m/%d/%H%M%S}.csv.gz', buf.getvalue(), f'odds {agora:%Y-%m-%d %H:%M} ({len(linhas)} linhas)')
        # ids dos times dos jogos desta execução: é com eles que o PC liga o jogo ao id da FootyStats
        ids = sorted({l['id_af'] for l in linhas})
        jogos = [{'id_af': i, 'ts': est['jogos'][i]['ts'], 'liga_id': est['jogos'][i].get('liga_id'),
                  'casa_id_af': est['jogos'][i].get('casa_id_af'), 'fora_id_af': est['jogos'][i].get('fora_id_af')}
                 for i in ids if i in est['jogos']]
        enviar(f'dados/{agora:%Y/%m/%d/%H%M%S}_jogos.json', json.dumps(jogos).encode(), f'jogos {agora:%Y-%m-%d %H:%M}')
    corte = time.time() - 86400
    est['jogos'] = {k: v for k, v in est['jogos'].items() if v.get('ts', 0) > corte}
    salvar_estado(est)
    resumo = {'inicio_utc': datetime.fromtimestamp(t0, timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
              'duracao_s': round(time.time() - t0), 'status': status, 'varredura': varreu,
              'requisicoes': INFO['req'], 'cota_resta': INFO['resta'], 'linhas': len(linhas),
              'jogos_no_estado': len(est['jogos']), 'erro': erro}
    log(json.dumps(resumo, ensure_ascii=False))
    with open('ultima_execucao.json', 'w', encoding='utf-8') as f:
        json.dump(resumo, f, ensure_ascii=False, indent=1)
    if status == 'erro':
        sys.exit(1)


if __name__ == '__main__':
    main()
