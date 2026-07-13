"""net_guard — garde SSRF/URL pour les outils réseau (fix CRITICAL SSRF).

- Schéma http/https uniquement.
- Résolution DNS de l'hôte : refus si une IP cible est loopback/privée/link-local
  (169.254.169.254 metadata cloud)/réservée/multicast/unspecified.
- Redirections suivies MANUELLEMENT et re-validées à chaque saut (anti bypass
  SSRF par redirection). Plafond de sauts.
- Timeout (connect, read) obligatoire.
Note: petite fenêtre TOCTOU DNS-rebinding résiduelle (résolution puis connexion) —
acceptable pour un agent local ; le risque majeur (URL/redirect internes) est fermé.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse, urljoin

import requests

_ALLOWED_SCHEMES = {"http", "https"}
_REDIRECT_CODES = {301, 302, 303, 307, 308}

DEFAULT_TIMEOUT = 30                       # secondes (connect=read)
MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024     # 100 Mo (download_file)
MAX_TEXT_BYTES = 10 * 1024 * 1024          # 10 Mo (http_get_request)
MAX_REDIRECTS = 5


class UrlBlockedError(Exception):
    """URL/redirection visant une cible interne ou un schéma interdit (SSRF)."""


def _ip_forbidden(ip: str) -> bool:
    o = ipaddress.ip_address(ip)
    return (o.is_private or o.is_loopback or o.is_link_local or o.is_reserved
            or o.is_multicast or o.is_unspecified)


def guarded_url(url: str) -> str:
    """Valide schéma + hôte ; refuse si l'hôte résout vers une IP interne."""
    p = urlparse(url)
    if p.scheme.lower() not in _ALLOWED_SCHEMES:
        raise UrlBlockedError(f"Schéma non autorisé: {p.scheme!r} (http/https uniquement)")
    host = p.hostname
    if not host:
        raise UrlBlockedError("URL sans hôte")
    port = p.port or (443 if p.scheme.lower() == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise UrlBlockedError(f"Résolution DNS échouée pour {host}: {e}")
    for *_, sockaddr in infos:
        ip = str(sockaddr[0])
        if _ip_forbidden(ip):
            raise UrlBlockedError(f"Cible interne interdite (SSRF): {host} -> {ip}")
    return url


def safe_get(url: str, timeout: int = DEFAULT_TIMEOUT) -> requests.Response:
    """GET avec garde SSRF + redirections re-validées à chaque saut.

    Retourne une Response en mode stream=True (à lire puis fermer par l'appelant).
    """
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        guarded_url(current)
        resp = requests.get(current, timeout=(min(timeout, 10), timeout),
                            stream=True, allow_redirects=False)
        if resp.status_code in _REDIRECT_CODES and "location" in resp.headers:
            nxt = urljoin(current, resp.headers["location"])
            resp.close()
            current = nxt
            continue
        return resp
    raise UrlBlockedError(f"Trop de redirections (> {MAX_REDIRECTS})")


def read_capped(resp: requests.Response, max_bytes: int) -> bytes:
    """Lit le corps en streaming en refusant tout dépassement de max_bytes."""
    cl = resp.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > max_bytes:
        resp.close()
        raise UrlBlockedError(f"Réponse trop volumineuse: {cl} > {max_bytes} octets")
    total, chunks = 0, []
    for chunk in resp.iter_content(65536):
        total += len(chunk)
        if total > max_bytes:
            resp.close()
            raise UrlBlockedError(f"Réponse dépasse le plafond de {max_bytes} octets")
        chunks.append(chunk)
    return b"".join(chunks)
