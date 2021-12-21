import os
import os.path
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

from cdv.util.load_clvm import load_clvm

from chia.wallet.derive_keys import master_sk_to_wallet_sk

from cdv.test import SmartCoinWrapper, CoinPairSearch, CoinWrapper, Wallet

from checkers.gamerecords import GameRecords
from checkers.driver import CheckersMover, showBoardFromDict, GAME_MOJO

from wallet.notme import NotMeWallet
from wallet.live import CheckersRunnerWallet

from support import SpendResult, FakeCoin, GAME_MOJO, LARGE_NUMBER_OF_BLOCKS

## HTTP LOGGING
import logging

NETNAME = 'testnet10'

async def main():
    black_wallet = None
    red_wallet = None
    mover = None

    try:
        inner_puzzle_code = load_clvm(
            "checkers.cl", "checkers.code", search_paths=["checkers/code"]
        )

        do_launch = None
        do_init_height = 1

        launcher = None
        color = None

        if '--launch' in sys.argv[1:] and len(sys.argv) > 2:
            do_launch = sys.argv[2]
        elif '--my-pk' in sys.argv[1:]:
            pk_fingerprint = None

            if len(sys.argv) > 2:
                pk_fingerprint = int(sys.argv[1])

            mywallet = CheckersRunnerWallet(NETNAME, do_init_height)
            black_wallet = mywallet

            await mywallet.wallet_get_pk(pk_fingerprint)
            print(mywallet.pk())

            return

        elif len(sys.argv) < 2:
            print('usage:')
            print('gamewallet.py --launch <red-player-pk> # Launch a game, returning its identifier')
            print(' -- returns identifier')
            print('gamewallet.py --my-pk # Give my pk for the game to start')
            print(' -- returns public key')
            print('gamewallet.py [identifier] # Show the game board')
            print('gamewallet.py [identifier] [move] # Make a move in the game')
            sys.exit(1)

        if do_launch:
            # Init wallet configuration for launching
            mywallet = CheckersRunnerWallet(NETNAME, do_init_height)
            notmywallet = NotMeWallet(binascii.unhexlify(do_launch))
            black_wallet = mywallet
            red_wallet = notmywallet

            mover = CheckersMover(inner_puzzle_code, black_wallet, red_wallet)
            await mywallet.start(mover)

            found_coin = await mywallet.choose_coin(GAME_MOJO)
            if found_coin is None:
                raise ValueError(f"could not find available coin containing {GAME_MOJO} mojo")
            print(f'select id for coin {found_coin.name()}')
            await mywallet.select_identity_for_coin(found_coin)

            launcher_coin, run_coin = await mover.launch_game(found_coin)
            print(f'launcher_coin {launcher_coin}, run_coin {run_coin}')

            print(f'you are playing black, identifier: {binascii.hexlify(launcher_coin).decode("utf8")}-{binascii.hexlify(bytes(mywallet.pk())).decode("utf-8")}-{binascii.hexlify(bytes(notmywallet.pk())).decode("utf-8")}')

            mywallet.game_records.remember_coin(
                launcher_coin,
                run_coin.name(),
                mover.get_board()
            )
        else:
            launcher_coin_name, black_public_key_str, red_public_key_str = \
                sys.argv[1].split('-')

            if len(sys.argv) > 2:
                moveFrom, moveTo = sys.argv[2].split(':')
                fromX, fromY = [int(x) for x in moveFrom.split(',')]
                toX, toY = [int(x) for x in moveTo.split(',')]
            else:
                fromX, fromY, toX, toY = None, None, None, None

            black_public_key = G1Element.from_bytes(
                binascii.unhexlify(black_public_key_str)
            )
            red_public_key = G1Element.from_bytes(
                binascii.unhexlify(red_public_key_str)
            )

            # Determine who we are
            mywallet = CheckersRunnerWallet(NETNAME, do_init_height)

            black_wallet = mywallet
            red_wallet = NotMeWallet(red_public_key)

            mover = CheckersMover(inner_puzzle_code, black_wallet, red_wallet, launcher_name = binascii.unhexlify(launcher_coin_name))
            await mywallet.start(mover)

            self_puzzle_hash = mywallet.game_records.get_self_hash()
            matches_red = \
                await mywallet.public_key_matches(red_public_key)

            if matches_red:
                print(f'MATCHED RED {red_public_key}')
                # We're playing red so reconfigure.
                mywallet.close()

                mywallet = CheckersRunnerWallet(NETNAME, LARGE_NUMBER_OF_BLOCKS)
                red_wallet = mywallet
                black_wallet = NotMeWallet(black_public_key)
                mover = CheckersMover(inner_puzzle_code, black_wallet, red_wallet)
                mover.set_launch_coin_name(launcher_coin_name)
                await mywallet.start(mover)

                # Select identity based on key embedded in game id
                await mywallet.public_key_matches(red_public_key)

            print(f'launcher_coin_name {launcher_coin_name}')
            current_coin_name_and_board = mywallet.game_records.get_coin_for_launcher(binascii.unhexlify(launcher_coin_name))
            print(f'found current game coin: {current_coin_name_and_board}')

            if current_coin_name_and_board:
                current_coin_name, current_board = current_coin_name_and_board
                parent_coins = await mywallet.get_parent_coins(binascii.unhexlify(launcher_coin_name))
                print(f'coins {parent_coins}')
                if len(parent_coins) < 1:
                    print(f"Couldn't yet find the most recent coin for the game.  Try again in a moment.")
                    return

                mover.set_current_coin_name(current_coin_name)
                mover.set_board(current_board)
                mywallet.game_records.remember_coin(
                    binascii.unhexlify(launcher_coin_name),
                    mover.current_coin_name,
                    mover.get_board()
                )
            else:
                print(f'no coin for game')
                return

            print(f'current coin for game {mover.current_coin_name}')

            if fromX is not None:
                launch_coin = await mywallet.find_coin_by_name(
                    binascii.unhexlify(launcher_coin_name)
                )

                if not matches_red:
                    await mywallet.public_key_matches(black_public_key)
                    mover.set_launch_coin_name(launch_coin.name)

                await mover.make_move(parent_coins, fromX, fromY, toX, toY)
            else:
                board = mover.get_board()
                print(showBoardFromDict(board))

    finally:
        if black_wallet:
            black_wallet.close()
        if red_wallet:
            red_wallet.close()

if __name__ == '__main__':
    asyncio.run(main())
