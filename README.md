# BeamPay | Manual for deploy

## Mongodb Installation Guide
**Ubuntu:** https://docs.mongodb.com/manual/tutorial/install-mongodb-on-ubuntu/

## Install Beam wallet api

**Create Directory**

`mkdir beam-wallet`

`cd beam-wallet`

**Get last version of the beam-wallet** [here](https://github.com/BeamMW/beam/releases)

`wget -c https://github.com/BeamMW/beam/releases/download/beam-3.1.5765/linux-beam-wallet-cli-3.1.5765.tar.gz -O - | tar -xz`

**To create wallet use:** `./beam-wallet init` Keep in safe seed

**To restore wallet use:** `./beam-wallet restore --seed_phrase=<semicolon separated list of 12 seed phrase words>;`

For more information read [here](https://documentation.beam.mw/en/latest/rtd_pages/user_backup_restore.html?highlight=restore)


`wget -c https://github.com/BeamMW/beam/releases/download/beam-3.1.5765/linux-wallet-api-3.1.5765.tar.gz -O - | tar -xz`

**Specify use_http=1 in the wallet-api.cfg**

**To start wallet_api use:** `./wallet-api`

For more information read [here](https://github.com/BeamMW/beam/wiki/Beam-wallet-protocol-API)

## Install Python
Please, install Python using link below

**Ubuntu:** https://websiteforstudents.com/installing-the-latest-python-3-7-on-ubuntu-16-04-18-04/


`pip3 install -r requirements.txt`

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
WorkingDirectory=/root/beampay
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

**Done! Manual created by** [@vsnation](https://t.me/vsnation)

### Donate Address
**Beam**: 203ae2ac20c67c67e035e580284c472156356e783c33af4c74a87ab84169d433b01
![Donate address](https://i.imgur.com/RJVr05X.png)

**BTC**: 3QVqpztjTXrfDCDaQiaVanHyjW6yGsWTRd
