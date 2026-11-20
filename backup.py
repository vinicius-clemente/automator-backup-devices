#!/usr/bin/env python3

import paramiko
from netmiko import ConnectHandler, NetMikoAuthenticationException
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
import os
import re
import json
import logging
import threading
import time
import shutil
import subprocess

CISCO_FILE = "devices.txt"
DELL_FILE = "devices-dell.txt"
DATACOM_FILE = "devices-datacom.txt"
RTS_FILE = "devices_rts.txt"
TELNET_FILE = "devices-xxxxx.txt"
FORTINET_FILE = "devices-fortinet.txt"
USERNAME = os.getenv("BKP_USER", "XXXXXXX")
PASSWORD = os.getenv("BKP_PASS", "XXXXXXX")
ENABLE_SECRET = os.getenv("BKP_ENABLE", "")
THREADS = 15
BACKUP_DIR = "/portal_bkps/"
STATE_FILE = os.path.join(BACKUP_DIR, "backup_state.json")

logging.getLogger('paramiko').setLevel(logging.CRITICAL)
logging.getLogger('netmiko').setLevel(logging.WARNING)

paramiko.Transport._preferred_keys = (
    'ecdsa-sha2-nistp256', 'ecdsa-sha2-nistp384', 'ecdsa-sha2-nistp521',
    'ssh-ed25519', 'rsa-sha2-512', 'rsa-sha2-256', 'ssh-rsa',
)
paramiko.Transport._preferred_kex = (
    'ecdh-sha2-nistp256', 'ecdh-sha2-nistp384', 'ecdh-sha2-nistp521',
    'diffie-hellman-group-exchange-sha256',
    'diffie-hellman-group14-sha256', 'diffie-hellman-group14-sha1',
    'diffie-hellman-group1-sha1',
)

_lock = threading.Lock()

CISCO_PLATFORMS = ['cisco_ios', 'cisco_xe', 'cisco_nxos']


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


def git_commit(hostname):
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


def save_state(hostname, ip, status, platform="", lines=0, error=""):
    with _lock:
        state = {}
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE) as f:
                    state = json.load(f)
            except (json.JSONDecodeError, OSError):
                bak = STATE_FILE + ".bak"
                if os.path.exists(bak):
                    try:
                        with open(bak) as f:
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
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, STATE_FILE)


# ==================== UTIL ====================

def read_until_prompt(channel, timeout=10, prompt_regex=r'[#>]\s*$'):
    output = ""
    start_time = time.time()
    while time.time() - start_time < timeout:
        if channel.recv_ready():
            output += channel.recv(65536).decode('utf-8', errors='ignore')
            if re.search(prompt_regex, output.strip(), re.MULTILINE):
                return output
            time.sleep(0.1)
        else:
            time.sleep(0.5)
    return output


# ==================== DELL OS10 ====================

def backup_dell_paramiko(ip):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        client.connect(ip, username=USERNAME, password=PASSWORD, timeout=30,
                       banner_timeout=60, allow_agent=False, look_for_keys=False,
                       disabled_algorithms={'pubkeys': ['rsa-sha2-256', 'rsa-sha2-512']})
        shell = client.invoke_shell(width=400, height=50000)
        time.sleep(2)
        output = shell.recv(9999999).decode('utf-8', errors='ignore')

        if 'Username' in output or 'Login' in output:
            shell.send(USERNAME + "\n")
            time.sleep(1)
            shell.recv(9999)
            shell.send(PASSWORD + "\n")
            time.sleep(2)
            shell.recv(9999)
            shell.send("\n")

        shell.send("\n")
        read_until_prompt(shell, prompt_regex=r'[>]\s*$')

        shell.send("enable\n")
        enable_out = read_until_prompt(shell, prompt_regex=r'[#]|Password.*')

        if 'Password' in enable_out or 'assword' in enable_out:
            shell.send((ENABLE_SECRET if ENABLE_SECRET else PASSWORD) + "\n")
            read_until_prompt(shell, prompt_regex=r'[#]\s*$')
        elif 'Denied' in enable_out or 'denied' in enable_out:
            shell.send((ENABLE_SECRET if ENABLE_SECRET else PASSWORD) + "\n")
            read_until_prompt(shell, prompt_regex=r'[#]\s*$')

        shell.send("\n")
        read_until_prompt(shell, timeout=2, prompt_regex=r'[#]\s*$')

        shell.send("show running-config | no-more\n")
        time.sleep(5)

        output = ""
        end_time = time.time() + 90

        while time.time() < end_time:
            if shell.recv_ready():
                output += shell.recv(65536).decode('utf-8', errors='ignore')
                time.sleep(0.2)
            if re.search(r'[#]\s*$', output.strip(), re.MULTILINE):
                break
            if not shell.recv_ready() and (time.time() - (end_time - 90)) > 10 and len(output) > 0:
                break

        client.close()

        if "no-more" in output:
            config = output.split("no-more", 1)[-1]
        else:
            config = output

        config = re.sub(r'[^\n]*[#>]\s*$', '', config, flags=re.MULTILINE).strip()
        config = re.sub(r'^.*show running-config.*', '', config, count=1, flags=re.DOTALL | re.IGNORECASE).strip()

        linhas = [l.rstrip() for l in config.splitlines() if
                  l.strip() and not l[0] in "%^#" and
                  len(l.strip()) > 3 and
                  'syntax error' not in l.lower() and
                  'invalid input' not in l.lower()]

        if len(linhas) < 100:
            return None

        hostname = next((l.split()[1].strip('"\'') for l in linhas if l.startswith("hostname ")), ip)

        data = datetime.now().strftime("%Y-%m-%d")
        dir_path = os.path.join(BACKUP_DIR, hostname)
        os.makedirs(dir_path, exist_ok=True, mode=0o775)
        arquivo = os.path.join(dir_path, f"{data}_config.txt")

        with open(arquivo, "w", encoding="utf-8") as f:
            f.write(f"# Backup {datetime.now()}\n")
            f.write(f"# {ip} -> {hostname} [dell_os10]\n\n")
            f.write("\n".join(linhas) + "\n")

        return hostname, len(linhas)

    except Exception as e:
        raise
    finally:
        try:
            client.close()
        except:
            pass


# ==================== DATACOM ====================

def backup_datacom_paramiko(ip):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        client.connect(ip, username=USERNAME, password=PASSWORD, timeout=30,
                       banner_timeout=60, allow_agent=False, look_for_keys=False,
                       disabled_algorithms={'pubkeys': ['rsa-sha2-256', 'rsa-sha2-512']})
        chan = client.invoke_shell(width=300, height=1000)
        time.sleep(1)
        output = chan.recv(9999).decode('utf-8', errors='ignore')

        if 'Username' in output or 'Login' in output:
            chan.send(USERNAME + "\n")
            time.sleep(1)
            chan.recv(9999)
            chan.send(PASSWORD + "\n")
            time.sleep(1)
            chan.recv(9999)

        chan.send("\n")
        time.sleep(0.5)
        chan.send("paginate false\n")
        time.sleep(0.5)
        chan.send("enable\n")
        time.sleep(0.5)
        enable_out = chan.recv(9999).decode('utf-8', errors='ignore')
        if 'Password' in enable_out or 'assword' in enable_out:
            chan.send((ENABLE_SECRET if ENABLE_SECRET else PASSWORD) + "\n")
            time.sleep(0.5)

        chan.send("show running-config | nomore\n")
        time.sleep(5)

        output = ""
        end_time = time.time() + 90

        while time.time() < end_time:
            if chan.recv_ready():
                output += chan.recv(65536).decode('utf-8', errors='ignore')
                time.sleep(0.2)
            if re.search(r'[#]\s*$', output.strip(), re.MULTILINE):
                break
            if not chan.recv_ready() and time.time() > end_time - 80:
                break

        config = output
        if "!" in output:
            config = output[output.find("!"):]

        config = re.sub(r'[^\n]*#\s*$', '', config, flags=re.MULTILINE).strip()
        config = re.sub(r'^.*show running-config.*', '', config, count=1, flags=re.DOTALL | re.IGNORECASE).strip()

        lines = []
        for line in config.splitlines():
            l = line.rstrip()
            if (l and not l.startswith('%') and not l.startswith('^') and
                'syntax error' not in l.lower() and
                'invalid input' not in l.lower() and
                'More' not in l and l != '---(more)---'):
                lines.append(l)

        if len(lines) < 100:
            return None

        hostname = ip
        for line in lines:
            if line.strip().startswith("hostname "):
                hostname = line.split()[1].strip('"\'')
                break

        data = datetime.now().strftime("%Y-%m-%d")
        dir_path = os.path.join(BACKUP_DIR, hostname)
        os.makedirs(dir_path, exist_ok=True, mode=0o775)
        arquivo = os.path.join(dir_path, f"{data}_config.txt")

        with open(arquivo, "w", encoding="utf-8") as f:
            f.write(f"# Backup Datacom {datetime.now()}\n")
            f.write(f"# {ip} -> {hostname} [datacom]\n\n")
            f.write("\n".join(lines) + "\n")

        return hostname, len(lines)

    except Exception as e:
        raise
    finally:
        try:
            client.close()
        except:
            pass


# ==================== INFINERA / CORIANT ====================

def backup_infinera_paramiko(ip):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        client.connect(
            hostname=ip, username=USERNAME, password=PASSWORD,
            allow_agent=False, look_for_keys=False,
            disabled_algorithms={'pubkeys': ['rsa-sha2-256', 'rsa-sha2-512']},
            timeout=30, banner_timeout=60
        )
        remote_conn = client.invoke_shell()
        time.sleep(5)

        def send_and_wait(conn, command, wait_for="#", timeout=15):
            conn.send(command + '\r')
            time.sleep(2)
            output = ""
            start_time = time.time()
            while time.time() - start_time < timeout:
                if conn.recv_ready():
                    output += conn.recv(65535).decode('utf-8', 'ignore')
                    if wait_for in output:
                        break
                time.sleep(0.5)
            return output

        initial = remote_conn.recv(65535).decode('utf-8', 'ignore')
        if 'y(es)/n(o)' in initial or 'Answer:' in initial:
            send_and_wait(remote_conn, 'y', wait_for=">")

        prompt_data = send_and_wait(remote_conn, 'enable', wait_for="#")
        hostname = re.sub(r'[>#\s]', '', prompt_data.splitlines()[-1]).strip()
        if not hostname or hostname == "enable":
            hostname = ip

        print(f"  - Coletando config de {hostname}...", flush=True)
        remote_conn.send('show running-config\r')

        full_config = ""
        infinera_start = time.time()
        infinera_timeout = 120
        while time.time() - infinera_start < infinera_timeout:
            time.sleep(1.5)
            if remote_conn.recv_ready():
                part = remote_conn.recv(65535).decode('utf-8', 'ignore')
                full_config += part
                if "--More--" in part:
                    remote_conn.send(" ")
                    continue
                if '#' in part.splitlines()[-1]:
                    break
            else:
                if len(full_config) > 0 and time.time() - infinera_start > 30:
                    break
                time.sleep(1)

        client.close()
        clean_config = re.sub(r'.*--More--.*\r?\n?', '', full_config)

        if len(clean_config) < 1000:
            return None

        data = datetime.now().strftime("%Y-%m-%d")
        dir_path = os.path.join(BACKUP_DIR, hostname)
        os.makedirs(dir_path, exist_ok=True, mode=0o775)
        arquivo = os.path.join(dir_path, f"{data}_config.txt")

        with open(arquivo, "w", encoding="utf-8") as f:
            f.write(f"# Backup Infinera/Coriant {datetime.now()}\n")
            f.write(f"# {ip} -> {hostname}\n\n")
            f.write(clean_config)

        return hostname, len(clean_config.splitlines())

    except Exception as e:
        raise
    finally:
        try:
            client.close()
        except:
            pass


# ==================== FORTINET ====================

FORTINET_PORT = 3020

def backup_fortinet_paramiko(ip):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        client.connect(
            hostname=ip, port=FORTINET_PORT, username=USERNAME, password=PASSWORD,
            allow_agent=False, look_for_keys=False,
            timeout=30, banner_timeout=60
        )
        chan = client.invoke_shell(width=300, height=50000)
        time.sleep(2)
        output = chan.recv(9999).decode('utf-8', errors='ignore')

        if 'Username' in output or 'Login' in output:
            chan.send(USERNAME + "\n")
            time.sleep(1)
            chan.recv(9999)
            chan.send(PASSWORD + "\n")
            time.sleep(2)
            chan.recv(9999)

        chan.send("\n")
        time.sleep(1)

        # Disable paging
        chan.send("config system console\n")
        time.sleep(1)
        chan.send("set output standard\n")
        time.sleep(1)
        chan.send("end\n")
        time.sleep(1)

        chan.send("show full-configuration\n")
        time.sleep(5)

        output = ""
        end_time = time.time() + 120

        while time.time() < end_time:
            if chan.recv_ready():
                output += chan.recv(65536).decode('utf-8', errors='ignore')
                time.sleep(0.2)
            if re.search(r'#\s*$', output.strip(), re.MULTILINE) and len(output) > 5000:
                break
            if not chan.recv_ready() and time.time() > end_time - 20:
                break

        client.close()

        if "show full-configuration" in output:
            config = output.split("show full-configuration", 1)[-1]
        else:
            config = output

        config = re.sub(r'[^\n]*#\s*$', '', config, flags=re.MULTILINE).strip()
        config = re.sub(r'config system console\s*\nset output standard\s*\nend\s*\n', '', config, flags=re.MULTILINE)

        lines = [l.rstrip() for l in config.splitlines() if l.strip()]

        if len(lines) < 20:
            return None

        hostname = ip
        for line in lines:
            m = re.match(r'config system global', line)
            if m:
                continue
            m = re.search(r'set hostname\s+"?([^"\s]+)', line)
            if m:
                hostname = m.group(1)
                break

        data = datetime.now().strftime("%Y-%m-%d")
        dir_path = os.path.join(BACKUP_DIR, hostname)
        os.makedirs(dir_path, exist_ok=True, mode=0o775)
        arquivo = os.path.join(dir_path, f"{data}_config.txt")

        with open(arquivo, "w", encoding="utf-8") as f:
            f.write(f"# Backup Fortinet {datetime.now()}\n")
            f.write(f"# {ip} -> {hostname} [fortinet]\n\n")
            f.write("\n".join(lines) + "\n")

        return hostname, len(lines)

    except Exception as e:
        raise
    finally:
        try:
            client.close()
        except:
            pass


# ==================== TELNET (Cisco antigo) ====================

def backup_telnet(ip):
    try:
        import telnetlib
    except ImportError:
        return None

    try:
        tn = telnetlib.Telnet(ip, port=23, timeout=20)

        tn.read_until(b"Username:", timeout=10)
        tn.write(USERNAME.encode('ascii') + b"\n")
        tn.read_until(b"Password:", timeout=10)
        tn.write(PASSWORD.encode('ascii') + b"\n")

        time.sleep(2)
        output = tn.read_very_eager().decode('ascii', errors='ignore')

        if "Password" in output or ">" in output:
            tn.write(b"enable\n")
            time.sleep(1)
            tn.write(ENABLE_SECRET.encode('ascii') + b"\n")
            time.sleep(1)

        tn.write(b"terminal length 0\n")
        time.sleep(1)
        tn.write(b"terminal no monitor\n")
        time.sleep(1)

        tn.write(b"\n")
        time.sleep(1)
        prompt = tn.read_very_eager().decode('ascii', errors='ignore')
        hostname = re.search(r'([A-Za-z0-9_-]+)[>#]', prompt)
        hostname = hostname.group(1) if hostname else ip

        tn.write(b"show running-config\n")
        time.sleep(5)

        config = b""
        telnet_start = time.time()
        telnet_timeout = 120
        while time.time() - telnet_start < telnet_timeout:
            data = tn.read_very_eager()
            if not data:
                time.sleep(1)
                continue
            config += data
            if re.search(rb'[A-Za-z0-9_-]+[>#]\s*$', data):
                break
            time.sleep(0.5)

        config = config.decode('ascii', errors='ignore')

        lines = config.splitlines()
        start_idx = 0
        for i, line in enumerate(lines):
            if line.strip().startswith("show running-config"):
                start_idx = i + 1
                break
        clean_config = "\n".join(lines[start_idx:]).strip()

        if len(clean_config.splitlines()) < 50:
            tn.write(b"exit\n")
            tn.close()
            return None

        data_arq = datetime.now().strftime("%Y-%m-%d")
        dir_eq = os.path.join(BACKUP_DIR, hostname)
        os.makedirs(dir_eq, exist_ok=True, mode=0o775)
        arquivo = os.path.join(dir_eq, f"{data_arq}_config.txt")

        with open(arquivo, "w", encoding="utf-8") as f:
            f.write(f"# Backup TELNET - {datetime.now():%Y-%m-%d %H:%M:%S}\n")
            f.write(f"# IP: {ip} | Hostname: {hostname}\n\n")
            f.write(clean_config)

        tn.write(b"exit\n")
        tn.close()
        return hostname, len(clean_config.splitlines())

    except Exception as e:
        raise
    finally:
        try:
            tn.close()
        except:
            pass


# ==================== CISCO ====================

def backup_cisco_netmiko(ip):
    for platform in CISCO_PLATFORMS:
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
            'disabled_algorithms': {'pubkeys': ['rsa-sha2-256', 'rsa-sha2-512']},
        }

        for tentativa in range(1, 4):
            try:
                conn = ConnectHandler(**device)
            except NetMikoAuthenticationException:
                if tentativa < 3:
                    time.sleep(3)
                    continue
                break
            except Exception:
                if tentativa < 3:
                    time.sleep(3)
                    continue
                break

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
                os.makedirs(dir_path, exist_ok=True, mode=0o775)
                arquivo = os.path.join(dir_path, f"{data}_config.txt")

                with open(arquivo, "w", encoding="utf-8") as f:
                    f.write(f"# Backup {datetime.now()}\n")
                    f.write(f"# {ip} -> {hostname} [{platform}]\n\n")
                    f.write(output)

                conn.disconnect()
                return hostname, len(output.splitlines())

            except Exception:
                if 'conn' in locals():
                    try:
                        conn.disconnect()
                    except:
                        pass
                break

    return backup_cisco_legacy(ip)


def backup_cisco_legacy(ip):
    for tentativa in range(1, 4):
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                hostname=ip, username=USERNAME, password=PASSWORD,
                allow_agent=False, look_for_keys=False,
                disabled_algorithms={'pubkeys': ['rsa-sha2-256', 'rsa-sha2-512']},
                timeout=30, banner_timeout=60
            )
            chan = client.invoke_shell()
            time.sleep(3)
            chan.recv(9999)

            chan.send("terminal length 0\n")
            time.sleep(1)
            chan.send("terminal no monitor\n")
            time.sleep(1)
            chan.send("no logging console\n")
            time.sleep(1)

            if ENABLE_SECRET:
                chan.send("enable\n")
                time.sleep(2)
                chan.send(ENABLE_SECRET + "\n")
                time.sleep(1)

            chan.send("show running-config\n")
            time.sleep(5)

            output = ""
            end_time = time.time() + 120
            while time.time() < end_time:
                if chan.recv_ready():
                    output += chan.recv(65535).decode('utf-8', 'ignore')
                    time.sleep(0.2)
                if re.search(r'[>#]\s*$', output.strip(), re.MULTILINE):
                    break
                if not chan.recv_ready() and time.time() > end_time - 20:
                    break

            if "show running-config" in output:
                config = output.split("show running-config", 1)[-1]
            else:
                config = output
            config = re.sub(r'[^\n]*[>#]\s*$', '', config, flags=re.MULTILINE).strip()

            lines = [l for l in config.splitlines() if l.strip() and not l.strip().startswith(('%', '^', '--'))]
            if len(lines) < 50:
                client.close()
                return None

            hostname = ip
            for line in lines:
                if line.strip().startswith("hostname "):
                    hostname = line.split()[1].strip('"\'')
                    break

            data = datetime.now().strftime("%Y-%m-%d")
            dir_path = os.path.join(BACKUP_DIR, hostname)
            os.makedirs(dir_path, exist_ok=True, mode=0o775)
            arquivo = os.path.join(dir_path, f"{data}_config.txt")

            with open(arquivo, "w", encoding="utf-8") as f:
                f.write(f"# Backup Legacy {datetime.now()}\n")
                f.write(f"# {ip} -> {hostname} [cisco_legacy]\n\n")
                f.write("\n".join(lines) + "\n")

            client.close()
            return hostname, len(lines)

        except Exception:
            client.close()
            if tentativa < 3:
                time.sleep(3)
                continue
            return None


# ==================== WORKER ====================

def backup_device(ip, tipo="cisco"):
    ip = ip.strip()
    if not ip or ip.startswith('#'):
        return {"ip": ip, "status": "skipped"}

    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {ip:20} -> {tipo.upper():8} ... ", end="", flush=True)

    max_retries = 2
    for tentativa in range(1, max_retries + 2):
        try:
            if tipo == "dell":
                resultado = backup_dell_paramiko(ip)
            elif tipo == "datacom":
                resultado = backup_datacom_paramiko(ip)
            elif tipo == "infinera":
                resultado = backup_infinera_paramiko(ip)
            elif tipo == "telnet":
                resultado = backup_telnet(ip)
            elif tipo == "fortinet":
                resultado = backup_fortinet_paramiko(ip)
            else:
                resultado = backup_cisco_netmiko(ip)

            if resultado is None:
                if tentativa <= max_retries:
                    print(f"retry({tentativa}/{max_retries}) ", end="", flush=True)
                    time.sleep(3)
                    continue
                print("FALHOU (todas as tentativas)")
                log_event(f"{ip} [{tipo}] -> FALHOU")
                save_state(ip, ip, "failed", tipo, error="Sem conexao")
                return {"ip": ip, "hostname": ip, "status": "failed"}

            hostname, linhas = resultado
            print(f"OK -> {hostname:30} ({linhas} linhas)")
            log_event(f"{ip} [{tipo}] -> SUCESSO -> {hostname}")
            save_state(hostname, ip, "success", tipo, linhas)
            git_commit(hostname)
            return {"ip": ip, "hostname": hostname, "status": "success", "platform": tipo}

        except (paramiko.AuthenticationException, paramiko.SSHException) as e:
            if tentativa <= max_retries:
                print(f"retry({tentativa}/{max_retries}) ", end="", flush=True)
                time.sleep(3)
                continue
            print(f"FALHOU ({str(e)[:50]})")
            log_event(f"{ip} [{tipo}] -> FALHOU: {str(e)[:100]}")
            save_state(ip, ip, "failed", tipo, error=str(e)[:100])
            return {"ip": ip, "status": "failed", "error": str(e)[:100]}

        except PermissionError as e:
            erro_msg = f"Permission denied: {e.filename if hasattr(e, 'filename') else str(e)}"
            print(f"FALHOU ({erro_msg[:80]})")
            log_event(f"{ip} [{tipo}] -> FALHOU: {erro_msg}")
            save_state(ip, ip, "failed", tipo, error=erro_msg)
            return {"ip": ip, "status": "failed", "error": erro_msg}
        except Exception as e:
            erro_msg = str(e)[:100]
            print(f"FALHOU ({str(e)[:50]})")
            log_event(f"{ip} [{tipo}] -> FALHOU: {erro_msg}")
            save_state(ip, ip, "failed", tipo, error=erro_msg)
            return {"ip": ip, "status": "failed", "error": erro_msg}

    print("FALHOU (max retries)")
    log_event(f"{ip} [{tipo}] -> FALHOU apos {max_retries + 1} tentativas")
    save_state(ip, ip, "failed", tipo, error="Max retries")
    return {"ip": ip, "status": "failed", "error": "Max retries"}


def carregar_ips(arquivo, tipo):
    if not os.path.exists(arquivo):
        return []
    with open(arquivo) as f:
        return [(l.strip(), tipo) for l in f if l.strip() and not l.startswith('#')]


# ==================== MAIN ====================

if __name__ == "__main__":
    print(f"\nBACKUP UNIFICADO CISCO + DELL + DATACOM + INFINERA + TELNET + FORTINET - {datetime.now().strftime('%d/%m/%Y %H:%M')}\n")

    os.makedirs(BACKUP_DIR, exist_ok=True, mode=0o775)
    if not os.access(BACKUP_DIR, os.W_OK):
        print(f"ERRO: Sem permissao de escrita em {BACKUP_DIR}")
        exit(1)

    dispositivos = []
    dispositivos += carregar_ips(CISCO_FILE, "cisco")
    dispositivos += carregar_ips(DELL_FILE, "dell")
    dispositivos += carregar_ips(DATACOM_FILE, "datacom")
    dispositivos += carregar_ips(RTS_FILE, "infinera")
    dispositivos += carregar_ips(TELNET_FILE, "telnet")
    dispositivos += carregar_ips(FORTINET_FILE, "fortinet")

    if not dispositivos:
        print("Nenhum equipamento encontrado.")
        exit(0)

    uso = shutil.disk_usage(BACKUP_DIR)
    if uso.free / uso.total < 0.05:
        print(f"ERRO: Disco quase cheio! Apenas {uso.free // 1024 // 1024}MB livres de {uso.total // 1024 // 1024}MB")
        log_event(f"ERRO: Disco cheio ({uso.free // 1024 // 1024}MB livres)")
        exit(1)
    print(f"Disco: {uso.free // 1024 // 1024}MB livres de {uso.total // 1024 // 1024}MB")

    print(f"Total: {len(dispositivos)} equipamentos ({THREADS} threads)")
    print(f"Backup dir: {BACKUP_DIR}\n")

    resultados = []
    with ThreadPoolExecutor(max_workers=THREADS) as executor:
        futures = {executor.submit(backup_device, ip, t): ip for ip, t in dispositivos}
        try:
            for future in as_completed(futures, timeout=3600):
                resultados.append(future.result())
        except TimeoutError:
            log_event("ERRO: Timeout geral de 3600s atingido! Cancelando tarefas restantes")
            for f in futures:
                f.cancel()

    success = sum(1 for r in resultados if r.get("status") == "success")
    failed = sum(1 for r in resultados if r.get("status") == "failed")
    print(f"\nRESUMO: {success} OK, {failed} FALHAS de {len(resultados)} equipamentos\n")
