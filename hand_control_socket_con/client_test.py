# -*- coding: utf-8 -*-
"""
client_test.py — Test istemcisi.

    python client_test.py                 -> etkilesimli
    python client_test.py PING ZERO       -> komutlari sirayla gonder
"""
import socket
import sys

HOST = "127.0.0.1"
PORT = 9090


def send_all(cmds):
    with socket.create_connection((HOST, PORT), timeout=10) as s:
        s.settimeout(10)
        for c in cmds:
            s.sendall((c + "\n").encode())
            try:
                print(f"{c:<16} -> {s.recv(4096).decode().strip()}")
            except socket.timeout:
                print(f"{c:<16} -> (cevap yok)")


def interactive():
    with socket.create_connection((HOST, PORT), timeout=60) as s:
        print(f"Baglandi {HOST}:{PORT}. Komut yaz (cikis: q)")
        while True:
            try:
                c = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if c.lower() in ("q", "quit", "exit"):
                break
            if not c:
                continue
            s.sendall((c + "\n").encode())
            try:
                print(s.recv(4096).decode().strip())
            except socket.timeout:
                print("(cevap yok)")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        send_all(sys.argv[1:])
    else:
        interactive()
