import os
import argparse
import json
import logging
import threading
import traceback
import datetime
import uuid
from _decimal import Decimal, ROUND_UP

import schedule
import time
import requests
from flask import Flask, request, jsonify
from pymongo import MongoClient

# Logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__, static_folder=None)
app.secret_key = os.urandom(24)

with open('config.json') as conf_file:
    conf = json.load(conf_file)
    connectionString = conf['mongo']['connectionString']
    httpprovider = conf['httpprovider']
    port = conf['port']
    logs_tg_channel_id = conf['tg_channel_id']

DEFAULT_FEE = 200

groth_in_beams = 100000000
client = MongoClient(connectionString)
db = client.get_default_database()
col_addresses = db['addresses']
col_updates = db['updates']
col_rates = db['rates']


class WalletAPI:

    def __init__(self):
        self.update_addresses_expiration()
        schedule.every(5).seconds.do(self.update_address_balance)
        schedule.every(10).seconds.do(self.update_rate)
        schedule.every(60).seconds.do(self.update_addresses_expiration)
        threading.Thread(target=self.pending_tasks).start()


    @staticmethod
    def pending_tasks():
        while True:
            schedule.run_pending()
            time.sleep(5)


    def update_rate(self):
        try:
            usdt_price = float(requests.get('https://api.binance.com/api/v1/ticker/24hr?symbol=BEAMUSDT', timeout=5).json()['lastPrice'])
            col_rates.update_one(
                {
                    "_id": "BEAMUSDT"
                },
                {
                    "$set": {
                        "_id": "BEAMUSDT",
                        "rate": usdt_price,
                        "updated_at": datetime.datetime.now()
                    }
                }, upsert=True
            )
        except Exception as exc:
            print(exc)
            traceback.print_exc()
            logger.error("Can't retreive the rate: %s" % str(exc))

    def update_addresses_expiration(self):
        try:
            addr_list = self.addr_list()['result']
            for _item in addr_list:
                try:
                    col_addresses.update_one(
                        {
                            "address": _item['address']
                        },
                        {
                            "$set": { "expired": _item['expired'] }
                        }
                    )
                except Exception as exc:
                    traceback.print_exc()
        except Exception as exc:
            print(exc)
            traceback.print_exc()

    """
        Update user's balance using transactions history
    """
    def update_address_balance(self):
        try:
            print('Update Balances %s' % datetime.datetime.now())
            response = self.get_txs_list(count=1000)

            for _tx in response['result']:
                try:
                    _receiver = col_addresses.find_one({"address": _tx['receiver']})
                    _is_tx_exist_deposit = col_updates.find_one(
                        {
                            "txId": _tx['txId'], "type": "deposit"
                        }
                    ) is not None

                    if _receiver is not None and not _is_tx_exist_deposit and \
                                    _tx['status'] == 3:
                        value_in_groths = int(_tx['value'])
                        new_balance = _receiver['balance'] + value_in_groths

                        col_addresses.update_one(
                            {
                                "address": _tx['receiver']
                            },
                            {
                                "$set":
                                    {
                                        "balance": int(new_balance)
                                    }
                            }
                        )
                        _tx.update({'type': "deposit"})
                        print("*Deposit Success*\n"
                              "Balance of address %s has recharged on *%s* Beams." % (
                            _tx['receiver'], value_in_groths
                        ))
                        _id = str(uuid.uuid4())
                        col_updates.insert_one({"_id": _id, **_tx})


                    _is_tx_exist_withdraw = col_updates.find_one(
                        {"txId": _tx['txId'], "type": "withdraw"}
                    ) is not None

                    _sender = col_addresses.find_one({"address": _tx['sender']})

                    if _sender is not None and not _is_tx_exist_withdraw and \
                            (_tx['status'] == 4 or _tx['status'] == 3 or _tx['status'] == 2):

                        value_in_groths = int(_tx['value']) + _tx['fee']

                        if _tx['status'] == 4 or _tx['status'] == 2:
                            _tx.update({'type': "withdraw"})
                            col_updates.insert_one(_tx)
                            new_locked = int(_sender['locked']) - value_in_groths
                            new_balance = int(_sender['balance']) + value_in_groths
                            col_addresses.update_one(
                                    {
                                        "address": _tx['sender']
                                    },
                                    {
                                        "$set":
                                            {
                                                "balance": int(new_balance),
                                                "locked": int(new_locked),
                                            }
                                    }
                                )

                        else:
                            # on the way if user will send transaction from the wallet
                            new_locked = int(_sender['locked']) - value_in_groths
                            if new_locked >= 0:
                                col_addresses.update_one(
                                    {
                                        "address": _tx['sender']
                                    },
                                    {
                                        "$set":
                                            {
                                                "locked": int(new_locked)
                                            }
                                    }
                                )
                            else:
                                new_balance = int(_sender['balance']) - value_in_groths
                                col_addresses.update_one(
                                    {
                                        "address": _tx['sender']
                                    },
                                    {
                                        "$set":
                                            {
                                                "balance": int(new_balance)
                                            }
                                    }
                                )
                            _tx.update({'type': "withdraw"})
                            print("*Withdrawal Success*\n"
                                    "Balance of address %s has recharged on *%s* Beams." % (
                                    _tx['sender'], value_in_groths
                            ))
                            _id = str(uuid.uuid4())
                            col_updates.insert_one({"_id": _id, **_tx})

                except Exception as exc:
                    print(exc)
                    traceback.print_exc()
        except Exception as exc:
            print(exc)


    """
        Create new wallet address
    """
    def create_user_wallet(self):
        response = requests.post(
            httpprovider,
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "create_address",
                    "params":
                        {
                            "expiration": "never"
                        }
                })).json()

        print(response)
        return response


    """
        Beam wallet API | Validate address
    """
    def validate_address(self, address):
        response = requests.post(
            httpprovider,
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "validate_address",
                    "params":
                        {
                            "address": address
                        }
                })).json()
        return response


    """
        Beam wallet API | Addr List
    """
    def addr_list(self):
        response = requests.post(
            httpprovider,
            data=json.dumps(
                {
                    "jsonrpc":"2.0",
                    "id": 8,
                    "method":"addr_list",
                    "params":
                    {
                        "own" : True
                    }
                })).json()
        return response


    """
        Beam wallet API | Delete SBBS address
    """
    def delete_address(self, address):
        response = requests.post(
            httpprovider,
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 8,
                    "method": "delete_address",
                    "params":
                        {
                            "address": "%s" % address
                        }
                })).json()

        print(response)
        return response


    """
        Beam wallet API | Edit SBBS address
        Expiration: expired/never/24h
    """
    def edit_address(self, address, comment="", expiration="never"):
        response = requests.post(
            httpprovider,
            data=json.dumps(
                {
                "jsonrpc": "2.0",
                "id": 8,
                "method": "edit_address",
                "params":
                {
                    "address": address,
                    "comment": comment,
                    "expiration": expiration
                }
            })).json()

        print(response)
        return response


    """
        Beam wallet api | Send Transaction
    """
    def send_transaction(
            self,
            value,
            fee,
            from_address,
            to_address,
            comment
    ):

        coins = get_coins(fee=fee, value=value)
        response = requests.post(
            httpprovider,
            json.dumps({
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tx_send",
                "params":
                    {
                        "value": value,
                        "fee": fee,
                        "from": from_address,
                        "address": to_address,
                        "comment": comment,
                        "coins": coins
                    }
            })).json()
        print(response)
        return response



    """
        Cancel Transaction
    """
    def cancel_tx(self, tx_id):
        response = requests.post(
            httpprovider,
            data=json.dumps(
                {
                    "jsonrpc":"2.0",
                    "id": 4,
                    "method":"tx_cancel",
                    "params":
                    {
                        "txId" : tx_id
                    }
                }
            )).json()

        print(response)
        return response



    """
        Fetch list of txs
    """
    def get_txs_list(self, count=100, skip=0, filter={}):
        response = requests.post(
            httpprovider,
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 8,
                    "method": "tx_list",
                    "params":
                        {
                            "filter": filter,
                            "skip": skip,
                            "count": count
                        }
                })).json()

        return response


    """
        Get wallet status
    """
    def wallet_status(self):
        response = requests.post(
            httpprovider,
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 6,
                    "method": "wallet_status",
                })).json()

        return response


    """
        Get transaction status
    """
    def get_tx_status(self, tx_id):
        response = requests.post(
            httpprovider,
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tx_status",
                    "params":
                        {
                            "txId": tx_id
                        }
                })).json()

        return response


    """
        Split txs
    """
    def split_coins(self, coins, fee):
        response = requests.post(
            httpprovider,
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tx_split",
                    "params":
                        {

                            "coins": coins,
                            "fee": fee
                        }
                })).json()

        return response


    """
        Get utxo status
    """
    def get_utxo(self, count=100, skip=0):
        response = requests.post(
            httpprovider,
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 6,
                    "method": "get_utxo",
                    "params":
                        {
                            "count": count,
                            "skip": skip
                        }
                })).json()
        return response


def get_coins(fee, value):
    try:
        free_utxos = [{"amount": _x['amount'], "id": _x['id']} for _x in wallet_api.get_utxo(count=10000)['result'] if _x['status'] == 1]
        full_value_utxo = None
        coins = []
        coins_sum = 0
        fee_utxos = []
        fee_sum = 0
        for _utxo in free_utxos:
            try:
                if _utxo['amount'] == value:
                    full_value_utxo = _utxo['id']
                    free_utxos.remove(_utxo)
                    break
            except Exception as exc:
                print(exc)

        if full_value_utxo is None:
            for _utxo in sorted(free_utxos, key= lambda x: x['amount'], reverse=True):
                try:
                    if coins_sum < value:
                        coins_sum += _utxo['amount']
                        coins.append(_utxo['id'])
                        free_utxos.remove(_utxo)
                    else:
                        break
                except Exception as exc:
                    print(exc)

        for _utxo in free_utxos:
            try:
                if fee_sum < fee:
                    fee_sum += _utxo['amount']
                    fee_utxos.append(_utxo['id'])
                else:
                    break
            except Exception as exc:
                print(exc)


        if full_value_utxo is not None:
            coins = [full_value_utxo, *fee_utxos]
        else:
            coins = [*coins, *fee_utxos]
        print(coins)

        return coins
    except Exception as exc:
        traceback.print_exc()

wallet_api = WalletAPI()


@app.route('/', methods=['GET'])
def index():
    endpoints = []
    for rule in app.url_map.iter_rules():
        if "GET" in rule.methods:
            endpoints.append(str(rule))
    return jsonify(endpoints=endpoints)


@app.route('/status', methods=['GET'])
def status():
    try:
        result = wallet_api.wallet_status()
        return jsonify(status=True, result=result)
    except Exception as exc:
        print(exc)
        traceback.print_exc()
        return jsonify(status=False, reason="Please, check wallet-api connection!")


@app.route('/get_current_rate', methods=['GET'])
def get_rate():
    try:
        result = list(col_rates.find())
        return jsonify(data=result)
    except Exception as exc:
        print(exc)
        traceback.print_exc()
        return jsonify(status=False, reason=str(exc))


"""
    Create SBBS address
"""
@app.route('/create_address', methods=['GET'])
def create_address():
    try:
        _id = request.args.get('id')
        if _id is None:
            return jsonify({"status": "failed", "reason": "Incorrect id"})
        else:
            data = col_addresses.find_one({"_id": _id})
            if data is None:
                address = wallet_api.create_user_wallet()['result']
                col_addresses.insert_one(
                    {
                        "_id": _id,
                        "address": address,
                        "balance": 0,
                        "locked": 0,
                        "expired": False,
                        "created_at": datetime.datetime.now(),
                        "updated_at": datetime.datetime.now()
                    }
                )
                return jsonify(id=_id, address=address)
            else:
                return jsonify(
                    {
                        "status": "failed",
                        "reason": "id already exists"
                    })


    except Exception as exc:
        print(exc)
        traceback.print_exc()
        return jsonify(status="failed", reason=str(exc))


"""
    Get address details
"""
@app.route('/get_address', methods=['GET'])
def get_address_data():
    try:
        address = request.args.get('address')
        _id = request.args.get('id')

        if address is None and _id is None:
            return jsonify({"status": "failed", "reason": "Incorrect id or address"})

        if address is not None:
            data = col_addresses.find_one({"address": address})
        else:
            data = col_addresses.find_one({"_id": _id})

        if data is None:
            return jsonify(
                {
                    "status": "failed",
                    "reason": "This address or id incorrect"
                })
        else:
            return data

    except Exception as exc:
        print(exc)
        traceback.print_exc()
        return jsonify(status="failed", reason=str(exc))


"""
    Get list of all addresses, page arg
"""
@app.route('/get_all_addresses', methods=['GET'])
def get_addresses_list():
    try:
        page = request.args.get('page')
        if page is None or not str(page).isdigit() or int(page) <= 0:
            page = 1
        page = int(page)
        page_count = int(Decimal(col_addresses.find().count() / 100).quantize(Decimal('1.'), rounding=ROUND_UP))
        data = list(col_addresses.find({}, projection={'_id': True, 'address': True, 'balance': True, 'locked': True, 'expired': True}).skip((page - 1) * 100))
        return jsonify(data=data, pages_count=page_count)

    except Exception as exc:
        print(exc)
        traceback.print_exc()
        return jsonify(status="failed", reason=str(exc))


"""
    Get balance of address by address/id
"""
@app.route('/get_balance', methods=['GET'])
def get_balance():
    try:
        address = request.args.get('address')
        _id = request.args.get('id')

        if address is None and _id is None:
            return jsonify({"status": "failed", "reason": "You haven't specified id or address"})

        if address is not None:
            data = col_addresses.find_one({"address": address}, projection={'_id': True, 'address': True, 'balance': True, 'locked': True})

        else:
            data = col_addresses.find_one({"_id": _id}, projection={'_id': True, 'address': True, 'balance': True, 'locked': True})

        if data is None:
            return jsonify(
                {
                    "status": "failed",
                    "reason": "This address or id incorrect"
                })
        else:
            beam_usdt_rate = col_rates.find_one({"_id": "BEAMUSDT"})['rate']
            balance_in_usdt = float("{0:.8f}".format(float(beam_usdt_rate * (data['balance'] / groth_in_beams))))
            data.update({"balance_in_usdt": balance_in_usdt})
            return data

    except Exception as exc:
        print(exc)
        traceback.print_exc()
        return jsonify(status="failed", reason=str(exc))


"""
    Delete address
    * Args *
    * address
    * type (full, wallet, db)
"""
@app.route('/delete_address', methods=['GET'])
def delete_address():
    try:
        _address = request.args.get('address')
        _type = request.args.get('type')
        if _address is None:
            return jsonify({"status": "failed", "reason": "Incorrect address param"})

        if _address is not None:
            if _type == 'full':
                response = wallet_api.delete_address(address=_address)
                col_addresses.remove({"address": _address})
            elif _type == 'wallet':
                response = wallet_api.delete_address(address=_address)
            elif _type == 'db':
                col_addresses.remove({"address": _address})
                response = "Address deleted"
            else:
                return jsonify({"status": "failed"})

            return response
    except Exception as exc:
        print(exc)
        traceback.print_exc()
        return jsonify(status="failed", reason=str(exc))


"""
    Addr List
"""
@app.route('/wallet_addr_list', methods=['GET'])
def addr_list():
    try:
        response = wallet_api.addr_list()
        return response
    except Exception as exc:
        print(exc)
        traceback.print_exc()
        return jsonify(status="failed", reason=str(exc))


"""
    Edit address
    * Args *
    * address (required)
    * comment (optional)
    * expiration(never as default)
"""
@app.route('/edit_address', methods=['GET'])
def edit_address():
    try:
        _address = request.args.get('address')
        comment = request.args.get('comment')
        expiration = request.args.get('expiration')
        if _address is None:
            return jsonify({"status": "failed", "reason": "Incorrect address param"})

        elif comment is None:
            comment = ""

        elif expiration is None:
            expiration = "never"

        response = wallet_api.edit_address(
            address=_address,
            comment=comment,
            expiration=expiration
        )
        return response
    except Exception as exc:
        print(exc)
        traceback.print_exc()
        return jsonify(status="failed", reason=str(exc))



"""
    Split tx
    * Args *
    * coins
"""
@app.route('/tx_split', methods=['GET'])
def tx_split():
    try:
        coins = request.args.getlist('coins')
        if coins is None or not isinstance(coins, list) or len(coins) == 0:
            return jsonify({"status": "failed", "reason": "Invalid coins param. It should be the not empty list"})
        coins = list(map(int, coins))
        fee = int(len(coins) * 10 + 10) + 100

        response = wallet_api.split_coins(coins=coins, fee=fee)
        return response

    except Exception as exc:
        print(exc)
        traceback.print_exc()
        return jsonify(status="failed", reason=str(exc))



"""
    Cancel tx
    * Args *
    * tx_id
"""
@app.route('/cancel_tx', methods=['GET'])
def cancel_tx():
    try:
        tx_id = request.args.get('tx_id')
        if tx_id is None:
            return jsonify({"status": "failed", "reason": "Invalid tx_id param!"})

        response = wallet_api.cancel_tx(tx_id)
        return response
    except Exception as exc:
        print(exc)
        return jsonify(status="failed", reason=str(exc))


"""
    Validate address
    * Args *
    * address
"""
@app.route('/validate_address', methods=['GET'])
def validate_address():
    try:
        address = str(request.args.get('address'))
        if address is None:
            return jsonify({"status": "failed", "reason": "Invalid address param!"})

        response = wallet_api.validate_address(address=address)
        return response
    except Exception as exc:
        print(exc)
        return jsonify(status="failed", reason=str(exc))


"""
    Get list of transactions
"""
@app.route('/get_txs_list', methods=['GET'])
def get_txs_list():
    try:
        count = request.args.get('count')
        skip = request.args.get('skip')
        _filter = request.args.get('filter')
        if count is None:
            count = 100
        elif count.isdigit():
            count = int(count)
        else:
            count = 100

        if skip is None:
            skip = 0
        elif skip.isdigit():
            skip = int(skip)
        else:
            skip = 0

        if _filter is not None or not isinstance(_filter, dict):
            return jsonify({"status": "failed", "reason": "Invalid filter param!"})
        else:
            _filter = {}


        response = wallet_api.get_txs_list(
            count=count,
            skip=skip,
            filter=_filter
        )
        return response
    except Exception as exc:
        print(exc)
        return jsonify(status="failed", reason=str(exc))


"""
    Get wallet status(current balance, block, etc..)
"""
@app.route('/get_wallet_status', methods=['GET'])
def get_wallet_status():
    try:
        response = wallet_api.wallet_status()
        return response
    except Exception as exc:
        print(exc)
        return jsonify(status="failed", reason=str(exc))


"""
    Get tx_status
    * Args *
    * tx_id
"""
@app.route('/get_tx_status', methods=['GET'])
def get_tx_status():
    try:
        tx_id = request.args.get('tx_id')
        if tx_id is None:
            return jsonify({"status": "failed", "reason": "Incorrect tx_id param!"})

        response = wallet_api.get_tx_status(tx_id=tx_id)
        return response
    except Exception as exc:
        print(exc)
        return jsonify(status="failed", reason=str(exc))


"""
    Get define get utxo
    * Args *
    * count(optional)
    * skip(optional)
"""
@app.route('/get_utxo', methods=['GET'])
def get_utxo():
    try:
        count = request.args.get('count')
        skip = request.args.get('skip')
        if count is None:
            count = 100
        elif count.isdigit():
            count = int(count)
        else:
            count = 100

        if skip is None:
            skip = 0
        elif skip.isdigit():
            skip = int(skip)
        else:
            skip = 0

        response = wallet_api.get_utxo(
            count=count,
            skip=skip
        )
        return response
    except Exception as exc:
        print(exc)
        return jsonify(status="failed", reason=str(exc))


"""
    Send Transaction
    * Args *
    * value
    * fee
    * from_address
    * to_address
    * comment
"""
@app.route('/send_transaction', methods=['GET'])
def send_transaction():
    try:
        value = int(request.args.get('value'))
        fee = int(request.args.get('fee'))
        from_address = request.args.get('from_address')
        to_address = request.args.get('to_address')
        comment = request.args.get('comment')
        print(value, fee, from_address, to_address, comment)

        address_data = col_addresses.find_one({"address": from_address})
        if address_data is None:
            return jsonify(status="failed", reason="This address is not found in the BeamPay!")

        last_balance = address_data['balance']
        last_locked = address_data['locked']

        if int(last_balance) < int(value + fee):
            return jsonify(status="failed", reason="Not enough balance on the address to withdraw!")

        response = wallet_api.send_transaction(
            value=value,
            fee=fee,
            from_address=from_address,
            to_address=to_address,
            comment=comment
        )

        new_balance = last_balance - (value + fee)
        new_locked = last_locked + (value + fee)
        col_addresses.update_one(
            {
                "address": from_address
            },
            {
                "$set": {
                    "balance": new_balance,
                    "locked": new_locked
                }
            }
        )
        return response
    except Exception as exc:
        print(exc)
        return jsonify(status="failed", reason="Check all parameters. You should specify value, fee, from_address, to_address, comment")


"""
    Get updates
    * Args *
    * offset (optional)
"""
@app.route('/get_updates', methods=['GET'])
def get_updates():
    try:
        offset = request.args.get('offset')
        offset = 0 if offset is None else int(offset)
        result = list(col_updates.find(projection={'_id': False}).skip(int(offset)).limit(100))
        return jsonify(data=result)
    except Exception as exc:
        print(exc)
        traceback.print_exc()
        return jsonify(status="failed", reason="Incorrect offset")


def main():
    app.run(host="127.0.0.1", port=port, debug=False)


if __name__ == '__main__':
    main()
