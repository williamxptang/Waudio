#!/usr/bin/env python3
"""The settings server starts at once and answers, even on a Mac whose name lookup never returns
(slow or missing DNS): it serves 127.0.0.1 only and needs no host name.

  python3 backend/tests/test_server_start.py
"""
import socket, sys, time, urllib.request
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from waudio.server.server import Server

LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({})).open  # never through a proxy


def no_name_lookup(*a, **kw):
    raise AssertionError("the server asked for the host name")


def the_server_starts_without_a_name_lookup():
    real, socket.getfqdn = socket.getfqdn, no_name_lookup  # stands in for DNS that never answers
    try:
        t = time.monotonic()
        s = Server(api=None)
        started = time.monotonic() - t
    finally:
        socket.getfqdn = real
    try:
        assert started < 2, started
        with LOCAL(f"http://127.0.0.1:{s.port}/", timeout=5) as r:
            assert r.status == 200, r.status
    finally:
        s.stop()


the_server_starts_without_a_name_lookup()
print("server start checks: ok")
