"""Rede: rota pela Cloudflare pros hosts do GameBanana e download com troca de espelho.

A rota da operadora até os servidores de arquivos/imagens do GameBanana perde
muito pacote, mas a Cloudflare (que serve gamebanana.com) também atende
images/files/filecacheNN.gamebanana.com com certificado válido. Então o DNS
desses hosts devolve primeiro os IPs da Cloudflare e depois os normais (o
urllib3 tenta na ordem, então cai no normal sozinho se a Cloudflare falhar).
"""
import hashlib
import re
import socket
import sys
import threading
import time
from urllib.parse import urlsplit, urlunsplit

import requests

from . import __version__

GB_ASSETS = re.compile(r"^(images|files|filecache\d+)\.gamebanana\.com$", re.I)
# Espelhos que se mostraram rápidos pela Cloudflare; o sorteado pelo site entra no fim.
FAST_MIRRORS = ["filecache40", "filecache30", "filecache20"]
SLOW_BPS = 400_000      # abaixo disso depois do período de teste, troca de espelho
PROBE_SECONDS = 4

_orig_getaddrinfo = socket.getaddrinfo


def _getaddrinfo(host, *args, **kwargs):
    if isinstance(host, str) and GB_ASSETS.match(host):
        try:
            cf = [a for a in _orig_getaddrinfo("gamebanana.com", *args, **kwargs) if a[0] == socket.AF_INET]
        except OSError:
            cf = []
        try:
            return cf + _orig_getaddrinfo(host, *args, **kwargs)
        except OSError:
            if cf:
                return cf
            raise
    return _orig_getaddrinfo(host, *args, **kwargs)


socket.getaddrinfo = _getaddrinfo

_local = threading.local()


def session():
    s = getattr(_local, "s", None)
    if s is None:
        s = requests.Session()
        s.headers["User-Agent"] = f"deadlock-mods/{__version__} (+{sys.platform})"
        _local.s = s
    return s


def get_json(url, params=None, timeout=20):
    r = session().get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def get_bytes(url, timeout=20):
    r = session().get(url, timeout=timeout)
    r.raise_for_status()
    return r.content


class Cancelled(Exception):
    pass


def _mirror_urls(final_url):
    parts = urlsplit(final_url)
    urls = [urlunsplit(parts._replace(netloc=f"{m}.gamebanana.com")) for m in FAST_MIRRORS]
    if final_url not in urls:
        urls.append(final_url)
    return urls


def download(url, dest, md5=None, progress=None, cancel=None):
    """Baixa `url` em `dest`, trocando de espelho se ficar lento. progress(bytes, total)."""
    s = session()
    # Descobre pra onde o /dl/<id> redireciona, sem baixar nada.
    r = s.head(url, allow_redirects=True, timeout=20)
    final = r.url
    candidates = _mirror_urls(final) if GB_ASSETS.match(urlsplit(final).hostname or "") else [final]

    done = 0
    total = None
    last_err = None
    with open(dest, "wb") as f:
        for i, cand in enumerate(candidates):
            is_last = i == len(candidates) - 1
            headers = {"Range": f"bytes={done}-"} if done else {}
            try:
                with s.get(cand, stream=True, timeout=20, headers=headers) as resp:
                    if resp.status_code not in (200, 206):
                        last_err = f"{cand}: HTTP {resp.status_code}"
                        continue
                    if done and resp.status_code == 200:   # espelho ignorou o Range
                        f.seek(0)
                        f.truncate()
                        done = 0
                    length = int(resp.headers.get("Content-Length") or 0)
                    total = done + length if length else total
                    start, start_bytes = time.monotonic(), done
                    for chunk in resp.iter_content(1 << 16):
                        if cancel and cancel():
                            raise Cancelled()
                        f.write(chunk)
                        done += len(chunk)
                        if progress:
                            progress(done, total)
                        elapsed = time.monotonic() - start
                        if not is_last and elapsed > PROBE_SECONDS and (done - start_bytes) / elapsed < SLOW_BPS:
                            raise TimeoutError("espelho lento")
                    break
            except Cancelled:
                raise
            except (requests.RequestException, TimeoutError) as e:
                last_err = f"{cand}: {e}"
                continue
        else:
            raise RuntimeError(f"download falhou ({last_err})")

    if md5:
        h = hashlib.md5()
        with open(dest, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest().lower() != md5.lower():
            raise RuntimeError("arquivo baixado veio corrompido (MD5 não confere)")
