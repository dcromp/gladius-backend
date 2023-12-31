import time 
import json
from web3 import Web3
from decimal import Decimal, getcontext

import firebase_admin
from firebase_admin import credentials, auth, firestore
from google.cloud import storage

from flask import request, jsonify

import tempfile # transform Firebase SDK key file
import functions_framework #  Enable CORS

# Read the ABI data from GLCToken.json
with open('GLCToken.json', 'r') as file:
    contract_data = json.load(file)

# Set the precision for decimal calculations (adjust this based on your requirements)
getcontext().prec = 28


# Set up the Polygon (Mumbai) network
polygon_rpc_url = "https://rpc-mumbai.maticvigil.com"  # Replace with the appropriate RPC endpoint
w3 = Web3(Web3.HTTPProvider(polygon_rpc_url))

# Replace with test data is needed
# In Production verison this data is queried from Firebase via id_token
private_key = ""
wallet_address = ""

# Define a function to wait for transaction confirmation
def wait_for_confirmation(tx_hash):
    max_retries = 60  # Maximum number of retries (adjust as needed)
    sleep_time = 1  # Sleep for 1 seconds between retries (adjust as needed)
    retry_count = 0

    while retry_count < max_retries:
        try:
            # Check the transaction receipt
            receipt = w3.eth.getTransactionReceipt(tx_hash)
            if receipt is not None:
                # Transaction confirmed
                return receipt
        except Exception as e:
            # Handle exceptions, e.g., if the transaction hasn't been mined yet
            print(f"Waiting for transaction receipt: {e}")

        # Sleep for a while and increment the retry count
        time.sleep(sleep_time)
        retry_count += 1

    # Transaction not confirmed within the maximum retries
    return None

@functions_framework.http
def token_transfer(request):

    # Set CORS headers for the preflight request
    if request.method == "OPTIONS":
        # Allows GET requests from any origin with the Content-Type
        # header and caches preflight response for an 3600s
        headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST",  # Allow both GET and POST methods
            "Access-Control-Allow-Headers": "Content-Type, Authorization",  # Added 'Authorization'
            "Access-Control-Max-Age": "3600",
        }


        return ("", 204, headers)

    # Set CORS headers for the main request
    headers = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "Content-Type, Authorization"}

    # Get the Firebase ID token from the Authorization header
    bearer_token = request.headers.get("Authorization")
    if not bearer_token:
        return jsonify({"error": "Authorization token not found"}), 401

    id_token = bearer_token.split(" ")[1]       
    # Call this function to initialize the Firebase Admin SDK in your Cloud Function
    try:
        # Replace with your Cloud Storage bucket and the path to the service account credentials JSON file
        bucket_name = "gladius-backend"
        credentials_file_path = "firebase-admi-sdk-d55dc53a0acc.json"

        # Download the service account credentials JSON file from Cloud Storage
        storage_client = storage.Client()
        bucket = storage_client.get_bucket(bucket_name)
        blob = bucket.blob(credentials_file_path)
        credentials_file_content = blob.download_as_text()
        print(credentials_file_path + ' was downloaded from ' + bucket_name)

        # Initialize the Firebase Admin SDK with the downloaded service account credentials
               # Create a temporary file to write the credentials content
        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
            temp_file.write(credentials_file_content.encode())
            temp_file_path = temp_file.name 

        # Initialize the Firebase Admin SDK with the temporary file
        if not firebase_admin._apps:
            cred = credentials.Certificate(temp_file_path)
            firebase_admin.initialize_app(cred)
        db = firestore.client()

        #cred = credentials.Certificate(credentials_file_content)
        # firebase_admin.initialize_app(cred)

        print('Firebase connection is up')

    except Exception as e:
        print("Error initializing Firebase Admin SDK:", e)


    try:

        # Verify the Firebase ID token
        decoded_token = auth.verify_id_token(id_token)

        # Access user information from the decoded token
        uid = decoded_token["uid"]
        email = decoded_token["email"]

        print(email + ' is authenticated with uid ' + uid)

        # Retrieve the user's document from the Firestore
        user_doc_ref = db.collection('users').document(uid)
        user_doc = user_doc_ref.get()

        if user_doc.exists:
            user_data = user_doc.to_dict()
            private_key = user_data.get('privateKey')
            wallet_address = user_data.get('address')
            username = user_data.get('name')
            print(username +'`s wallet is ' +wallet_address ) 

            if private_key:
                # Use the private_key
                #print(f"Private Key {private_key} is loaded from Firebase")
                print(f"private_key was loaded from Firebase")
            else:
                print("private_key not found in user document")
        else:
            print("User document not found")

    except auth.ExpiredIdTokenError as e:
        return jsonify({"error": "Token expired"}), 401, headers

    except Exception as e:
        print("Firebase Error:", e)

    try:
        #####################################
        # Cloud Function logic start

        # GLC Token contract address
        token_contract_address = "0x7A57269A63F37244c09742d765B18b1852078072"


        # Load the contract ABI (Replace this with the actual ABI of your token contract)
        token_abi = contract_data['abi']  # Put your token contract ABI here

        # Instantiate the contract
        contract = w3.eth.contract(address=token_contract_address, abi=token_abi)

        
        # request_json = json.loads(request)
        
        request_json = request.get_json()

     
        if 'transactions' in request_json:
            transactions = request_json['transactions']
            
            results = []

            for tx in transactions:
                    
                current_nonce = w3.eth.getTransactionCount(w3.toChecksumAddress(wallet_address), "pending")
                sender_balance = contract.functions.balanceOf(wallet_address).call()
                
                # Calculate the total nonce
                print(f'Current nonce: {current_nonce}')

                to_address = tx['to_address']
                amount = Decimal(tx['amount'])  # Convert the amount to a Decimal
                

                print(f'Sending {amount} to {to_address} from {wallet_address}')
                

                # Replace with the token decimals (usually 18 for most ERC20 tokens)
                token_decimals = 18

                # Convert the amount to the token's base unit
                amount_in_base_unit = int(amount * 10**token_decimals)

                if amount < 0:
                    results.append({'to_address': to_address, 'error': 'Amount should be a positive number'})
                    print(f'to_address: {to_address} error: Amount should be a positive number')

                elif amount == 0:
                    results.append({'to_address': to_address, 'error': 'Not allowed to send 0 coins'})
                    print(f'to_address: {to_address} error: Not allowed to send 0 coins')

                elif wallet_address == to_address:
                    results.append({'to_address': to_address, 'error': 'Not allowed to send coins to yourself'})
                    print(f'to_address: {to_address} error Not allowed to send coins to yourself')

                elif sender_balance < amount_in_base_unit:
                    # return jsonify(f'Error: Transfer amount exceeds balance. Sender balance: {sender_balance/10**token_decimals}, Amount to transfer: {amount_in_base_unit/10**token_decimals}'), 400, headers
                    results.append({'to_address':  to_address, 'error': 'Transfer amount exceeds balance'})
                    print(f'to_address:  {to_address} error: Transfer amount exceeds balance')

                else:
                    # Create the transaction object, but don't send it yet
                    transaction = contract.functions.transfer(to_address, amount_in_base_unit).buildTransaction({
                        'chainId': 80001,  # Replace with the appropriate chain ID (Mumbai network has chain ID 80001)
                        'gas': 200000,
                        'gasPrice': Web3.toWei('10', 'gwei'),  # Use Web3.toWei function directly
                        'nonce': current_nonce  # FOR SINGLE TRANSACTION USE: w3.eth.getTransactionCount(w3.toChecksumAddress(wallet_address))
                        # 'value': 0,  # Set the value to 0 for ERC20 token transfers
                    })

                # Sign the transaction
                    signed_transaction = w3.eth.account.signTransaction(transaction, private_key)

                    # Send the transaction
                    tx_hash = w3.eth.sendRawTransaction(signed_transaction.rawTransaction)
                    # Send the transaction and wait for confirmation
                    # transaction_receipt = wait_for_confirmation(tx_hash)

                    #if transaction_receipt is not None:
                        # Transaction confirmed
                        # return jsonify(results), 200, headers
                        #current_nonce += 1
                        #return jsonify(f'Transaction sent. to_address: {to_address}, amount: {amount}, tx_hash: {tx_hash.hex()}'), 200, headers

                        # Append transactions together
                    results.append({'to_address': to_address, 'tx_hash': tx_hash.hex()})
                    print(f'Transaction {tx_hash.hex()} has been sent')
                        #results.append(f'Transaction has been sent. Receiver: {to_address}, amount: {amount}, tx_hash: {tx_hash.hex()}')
                        #tx_hash_hex = tx_hash.hex()
                        #tx_hash_url = f"https://mumbai.polygonscan.com/tx/{tx_hash_hex}"  # Replace with the appropriate explorer URL
                        #results.append(f'Transaction has been sent. Receiver: {to_address}, amount: {amount}, tracking url: {tx_hash_url}')
                    #else:
                        
                        # Transaction not confirmed within the specified retries
                    #    return jsonify(f'Transaction not confirmed within the specified retries. Transaction hash: {tx_hash.hex()}'), 500, headers
                        #return "Transaction not confirmed within the specified retries", 500, headers

                    # Increment the current nonce for the next transaction
                    #current_nonce += 1
                    current_nonce = w3.eth.getTransactionCount(wallet_address, 'pending')


            # Return all transactions at a time
            return jsonify(results), 200, headers
 
            # Return one transaction result    
            # return jsonify(f'Transaction sent. Transaction hash: {tx_hash.hex()}'), 200, headers

    # Cloud Function logic end
    #####################################

    except json.JSONDecodeError:
        return 'Invalid JSON payload.'

    except KeyError:
        return 'Invalid request. Please provide "to_address" and "amount" in the JSON payload.'
    
    except ValueError as e:
        # Handle insufficient funds error
        return jsonify(f'ValueError: {e}'), 400, headers
    