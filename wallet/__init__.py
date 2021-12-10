import os
from chia.util.agg_sig_me_additional_data import get_agg_sig_me_additional_data

rpc_host = os.environ['CHIA_RPC_HOST'] if 'CHIA_RPC_HOST' in os.environ \
    else 'localhost'
full_node_rpc_port = os.environ['CHIA_RPC_PORT'] if 'CHIA_RPC_PORT' in os.environ \
    else '8555'
wallet_rpc_port = os.environ['CHIA_WALLET_PORT'] if 'CHIA_WALLET_PORT' in os.environ \
    else '9256'

AGG_SIG_ME_ADDITIONAL_DATA = get_agg_sig_me_additional_data()
