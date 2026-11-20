#!/usr/bin/env python3

import paramiko
from netmiko import ConnectHandler
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
import re
import json
import logging
import threading
import time
import subprocess

IPS_FILE = "devices.txt"
USERNAME = os.getenv("BKP_USER", "XXXXXX")
PASSWORD = os.getenv("BKP_PASS", "XXXXXX")
ENABLE_SECRET = os.getenv("BKP_ENABLE", "")
THREADS = 15
BACKUP_DIR = "/portal_bkps/"
STATE_FILE = os.path.join(BACKUP_DIR, "backup_state.json")

PLATAFORMAS = ['cisco_ios', 'cisco_xe', 'cisco_nxos']

logging.getLogger('paramiko').setLevel(logging.CRITICAL)
logging.getLogger('netmiko').setLevel(logging.WARNING)

_lock = threading.Lock()

def log_event(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _lock:
        with open(os.path.join(BACKUP_DIR, "backup_log.txt"), "a", encoding="utf-8") as log:
            log.write(f"[{ts}] {msg}\n")

def has_git():
    try:
        subprocess.run(["git", "--version"], capture_output=True)
        return True
    except:
        return False

def git_commit(hostname, arquivo):
    if not has_git():
        return
    try:
        repo_dir = os.path.join(BACKUP_DIR, hostname)
        if not os.path.exists(os.path.join(repo_dir, ".git")):
            subprocess.run(["git", "init"], cwd=repo_dir, capture_output=True)
        subprocess.run(["git", "config", "user.email", "bkp@portal.local"], cwd=repo_dir, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Backup Automator"], cwd=repo_dir, capture_output=True)
        subprocess.run(["git", "add", "."], cwd=repo_dir, capture_output=True)
        subprocess.run(["git", "commit", "-m", f"Backup {datetime.now().strftime('%Y-%m-%d %H:%M')}"], cwd=repo_dir, capture_output=True)
    except:
        pass

def detect_platform(ip, hostname_hint=""):
    hint = (ip + " " + hostname_hint).upper()
    if "NX" in hint or "NEXUS" in hint or "MDS" in hint:
        return "cisco_nxos"
    return "cisco_ios"

def get_kex_params(hostname=""):
    h = hostname.upper()
    if any(x in h for x in ["2960", "3750", "3560", "SRX"]):
        return "legacy"
    return "modern"

def set_kex(mode):
    if mode == "legacy":
        kex = ('diffie-hellman-group14-sha1', 'diffie-hellman-group1-sha1')
    else:
        kex = ('diffie-hellman-group-exchange-sha256', 'diffie-hellman-group14-sha256')
    paramiko.Transport._preferred_kex = kex
    paramiko.Transport._preferred_keys = ('ssh-rsa',)

def save_state(hostname, ip, status, platform="", lines=0, error=""):
    with _lock:
        state = {}
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE) as f:
                    state = json.load(f)
            except:
                pass
        state[hostname] = {
            "ip": ip,
            "status": status,
            "platform": platform,
            "lines": lines,
            "error": error,
            "last_backup": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)

def backup_device(ip):
    ip = ip.strip()
    if not ip or ip.startswith('#'):
        return {"ip": ip, "status": "skipped"}

    hostname = ip
    conectado = False
    falhas = []

    for platform in PLATAFORMAS:
        if conectado:
            break

        device = {
            'device_type': platform,
            'host': ip,
            'username': USERNAME,
            'password': PASSWORD,
            'secret': ENABLE_SECRET if ENABLE_SECRET else PASSWORD,
            'port': 22,
            'conn_timeout': 20,
            'session_timeout': 180,
            'fast_cli': False,
            'global_delay_factor': 4,
            'allow_agent': False,
            'keepalive': 30,
        }

        try:
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"[{ts}] {ip} -> {platform} ... ", end="", flush=True)
            conn = ConnectHandler(**device)

        except Exception as e:
            set_kex("legacy")
            try:
                conn = ConnectHandler(**device)
            except Exception as e2:
                falhas.append(f"{platform}: {str(e2)[:80]}")
                print(f"FALHOU ({str(e2)[:50]})")
                continue

        try:
            conn.send_command_timing("terminal length 0")
            conn.send_command_timing("terminal no monitor")
            conn.send_command_timing("no logging console")

            if platform == 'cisco_nxos':
                conn.send_command_timing("terminal dont-ask")

            if ENABLE_SECRET:
                conn.enable()

            prompt = conn.find_prompt()
            hostname = re.sub(r'[>#]', '', prompt).strip() or ip

            output = conn.send_command("show running-config", read_timeout=240)

            data = datetime.now().strftime("%Y-%m-%d")
            dir_path = os.path.join(BACKUP_DIR, hostname)
            os.makedirs(dir_path, exist_ok=True)
            arquivo = os.path.join(dir_path, f"{data}_config.txt")
            with open(arquivo, "w", encoding="utf-8") as f:
                f.write(f"# Backup {datetime.now()}\n")
                f.write(f"# {ip} -> {hostname} [{platform}]\n\n")
                f.write(output)

            print(f"OK -> {hostname} ({len(output.splitlines())} linhas)")
            log_event(f"{ip} -> SUCESSO -> {hostname} [{platform}]")
            save_state(hostname, ip, "success", platform, len(output.splitlines()))
            git_commit(hostname, arquivo)
            conn.disconnect()
            conectado = True
            return {"ip": ip, "hostname": hostname, "status": "success", "platform": platform}

        except Exception as e:
            falhas.append(f"{platform}: {str(e)[:80]}")
            print(f"FALHOU ({str(e)[:50]})")
            if 'conn' in locals():
                try:
                    conn.disconnect()
                except:
                    pass

    if not conectado:
        msg = f"{ip} -> FALHOU EM TODAS. Erros: {falhas}"
        log_event(msg)
        save_state(hostname if hostname != ip else ip, ip, "failed", "", error=str(falhas))
        return {"ip": ip, "hostname": hostname, "status": "failed", "error": falhas}


if __name__ == "__main__":
    print(f"\nBACKUP CISCO AUTOMATOR v2 - {datetime.now().strftime('%d/%m/%Y %H:%M')}\n")

    try:
        with open(IPS_FILE, "r") as f:
            ips = [l.strip() for l in f if l.strip() and not l.startswith('#')]
    except FileNotFoundError:
        print(f"ERRO: {IPS_FILE} nao encontrado.")
        exit(1)

    if not ips:
        print("Nenhum IP encontrado.")
        exit(0)

    print(f"{len(ips)} equipamentos, {THREADS} threads")
    print(f"Backup dir: {BACKUP_DIR}\n")

    resultados = []
    with ThreadPoolExecutor(max_workers=THREADS) as executor:
        futures = {executor.submit(backup_device, ip): ip for ip in ips}
        for future in as_completed(futures):
            resultados.append(future.result())

    success = sum(1 for r in resultados if r and r.get("status") == "success")
    failed = sum(1 for r in resultados if r and r.get("status") == "failed")
    print(f"\nRESUMO: {success} OK, {failed} FALHAS de {len(resultados)} equipamentos")
