import os
import os.path
import sqlite3
import yaml
import json
import sys
import asyncio
from pathlib import Path
import binascii

from typing import Dict, List, Tuple, Optional, Union
from blspy import AugSchemeMPL, G1Element, G2Element, PrivateKey

from clvm import SExp, to_sexp_f

from chia.types.blockchain_format.coin import Coin
from chia.types.blockchain_format.program import Program
from chia.types.blockchain_format.sized_bytes import bytes32
from chia.types.spend_bundle import SpendBundle
from chia.types.coin_spend import CoinSpend
from chia.types.coin_record import CoinRecord

from chia.consensus.default_constants import DEFAULT_CONSTANTS
from chia.wallet.sign_coin_spends import sign_coin_spends
from chia.wallet.puzzles.p2_delegated_puzzle_or_hidden_puzzle import puzzle_for_pk
from chia.wallet.puzzles.p2_delegated_puzzle_or_hidden_puzzle import (  # standard_transaction
    puzzle_for_pk,
    calculate_synthetic_secret_key,
    DEFAULT_HIDDEN_PUZZLE_HASH,
)

from chia.rpc.rpc_client import RpcClient
from chia.rpc.full_node_rpc_client import FullNodeRpcClient
from chia.rpc.wallet_rpc_client import WalletRpcClient

from chia.util.condition_tools import ConditionOpcode
from chia.util.config import load_config, save_config
from chia.util.hash import std_hash
from chia.util.ints import uint16, uint64

from cdv.test import SmartCoinWrapper, CoinPairSearch, CoinWrapper

GAME_MOJO = 1
LARGE_NUMBER_OF_BLOCKS = 3000

class SpendResult:
    def __init__(self, result: Dict):
        """Constructor for internal use.

        error - a string describing the error or None
        result - the raw result from Network::push_tx
        outputs - a list of new Coin objects surviving the transaction
        """
        self.result = result
        if "error" in result:
            self.error: Optional[str] = result["error"]
            self.outputs: List[Coin] = []
        elif "additions" in result:
            self.error = None
            self.outputs = result["additions"]
        else:
            self.outputs = []

    def find_standard_coins(self, puzzle_hash: bytes32) -> List[Coin]:
        """Given a Wallet's puzzle_hash, find standard coins usable by it.

        These coins are recognized as changing the Wallet's chia balance and are
        usable for any purpose."""
        return list(filter(lambda x: x.puzzle_hash == puzzle_hash, self.outputs))

class FakeCoin:
    def __init__(self,name : bytes32):
        self.name_ = name
        self.coin = self
        self.amount = GAME_MOJO

    def as_coin(self):
        return self

    def name(self):
        return self.name_
