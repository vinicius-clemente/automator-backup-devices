#!/usr/bin/env python3

from flask import Flask, render_template, abort, request, jsonify, redirect, url_for, session, send_file
import os
import json
import difflib
from datetime import datetime
import re
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.urandom(24).hex()

BACKUP_DIR = "/portal_bkps/"
STATE_FILE = os.path.join(BACKUP_DIR, "backup_state.json")
USERS_FILE = os.path.join(BACKUP_DIR, "users.json")

ROLES = {
    "admin": "Administrador",
    "operador": "Operador",
    "visualizador": "Visualizador"
}

DEVICE_FILES = {
    "Switches Cisco": "devices.txt",
    "Switches Dell": "devices-dell.txt",
    "Switches Datacom": "devices-datacom.txt",
    "Switches Cisco (Legacy)": "devices-XXXXXXX.txt",
    "Roteadores Infinera": "devices_rts.txt",
    "Firewalls Fortinet": "devices-fortinet.txt",
}


def load_users():
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE) as f:
                return json.load(f)
        except:
            pass
    return {}


def save_users(users):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=2)


def init_users():
    users = load_users()
    changed = False
    if not users:
        users["admin"] = {
            "password": generate_password_hash("backup"),
            "role": "admin",
            "must_change_password": False,
            "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        changed = True
    else:
        for name, data in users.items():
            if "role" not in data:
                data["role"] = "admin" if name == "admin" else "visualizador"
                changed = True
            if "must_change_password" not in data:
                data["must_change_password"] = False
                changed = True
            if "nome" not in data:
                data["nome"] = name
                changed = True
            if "cargo" not in data:
                data["cargo"] = ""
                changed = True
    if changed:
        save_users(users)
    return users


init_users()


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.path))
        users = load_users()
        username = session.get("username", "")
        if username in users and users[username].get("must_change_password", False):
            if request.endpoint not in ("change_password", "logout"):
                return redirect(url_for("change_password"))
        return f(*args, **kwargs)
    return decorated


def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            users = load_users()
            username = session.get("username", "")
            user_role = users.get(username, {}).get("role", "visualizador")
            if user_role not in roles:
                abort(403)
            return f(*args, **kwargs)
        return decorated
    return decorator


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = request.form.get("user", "")
        passwd = request.form.get("pass", "")
        users = load_users()
        if user in users and check_password_hash(users[user]["password"], passwd):
            session["logged_in"] = True
            session["username"] = user
            session["role"] = users[user].get("role", "visualizador")
            session["nome"] = users[user].get("nome", user)
            if users[user].get("must_change_password", False):
                return redirect(url_for("change_password"))
            return redirect(request.args.get("next", "/"))
        return render_template("login.html", erro="Usuário ou senha inválidos")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    username = session.get("username", "")
    if request.method == "POST":
        current = request.form.get("current", "")
        newpass = request.form.get("newpass", "")
        confirm = request.form.get("confirm", "")
        if not current or not newpass or not confirm:
            return render_template("change_password.html", erro="Preencha todos os campos")
        if newpass != confirm:
            return render_template("change_password.html", erro="Senhas não conferem")
        if len(newpass) < 4:
            return render_template("change_password.html", erro="Senha deve ter no mínimo 4 caracteres")
        users = load_users()
        if username not in users or not check_password_hash(users[username]["password"], current):
            return render_template("change_password.html", erro="Senha atual inválida")
        users[username]["password"] = generate_password_hash(newpass)
        users[username]["must_change_password"] = False
        save_users(users)
        session["password_changed"] = True
        return redirect(url_for("index"))
    users = load_users()
    forced = users.get(username, {}).get("must_change_password", False) if username in users else False
    return render_template("change_password.html", forced=forced)


@app.route("/admin/users")
@login_required
@role_required("admin")
def admin_users():
    users = load_users()
    return render_template("users.html", users=users, roles=ROLES)


@app.route("/admin/users/create", methods=["POST"])
@login_required
@role_required("admin")
def admin_users_create():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "").strip()
    role = request.form.get("role", "visualizador").strip()
    if not username or not password:
        return render_template("users.html", users=load_users(), roles=ROLES, erro="Preencha usuário e senha")
    if role not in ROLES:
        role = "visualizador"
    users = load_users()
    if username in users:
        return render_template("users.html", users=users, roles=ROLES, erro="Usuário já existe")
    nome = request.form.get("nome", "").strip() or username
    cargo = request.form.get("cargo", "").strip()
    users[username] = {
        "password": generate_password_hash(password),
        "role": role,
        "nome": nome,
        "cargo": cargo,
        "must_change_password": True,
        "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    save_users(users)
    return render_template("users.html", users=users, roles=ROLES, msg=f"Usuário '{username}' criado como {ROLES[role]}")


@app.route("/admin/users/password", methods=["POST"])
@login_required
@role_required("admin")
def admin_users_password():
    username = request.form.get("username", "").strip()
    newpass = request.form.get("newpass", "").strip()
    if not username or not newpass:
        return render_template("users.html", users=load_users(), roles=ROLES, erro="Preencha todos os campos")
    users = load_users()
    if username not in users:
        return render_template("users.html", users=users, roles=ROLES, erro="Usuário não encontrado")
    users[username]["password"] = generate_password_hash(newpass)
    users[username]["must_change_password"] = True
    save_users(users)
    return render_template("users.html", users=users, roles=ROLES, msg=f"Senha de '{username}' alterada (forçará troca no próximo login)")


@app.route("/admin/users/role", methods=["POST"])
@login_required
@role_required("admin")
def admin_users_role():
    username = request.form.get("username", "").strip()
    role = request.form.get("role", "").strip()
    if not username or role not in ROLES:
        return render_template("users.html", users=load_users(), roles=ROLES, erro="Usuário ou cargo inválido")
    if username == "admin" and role != "admin":
        return render_template("users.html", users=load_users(), roles=ROLES, erro="Não pode rebaixar o admin")
    users = load_users()
    if username not in users:
        return render_template("users.html", users=users, roles=ROLES, erro="Usuário não encontrado")
    users[username]["role"] = role
    save_users(users)
    return render_template("users.html", users=users, roles=ROLES, msg=f"Cargo de '{username}' alterado para {ROLES[role]}")


@app.route("/admin/users/delete", methods=["POST"])
@login_required
@role_required("admin")
def admin_users_delete():
    username = request.form.get("username", "").strip()
    if not username:
        return render_template("users.html", users=load_users(), roles=ROLES, erro="Usuário inválido")
    if username == "admin":
        return render_template("users.html", users=load_users(), roles=ROLES, erro="Não pode excluir admin")
    users = load_users()
    if username not in users:
        return render_template("users.html", users=users, roles=ROLES, erro="Usuário não encontrado")
    del users[username]
    save_users(users)
    return render_template("users.html", users=users, roles=ROLES, msg=f"Usuário '{username}' excluído")


@app.route("/admin/users/edit", methods=["POST"])
@login_required
@role_required("admin")
def admin_users_edit():
    username = request.form.get("username", "").strip()
    nome = request.form.get("nome", "").strip()
    cargo = request.form.get("cargo", "").strip()
    if not username:
        return render_template("users.html", users=load_users(), roles=ROLES, erro="Usuário inválido")
    users = load_users()
    if username not in users:
        return render_template("users.html", users=users, roles=ROLES, erro="Usuário não encontrado")
    if nome:
        users[username]["nome"] = nome
    users[username]["cargo"] = cargo
    save_users(users)
    return render_template("users.html", users=users, roles=ROLES, msg=f"Perfil de '{username}' atualizado")


def get_devices_by_type():
    state = get_state()
    ip_to_name = {}
    for hostname, info in state.items():
        if "ip" in info:
            ip_to_name[info["ip"]] = hostname

    all_devices = []
    for label, filename in DEVICE_FILES.items():
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
        if not os.path.exists(path):
            path = filename
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    ip = line.strip()
                    if ip and not ip.startswith("#"):
                        name = ip_to_name.get(ip, ip)
                        all_devices.append((name, label))

    groups = {"Switches": [], "Roteadores": [], "Firewalls": [], "Outros": []}
    for name, label in all_devices:
        upper = name.upper()
        if re.search(r"(^|-)RT", upper):
            groups["Roteadores"].append(name)
        elif re.search(r"(^|-)FW", upper):
            groups["Firewalls"].append(name)
        elif re.search(r"(^|-)SW", upper):
            groups["Switches"].append(name)
        else:
            groups["Outros"].append(name)

    return {k: v for k, v in groups.items() if v}


def get_devices():
    devices = {}
    if not os.path.exists(BACKUP_DIR):
        return devices
    for item in os.listdir(BACKUP_DIR):
        item_path = os.path.join(BACKUP_DIR, item)
        if os.path.isdir(item_path) and not item.startswith("."):
            backups = sorted([f for f in os.listdir(item_path) if f.endswith(".txt")], reverse=True)
            info = {"path": item_path, "backups": backups, "qtd": len(backups)}
            if backups:
                info["ultimo"] = backups[0].replace("_config.txt", "")
            devices[item] = info
    return devices


def get_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except:
            return {}
    return {}


@app.route("/")
@login_required
def index():
    groups = get_devices_by_type()
    devices = get_devices()
    state = get_state()

    groups_with_status = {}
    for tipo, devs in groups.items():
        groups_with_status[tipo] = []
        for d in devs:
            info = devices.get(d, {})
            st = state.get(d, {})
            groups_with_status[tipo].append({
                "nome": d,
                "tem_backup": d in devices,
                "ultimo": info.get("ultimo", "-"),
                "qtd": info.get("qtd", 0),
                "status": st.get("status", "-"),
                "platform": st.get("platform", "-"),
                "last_backup": st.get("last_backup", "-")
            })

    stats = {"total": 0, "com_backup": 0, "sem_backup": 0}
    for tipo, devs in groups_with_status.items():
        for d in devs:
            stats["total"] += 1
            if d["tem_backup"]:
                stats["com_backup"] += 1
            else:
                stats["sem_backup"] += 1

    user_role = session.get("role", "visualizador")
    return render_template("index.html", groups=groups_with_status, stats=stats, user_role=user_role)


@app.route("/device/<device_name>")
@login_required
@role_required("admin", "operador")
def device_view(device_name):
    device_path = os.path.join(BACKUP_DIR, device_name)
    if not os.path.exists(device_path):
        abort(404)

    backups = sorted(
        [f for f in os.listdir(device_path) if f.endswith(".txt")],
        reverse=True
    )

    state = get_state()
    info = state.get(device_name, {})

    backups_info = []
    for b in backups:
        fp = os.path.join(device_path, b)
        data = b.replace("_config.txt", "")
        tamanho = os.path.getsize(fp) if os.path.exists(fp) else 0
        backups_info.append({"arquivo": b, "data": data, "tamanho": f"{tamanho/1024:.1f}KB"})

    return render_template("device.html",
        device=device_name,
        backups=backups_info,
        info=info
    )


@app.route("/config/<device_name>/<backup_file>")
@login_required
@role_required("admin", "operador")
def config_view(device_name, backup_file):
    fp = os.path.join(BACKUP_DIR, device_name, backup_file)
    if not os.path.exists(fp) or not os.path.isfile(fp):
        abort(404)

    with open(fp, encoding="utf-8", errors="replace") as f:
        content = f.read()

    return render_template("config.html",
        device=device_name,
        backup=backup_file,
        content=content,
        data=backup_file.replace("_config.txt", ""),
        lines=len(content.splitlines())
    )


@app.route("/download/<device_name>/<backup_file>")
@login_required
@role_required("admin", "operador")
def download_config(device_name, backup_file):
    fp = os.path.join(BACKUP_DIR, device_name, backup_file)
    if not os.path.exists(fp) or not os.path.isfile(fp):
        abort(404)
    return send_file(fp, as_attachment=True, download_name=backup_file)


@app.route("/diff/<device_name>")
@login_required
@role_required("admin", "operador")
def diff_view(device_name):
    device_path = os.path.join(BACKUP_DIR, device_name)
    if not os.path.exists(device_path):
        abort(404)

    backups = sorted(
        [f for f in os.listdir(device_path) if f.endswith(".txt")],
        reverse=True
    )

    b1 = request.args.get("b1", backups[0] if len(backups) > 0 else "")
    b2 = request.args.get("b2", backups[1] if len(backups) > 1 else "")

    diff_lines = []
    if b1 and b2 and b1 != b2:
        fp1 = os.path.join(device_path, b1)
        fp2 = os.path.join(device_path, b2)
        if os.path.exists(fp1) and os.path.exists(fp2):
            with open(fp1, encoding="utf-8", errors="replace") as f:
                c1 = f.readlines()
            with open(fp2, encoding="utf-8", errors="replace") as f:
                c2 = f.readlines()
            diff = difflib.unified_diff(c1, c2, fromfile=b1, tofile=b2, lineterm="")
            diff_lines = list(diff)

    return render_template("diff.html",
        device=device_name,
        backups=backups,
        b1=b1,
        b2=b2,
        diff=diff_lines
    )


@app.route("/logs")
@login_required
@role_required("admin", "operador")
def logs():
    log_file = os.path.join(BACKUP_DIR, "backup_log.txt")
    logs_content = []
    if os.path.exists(log_file):
        with open(log_file, encoding="utf-8") as f:
            for line in f.readlines()[-500:]:
                logs_content.append(line.strip())
    return render_template("logs.html", logs=logs_content[::-1])


@app.route("/api/state")
@login_required
@role_required("admin", "operador")
def api_state():
    return jsonify(get_state())


@app.errorhandler(403)
def forbidden(e):
    return render_template("login.html", erro="Acesso negado: você não tem permissão"), 403


if __name__ == "__main__":
    app.run(host="X.X.X.X", port=58000, debug=False)
