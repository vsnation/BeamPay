import requests
import json

class BEAMWalletAPI:
    def __init__(self, api_url):
        """
        Initialize the BEAM Wallet API client.

        :param api_url: The full URL to the BEAM Wallet API (e.g., 'http://127.0.0.1:10000')
        """
        self.api_url = api_url
        self.headers = {
            'Content-Type': 'application/json',
        }

    def _post(self, method, params=None):
        """
        Send a JSON-RPC request to the BEAM Wallet API.

        :param method: The API method to call (e.g., 'create_address').
        :param params: A dictionary of parameters for the API call.
        :return: The 'result' field from the API response.
        """
        payload = {
            'jsonrpc': '2.0',
            'id': 1,
            'method': method,
            'params': params or {}
        }

        try:
            response = requests.post(self.api_url, headers=self.headers, data=json.dumps(payload))
            response.raise_for_status()  # Raise an exception for HTTP errors
            result = response.json()
            if 'error' in result:
                raise Exception(f"Error {result['error']['code']}: {result['error']['message']}")
            if "result" in result:
                return result.get('result')
            elif "assets" in result:
                return result['assets']
        except requests.exceptions.RequestException as e:
            raise Exception(f"HTTP Request failed: {e}")

    def create_address(self, label=None, wallet_type="regular", expiration='never', use_default_signature=False):
        """
        Create a new payment address.

        :param label: An optional label for the address.
        :param expiration: Address expiration type ('never', '24h', 'auto').
        :param use_default_signature: If True, the address will use the default wallet signature (new in API v7.3).
        :return: The newly created address.
        """
        params = {}
        if wallet_type:
            params.update({"type": wallet_type})
        if label:
            params.update({"comment": label})
        if expiration:
            params.update({"expiration": expiration})

        if use_default_signature:
            params.update({"use_default_signature": use_default_signature})

        return self._post('create_address', params)

    def wallet_status(self):
        """
        Retrieve the current status of the wallet.
        """
        return self._post('wallet_status')

    def generate_tx_id(self):
        """
        Generate a unique transaction ID.
        """
        return self._post('generate_tx_id')

    def tx_cancel(self, tx_id):
        """
        Cancel a transaction by its ID.
        """
        params = {'txId': tx_id}
        return self._post('tx_cancel', params)

    def set_confirmations_count(self, count):
        """
        Set the number of confirmations required for a transaction.
        """
        params = {'count': count}
        return self._post('set_confirmations_count', params)

    def get_confirmations_count(self):
        """
        Retrieve the current confirmation count setting.
        """
        return self._post('get_confirmations_count')

    def sign_message(self, message):
        """
        Sign a message using the wallet's private key.
        """
        params = {'message': message}
        return self._post('sign_message', params)

    def verify_signature(self, message, signature):
        """
        Verify a message's signature.
        """
        params = {'message': message, 'signature': signature}
        return self._post('verify_signature', params)

    def derive_id(self, own_id, is_offline=False):
        """
        Derive a new ID based on an existing one.
        """
        params = {'own_id': own_id, 'is_offline': is_offline}
        return self._post('derive_id', params)

    def block_details(self, height):
        """
        Retrieve block details by height.
        """
        params = {'height': height}
        return self._post('block_details', params)

    def delete_address(self, address):
        """
        Delete a specific address from the wallet.

        :param address: The address to delete.
        :return: Confirmation of deletion.
        """
        params = {'address': address}
        return self._post('delete_address', params)

    def edit_address(self, address, label=None, expiration=None):
        """
        Edit a specific address. Allows changing the label or expiration.

        :param address: The address to edit.
        :param label: An optional new label.
        :param expiration: New expiration value ('never', '24h', 'expired').
        :return: Confirmation of edit.
        """
        params = {
            'address': address,
            # 'comment': label,
            'expiration': expiration
        }
        return self._post('edit_address', params)

    def addr_list(self, own=True):
        """
        Retrieve a list of addresses stored in the wallet database.

        :param own: If True, returns only own addresses. If False, returns peer addresses.
        :return: List of addresses.
        """
        params = {'own': own}
        return self._post('addr_list', params)

    def validate_address(self, address):
        """
        Validate a BEAM address.

        :param address: The address to validate.
        :return: Validation details including whether the address is valid and if it belongs to the wallet.
        """
        params = {'address': address}
        return self._post('validate_address', params)

    def tx_send(self, value, fee, sender, receiver, comment=None, asset_id=0, offline=False):
        """
        Send BEAM or assets to a given address.

        :param value: Amount to send in Groths or asset Groths.
        :param fee: Transaction fee in BEAM Groths.
        :param receiver: Receiver's address or token.
        :param comment: An optional transaction comment.
        :param asset_id: Asset ID (0 for BEAM).
        :param offline: If True, starts an offline transaction (default is False).
        :return: Transaction ID of the sent transaction.
        """
        params = {
            "value": value,
            "address": receiver,
            "asset_id": asset_id,
            "fee": fee
        }
        if comment:
            params["comment"] = comment
        if sender:
            params["from"] = sender
        if offline:
            params["offline"] = offline

        return self._post('tx_send', params)

    def get_asset_info(self, asset_id):
        """
        Retrieve asset information from the local database.

        :param asset_id: The ID of the asset to retrieve info about.
        :return: Asset details including metadata, emission, and ownership status.
        """
        params = {'asset_id': asset_id}
        return self._post('get_asset_info', params)

    def tx_status(self, tx_id):
        """
        Get the status and extended information of a specific transaction.

        :param tx_id: Transaction ID to query.
        :return: Detailed transaction information, including status and type.
        """
        params = {'txId': tx_id}
        return self._post('tx_status', params)

    def tx_list(self, filter=None, count=100, skip=0, rates=False):
        """
        Get the list of transactions.
        """
        params = {
            "count": count,
            "skip": skip
        }
        if filter:
            params["filter"] = filter

        return self._post('tx_list', params)

    def get_utxo(self, count=0, skip=0, sort_field="amount", sort_direction="asc", filter={}):
        """
        Retrieve a list of unlocked UTXOs (Unspent Transaction Outputs).

        :param count: Number of UTXOs to retrieve (0 for all).
        :param skip: Number of UTXOs to skip (default is 0).
        :param asset_id: Filter UTXOs by specific asset_id (0 for BEAM, >0 for CAs).
        :param sort_field: Field to sort by (e.g., 'amount', 'asset_id').
        :param sort_direction: Sorting direction ('asc' or 'desc').
        :return: List of UTXOs.
        """
        params = {
            "count": count,
            "skip": skip,
            "sort": {
                "field": sort_field,
                "direction": sort_direction
            }
        }

        if filter is not None:
            params['assets'] = True
            params["filter"] = filter


        return self._post("get_utxo", params)
    

    def assets_list(self, refresh=False, height=None):
        """
        Retrieve the list of registered assets.

        :param refresh: If True, refreshes the asset list from the node.
        :param height: The blockchain height up to which assets should be listed.
        :return: List of assets with details.
        """
        params = {
            'refresh': refresh,
        }
        if height:
            params.update({"height": height})
        return self._post('assets_list', params)

    def assets_swap_offers_list(self):
        """
        Retrieve all available asset swap offers.

        :return: List of asset swap offers.
        """
        return self._post('assets_swap_offers_list')

    def assets_swap_create(self, send_amount, send_asset_id, receive_amount, receive_asset_id, minutes_before_expire, comment=None):
        """
        Create a new asset swap offer.

        :param send_amount: Amount of asset to send.
        :param send_asset_id: Asset ID of the asset to send.
        :param receive_amount: Amount of asset to receive.
        :param receive_asset_id: Asset ID of the asset to receive.
        :param minutes_before_expire: Expiration time in minutes for the offer.
        :param comment: An optional comment for the offer.
        :return: Details of the created asset swap offer.
        """
        params = {
            "send_amount": send_amount,
            "send_asset_id": send_asset_id,
            "receive_amount": receive_amount,
            "receive_asset_id": receive_asset_id,
            "minutes_before_expire": minutes_before_expire
        }
        if comment:
            params["comment"] = comment
        return self._post('assets_swap_create', params)

    def assets_swap_accept(self, offer_id):
        """
        Accept an existing asset swap offer.

        :param offer_id: ID of the asset swap offer to accept.
        :return: Details of the accepted offer and related transaction ID.
        """
        params = {'offer_id': offer_id}
        return self._post('assets_swap_accept', params)

    def assets_swap_cancel(self, offer_id):
        """
        Cancel an existing asset swap offer.

        :param offer_id: ID of the asset swap offer to cancel.
        :return: Confirmation of cancellation.
        """
        params = {'offer_id': offer_id}
        return self._post('assets_swap_cancel', params)

    def ipfs_add(self, data):
        """
        Add data to IPFS.
        """
        params = {'data': data}
        return self._post('ipfs_add', params)

    def ipfs_hash(self, hash_):
        """
        Retrieve data from IPFS by hash.
        """
        params = {'hash': hash_}
        return self._post('ipfs_hash', params)

    def ipfs_get(self, hash_):
        """
        Get the content of a hash from IPFS.
        """
        params = {'hash': hash_}
        return self._post('ipfs_get', params)

    def invoke_contract(self, contract=None, contract_file=None, args=None, create_tx=True, priority=0, unique=0):
        """
        Invoke a Beam smart contract.

        :param contract: Raw contract code as a bytes array (optional).
        :param contract_file: Contract file path (optional, ignored if `contract` is provided).
        :param args: Arguments for the contract in string format (e.g., "role=manager,action=view").
        :param create_tx: If True, automatically creates a transaction if the contract requires it.
        :param priority: Priority level for contract execution queue (default is 0).
        :param unique: Ensures a unique contract execution to prevent redundant processing.
        :return: Contract output, transaction ID (if applicable), or raw contract execution data.
        """
        params = {}
        if contract:
            params["contract"] = contract
        elif contract_file:
            params["contract_file"] = contract_file
        if args:
            params["args"] = args
        if create_tx is not None:
            params["create_tx"] = create_tx
        if priority:
            params["priority"] = priority
        if unique:
            params["unique"] = unique

        return self._post('invoke_contract', params)


    def process_invoke_data(self, data):
        """
        Process transaction data returned from a smart contract invocation.

        :param data: Raw invoke data as a bytes array (returned from `invoke_contract`).
        :return: Transaction ID of the executed contract.
        """
        if not data:
            raise ValueError("Data parameter is required for processing contract invocation.")
        
        params = {"data": data}
        return self._post('process_invoke_data', params)


