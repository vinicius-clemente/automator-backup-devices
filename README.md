# MANUAL DO PORTAL DE BACKUPS UNIFICADO (FLASK)

Este repositório contém a aplicação web em **Flask** e os scripts de automação em Python (`Netmiko` / `Paramiko`) responsáveis por realizar backups automáticos e manuais de equipamentos de rede (Cisco, Dell, Datacom, Infinera/Coriant, Telnet), além de fornecer uma interface web moderna para consulta de históricos, diffs de configuração, gerenciamento de dispositivos e controle de usuários.

---

## 📁 Estrutura do Projeto

- **`portal.py`**: Aplicação principal em Flask (rotas web, autenticação, gerenciamento de logs, diffs e usuários).
- **`backup.py` / `bkp-automator-cisco.py`**: Scripts de automação para conexão SSH/Telnet nos equipamentos e salvamento dos arquivos de configuração.
- **`templates/`**: Páginas HTML da interface web (Dashboard, Visualizador de Dispositivos, Comparador de Diffs, Logs, Usuários e Configurações).
- **`requirements.txt`**: Dependências Python do projeto.
- **`setup-service.sh`**: Script para configurar o portal como um serviço systemd no Linux (execução contínua).
- **`devices*.txt`**: Arquivos contendo a listagem de IPs, nomes e credenciais dos equipamentos divididos por fabricante/tecnologia.

---

## ⚙️ Pré-requisitos e Dependências

- Python 3.8+
- Bibliotecas Python:
  ```bash
  pip install -r requirements.txt
  ```
  *(Principais pacotes: `Flask`, `netmiko`, `paramiko`)*

---

## 🚀 Guia de Instalação e Deploy (Linux / VM)

### 1. Clonar ou Copiar os Arquivos
Envie os arquivos para o diretório de destino na máquina de automação:
```bash
scp -r ./Portal-Backup* root@<IP_DA_VM>:/caminho/do/projeto/
```

### 2. Configurar o Diretório de Armazenamento de Backups
Crie a pasta onde os arquivos de configuração coletados serão salvos:
```bash
sudo mkdir -p /portal_bkps
sudo chown -R $(whoami):$(whoami) /portal_bkps
```

### 3. Configurar os Arquivos de Dispositivos
Preencha os arquivos de inventário conforme o fabricante:
- `devices.txt` → Equipamentos Cisco IOS/NX-OS
- `devices-dell.txt` → Dell OS10
- `devices-datacom.txt` → Datacom
- `devices_rts.txt` → Infinera / Coriant
- `devices-cliente.txt` → Equipamentos Legados (Telnet)

### 4. Executar Backup Manual (Teste)
Para testar a coleta de backups manualmente via CLI:
```bash
python3 backup.py
```

### 5. Configurar o Portal como Serviço Systemd (24/7)
Para manter o servidor Flask rodando em segundo plano e reiniciar automaticamente com o sistema:
```bash
chmod +x setup-service.sh
sudo ./setup-service.sh
```

---

## 🌐 Acesso e Credenciais

- **URL Padrão:** `http://<IP_DA_VM>:58000`
- **Login Inicial:** `admin` / `backup` *(ou conforme configurado nas variáveis de ambiente)*

### Como Alterar a Senha do Administrador:
1. Edite o serviço systemd:
   ```bash
   sudo systemctl edit backup-portal
   ```
2. Adicione as linhas:
   ```ini
   [Service]
   Environment=PORTAL_PASS=sua-nova-senha-segura
   ```
3. Recarregue e reinicie o serviço:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl restart backup-portal
   ```

---

## 🕒 Automação de Backups (Cron)

Para agendar a execução automática diária dos backups:
```bash
crontab -e
```
Adicione a linha abaixo (exemplo para rodar todo dia às 02:00 AM):
```cron
0 2 * * * cd "/caminho/do/projeto/Portal-Backup" && python3 backup.py >> /portal_bkps/cron_log.txt 2>&1
```

---

## 🛠️ Comandos Úteis de Gerenciamento

```bash
# Verificar status do serviço
sudo systemctl status backup-portal

# Visualizar logs em tempo real
sudo journalctl -u backup-portal -f

# Parar o portal
sudo systemctl stop backup-portal

# Reiniciar o portal
sudo systemctl restart backup-portal
```
