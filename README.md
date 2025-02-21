# 🚀 BeamPay - Simplified Payments for Users & Businesses

BeamPay is a self-hosted payment gateway for seamless **Beam blockchain** transactions. It provides:
- **Automated balance tracking** by scanning the blockchain.
- **Secure API** for merchant integrations.
- **Webhook notifications** for deposits & withdrawals.
- **Telegram monitoring** (if enabled).
- **Admin Dashboard** for transaction tracking.
- **Auto-recovery of services** using systems.

Project Structure
```bash
BeamPay/
│── api.py               # FastAPI service for managing addresses, deposits, withdrawals
│── process_payments.py   # Background job for tracking blockchain transactions
│── lib/beam.py               # BEAM API Wrapper
│── config.py             # Configuration settings (loads .env variables)
│── db.py                 # MongoDB connection
│── .env.example          # Example environment file
│── requirements.txt      # Python dependencies
│── README.md             # Project documentation
```

🚀 Project Progress & TODO List

✅ **Completed Tasks**
-	✅ **Core Payment Processing** - Track transactions, update balances
-	✅ **API Development** - Secure FastAPI for deposits, withdrawals
-	✅ **Database Sync** - Sync addresses, transactions, and assets in MongoDB
-	✅ **Webhook Integration** - Notify services of deposits & withdrawals
-	✅ **Telegram Alerts** - Notify users & admins about transfers

🔄 **In Progress**
-	🚧 **Admin Dashboard** - Statistics & balance verification
-	🚧 **Transaction History** - User-friendly logs & filters
- 🚧 **Security Enhancements** - API Key Authentication & IP Whitelisting

🛠️ Upcoming Features
-	📝 **TBA**

---

## 🛠️ **Installation Guide**

### **1️⃣ Install Dependencies**
#### **🔹 Install MongoDB**
```bash
sudo apt update
sudo apt install -y mongodb
sudo systemctl enable mongod --now
```
> Check if MongoDB is running:
```bash
sudo systemctl status mongod
```

#### **🔹 Install Python & Virtual Environment**
```bash
sudo apt install -y python3 python3-pip python3-venv
```
> **Create a virtual environment & activate it**
```bash
python3 -m venv venv
source venv/bin/activate
```
> **Install required Python packages**
```bash
pip install -r requirements.txt
```

---

## 🔧 **Configuration**
### **2️⃣ Setup Environment Variables**
Copy the `.env.example` file and update the values:
```bash
cp .env.example .env
nano .env
```
### **.env Configuration**
```ini
MONGO_URI=mongodb://localhost:27017/beampay
BEAM_API_RPC=http://127.0.0.1:10000
TELEGRAM_BOT_TOKEN=your_bot_token_here
WEBHOOK_URL=https://yourserver.com/webhook
```

---

## 🚀 **Running BeamPay Services**
### **3️⃣ Start API & Payment Processor**
#### **Using Systemd (Recommended)**
> **Create a systemd service for API**
```bash
sudo nano /etc/systemd/system/beampay-api.service
```
**Add the following:**
```ini
[Unit]
Description=BeamPay API Service
After=network.target

[Service]
User=root
WorkingDirectory=/path/to/BeamPay
ExecStart=/path/to/BeamPay/venv/bin/uvicorn api:app --host 0.0.0.0 --port 8000
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```
> **Create a systemd service for Payment Scanner**
```bash
sudo nano /etc/systemd/system/beampay-payments.service
```
**Add the following:**
```ini
[Unit]
Description=BeamPay Payment Processor
After=network.target

[Service]
User=root
WorkingDirectory=/path/to/BeamPay
ExecStart=/path/to/BeamPay/venv/bin/python process_payments.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```
> **Enable & Start Services**
```bash
sudo systemctl daemon-reload
sudo systemctl enable beampay-api
sudo systemctl enable beampay-payments
sudo systemctl start beampay-api
sudo systemctl start beampay-payments
```

#### **Using Crontab (Alternative)**
> Run `crontab -e` and add:
```cron
* * * * * pgrep -f "uvicorn api:app" > /dev/null || systemctl restart beampay-api
* * * * * pgrep -f "process_payments.py" > /dev/null || systemctl restart beampay-payments
```

---

## 📡 **Using the API**
> **Get deposit address**
```bash
curl -X POST http://127.0.0.1:8000/create_wallet
```
> **Withdraw funds**
```bash
curl -X POST http://127.0.0.1:8000/withdraw      -H "Content-Type: application/json"      -d '{"from_address": "your_wallet", "to_address": "recipient_wallet", "asset_id": "0", "amount": 1000000}'
```
> **Get balances**
```bash
curl -X GET http://127.0.0.1:8000/balances?address=your_wallet
```

---

## 📊 **Admin Dashboard**
### **4️⃣ Access the Web UI**
1. Open `http://127.0.0.1:8000/dashboard`
2. Monitor **transactions, balances, and users.**
3. Track mismatched balances between the blockchain & database.

---

## 📲 **Telegram Notifications**
> **Enable Telegram monitoring in `.env`**
```ini
TELEGRAM_BOT_TOKEN=your_bot_token_here
```
> **Monitored Events**
✅ Deposit received  
✅ Withdrawal request  
✅ Internal transfers  

---

## 🎯 **Features**
✔️ **Secure API authentication (API keys, IP whitelisting)**  
✔️ **Webhook notifications for deposits/withdrawals**  
✔️ **Automatic address & transaction syncing**  
✔️ **Admin Panel to monitor balances**  
✔️ **Auto-restarting services using systemd**  

---

## Server Security

### **Disable SSH Password Authentication**

For security reasons, it's recommended to disable SSH login using passwords and allow only key-based authentication.

### **Step 1: Get Your Public Key**

Run the following command on your **local PC** (the one that should have access to the server):

```sh
cat ~/.ssh/id_rsa.pub
```

Copy the output, which is your public SSH key.

### **Step 2: Add the Key to the Server**

Log in to your server and open the `authorized_keys` file:

```sh
nano ~/.ssh/authorized_keys
```

Paste the copied public key into the file. Save and exit by pressing **CTRL + X**, then **Y**, and **ENTER**.

### **Step 3: Verify SSH Key Login**

Open a new terminal on your PC and try connecting to the server:

```sh
ssh root@YOUR_SERVER_IP
```

If you can connect without entering a password, the setup is correct.

### **Step 4: Disable Password Authentication**

Now, disable password authentication for SSH to enhance security.

Open the SSH configuration file:

```sh
sudo nano /etc/ssh/sshd_config
```

Find the following line:

```sh
PasswordAuthentication Yes
```

Change it to:

```sh
PasswordAuthentication No
```

**Make sure there is only one occurrence of this setting in the file.** Sometimes, another entry might be commented (`#`) and a duplicate could exist later in the file.

### **Step 5: Restart SSH Service**

Apply the changes by restarting the SSH service:

```sh
sudo systemctl restart sshd
```

---

# 🔧 Installation BEAM

## Setup the Node, Wallet, and Wallet API

### Create Directory
```sh
mkdir beam-wallet
cd beam-wallet
```

## Install Beam Node

**Get the latest version of Beam Node** [here](https://github.com/BeamMW/beam/releases)

```sh
wget -c https://github.com/BeamMW/beam/releases/download/latest/linux-beam-node.tar.gz -O - | tar -xz
```

- Add `horizon_hi=1440` at the end of `beam-node.cfg`.
- Add `fast_sync=1` at the end of `beam-node.cfg`.
- Add `peer=eu-nodes.mainnet.beam.mw:8100,us-nodes.mainnet.beam.mw:8100` at the end of `beam-node.cfg`.

Run the node:

```sh
./beam-node
```

## Install CLI Beam Wallet

**Get the latest version of Beam Wallet CLI** [here](https://github.com/BeamMW/beam/releases)

```sh
wget -c https://github.com/BeamMW/beam/releases/download/latest/linux-beam-wallet-cli.tar.gz -O - | tar -xz
```

**To create a wallet use:**
```sh
./beam-wallet init
```
💡 **Keep your seed phrase in a safe place!**

**To restore a wallet use:**
```sh
./beam-wallet restore --seed_phrase="<semicolon-separated list of 12 seed phrase words>"
```

For more information, read [this guide](https://documentation.beam.mw/en/latest/rtd_pages/user_backup_restore.html?highlight=restore).

## Install Beam Wallet API

**Get the latest version of Beam Wallet API** [here](https://github.com/BeamMW/beam/releases)

```sh
wget -c https://github.com/BeamMW/beam/releases/download/latest/linux-wallet-api.tar.gz -O - | tar -xz
```

### Configuration:
Edit `wallet-api.cfg` and add the following:
```ini
use_http=1
```

### Start Wallet API:
```sh
./wallet-api
```

For more information, read [the API documentation](https://github.com/BeamMW/beam/wiki/Beam-wallet-protocol-API).


---

## ⚡ **Contributing**
1. Fork the repository.
2. Make your changes.
3. Submit a Pull Request (PR).

---

## 🔥 **Support & Community**
📢 Join BEAM **Telegram Group**: `https://t.me/BeamPrivacy`  


#### **BeamPay is created by** [@vsnation](https://t.me/vsnation)

### **Donate Address**
- **Beam**: `203ae2ac20c67c67e035e580284c472156356e783c33af4c74a87ab84169d433b01`
- **BTC**: `3QVqpztjTXrfDCDaQiaVanHyjW6yGsWTRd`

