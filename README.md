# BeamPay | Manual for deploy

## Mongodb Installation Guide
**Ubuntu:** https://docs.mongodb.com/manual/tutorial/install-mongodb-on-ubuntu/

`wget -qO - https://www.mongodb.org/static/pgp/server-4.2.asc | sudo apt-key add -`

`echo "deb [ arch=amd64 ] https://repo.mongodb.org/apt/ubuntu bionic/mongodb-org/4.2 multiverse" | sudo tee /etc/apt/sources.list.d/mongodb-org-4.2.list`

`sudo apt-get update`

`sudo apt-get install -y mongodb-org`

`sudo service mongod start`

`sudo service mongod status`

# Install Beam Node, Wallet, API

**Create Directory**

`mkdir beam-wallet`

`cd beam-wallet`

## Install Beam Node

**Get last version of the beam-wallet** [here](https://github.com/BeamMW/beam/releases)

`wget -c https://github.com/BeamMW/beam/releases/download/beam-3.1.5765/linux-beam-node-3.1.5765.tar.gz -O - | tar -xz`

Add `--horizon_hi=1440` at the end of beam-node.cfg.

Run the node 

`./beam-node`

## Install CLI Beam wallet

**Get last version of the beam-wallet** [here](https://github.com/BeamMW/beam/releases)

`wget -c https://github.com/BeamMW/beam/releases/download/beam-3.1.5765/linux-beam-wallet-cli-3.1.5765.tar.gz -O - | tar -xz`

**To create wallet use:** `./beam-wallet init` Keep in safe seed

**To restore wallet use:** `./beam-wallet restore --seed_phrase=<semicolon separated list of 12 seed phrase words>;`

For more information read [here](https://documentation.beam.mw/en/latest/rtd_pages/user_backup_restore.html?highlight=restore)

## Install Beam Wallet API

**Get last version of the beam-wallet** [here](https://github.com/BeamMW/beam/releases)

`wget -c https://github.com/BeamMW/beam/releases/download/beam-3.1.5765/linux-wallet-api-3.1.5765.tar.gz -O - | tar -xz`

**Specify use_http=1 in the wallet-api.cfg**

**To start wallet_api use:** `./wallet-api`

For more information read [here](https://github.com/BeamMW/beam/wiki/Beam-wallet-protocol-API)

## Install Python
Please, install Python using the link below

**Ubuntu:** https://www.digitalocean.com/community/tutorials/how-to-install-python-3-and-set-up-a-programming-environment-on-ubuntu-18-04-quickstart

`sudo apt update`

`sudo apt -y upgrade`

`sudo apt install software-properties-common build-essential zlib1g-dev libncurses5-dev libgdbm-dev libnss3-dev libssl-dev libreadline-dev libffi-dev wget python3-dev python3-setuptools`

`sudo apt install -y python3-pip`

`cd /root/BeamPay; pip3 install -r requirements.txt`

## Create Init script

`cd /etc/systemd/system`

`nano beampay.service`

**Insert text below into beampay.service file**.

```
[Unit]
Description=beampay
After=network.target
After=mongodb.service


[Service]
Type=simple
WorkingDirectory=/root/BeamPay
ExecStart=/usr/bin/python3 beampay.py
RestartSec=10
SyslogIdentifier=beampay
TimeoutStopSec=120
TimeoutStartSec=2
StartLimitInterval=120
StartLimitBurst=5
KillMode=mixed
Restart=always
PrivateTmp=true


[Install]
WantedBy=multi-user.target
```

`systemctl daemon-reload`

`systemctl enable beampay.service`

`systemctl start beampay.service`

## Security

**We need to disable ssh connection using the password.**

**Open the terminal on your PC(only this PC will have access to the server) and enter below command to get your public key.** 

`cat .ssh/id_rsa.pub`

*Please, copy the result and paste it into the .ssh/authorized_keys on the server*

`nano .ssh/authorized_keys`

Paste it using `Ctrl + V`

*Open a new tab in the PC terminal and check that you can connect to your server without asking the password*

`ssh root@IP`

*If all is okay, you need to open sshd_config to disable password auth*

`sudo nano /etc/ssh/sshd_config`

Find string with the name "PasswordAuthentication Yes" and change it to No. **Make sure that only one note exists.** There are cases when one of the notes is commented using "#" and other located at the end of file

`PasswordAuthentication No`

**Done! Manual created by** [@vsnation](https://t.me/vsnation)

### Donate Address
**Beam**: 203ae2ac20c67c67e035e580284c472156356e783c33af4c74a87ab84169d433b01
![Donate address](https://i.imgur.com/RJVr05X.png)

**BTC**: 3QVqpztjTXrfDCDaQiaVanHyjW6yGsWTRd
