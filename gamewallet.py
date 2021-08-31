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

from cdv.util.load_clvm import load_clvm

from chia.wallet.derive_keys import master_sk_to_wallet_sk

from cdv.test import SmartCoinWrapper, CoinPairSearch, CoinWrapper

from checkers.driver import CheckersMover, showBoardFromDict
from support import SpendResult, FakeCoin, GAME_MOJO, LARGE_NUMBER_OF_BLOCKS

NETNAME = 'testnet7'

rpc_host = os.environ['CHIA_RPC_HOST'] if 'CHIA_RPC_HOST' in os.environ \
    else 'localhost'
full_node_rpc_port = os.environ['CHIA_RPC_PORT'] if 'CHIA_RPC_PORT' in os.environ \
    else '8555'
wallet_rpc_port = os.environ['CHIA_WALLET_PORT'] if 'CHIA_WALLET_PORT' in os.environ \
    else '9256'

class GameRecords:
    def run_db(self,stmt,*params):
        cursor = self.db.cursor()
        cursor.execute(stmt, *params)
        cursor.close()
        self.db.commit()

    def __init__(self,cblock,netname,mover,client):
        self.blocks_ago = cblock
        self.netname = netname
        self.client = client
        self.mover = mover

        self.db = sqlite3.connect('checkers.db')
        self.run_db("create table if not exists height (net text primary key, block integer)")
        self.run_db("create table if not exists checkers (launcher text, board text, coin text)")
        self.run_db("create table if not exists self (puzzle_hash)")
        self.db.commit()

    def close(self):
        self.db.close()

    def get_coin_for_launcher(self,launcher):
        result = None
        cursor = self.db.cursor()
        print(f'find launcher {launcher}')
        rows = cursor.execute('select coin, board from checkers where launcher = ? limit 1', (launcher,))
        for r in rows:
            print(f'found {r}')
            result = binascii.unhexlify(r[0]), json.loads(r[1])
        cursor.close()

        return result

    def remember_coin(self,launcher,coin,board):
        self.run_db('delete from checkers where launcher = ?', (launcher,))
        self.run_db('insert into checkers (launcher, coin, board) values (?,?,?)', (launcher, binascii.hexlify(coin), json.dumps(board)))

    async def get_current_height_from_node(self):
        blockchain_state = await self.client.get_blockchain_state()
        new_height = blockchain_state['peak'].height
        return new_height

    async def retrieve_current_block(self):
        cursor = self.db.cursor()
        current_block = None

        for row in cursor.execute("select block from height where net = ? order by block desc limit 1", (self.netname,)):
            current_block = row[0]

        cursor.close()

        if current_block is None:
            current_block = await self.get_current_height_from_node()
            current_block -= self.blocks_ago

        return current_block

    def set_current_block(self,new_height):
        cursor = self.db.cursor()
        cursor.execute("insert or replace into height (net, block) values (?,?)", (self.netname, new_height))
        cursor.close()
        self.db.commit()

    def set_self_hash(self,puzzle_hash):
        cursor = self.db.cursor()
        cursor.execute("delete from self")
        cursor.close()
        self.db.commit()

        cursor = self.db.cursor()
        cursor.execute("insert or replace into self (puzzle_hash) values (?)", (puzzle_hash,))
        cursor.close()
        self.db.commit()

    def get_self_hash(self):
        result = None

        cursor = self.db.cursor()
        rows = cursor.execute("select puzzle_hash from self limit 1")
        for r in rows:
            result = r[0]

        cursor.close()

        return result

    async def update_to_current_block(self, blocks_ago):
        current_block = await self.retrieve_current_block()
        new_height = await self.get_current_height_from_node()
        if new_height - blocks_ago < current_block:
            current_block = max(new_height - blocks_ago, 1)

        if current_block is None:
            current_block = await self.get_current_height_from_node()
            current_block -= self.blocks_ago

        while new_height > current_block:
            if new_height - current_block > 1:
                new_height = current_block + 1

            print(f'absorb state until block {new_height}')
            await self.mover.absorb_state(new_height, self.client)
            self.set_current_block(new_height)
            current_block = new_height
            blockchain_state = await self.client.get_blockchain_state()
            new_height = blockchain_state['peak'].height

class NotMeWallet:
    def __init__(self,public_key):
        self.pk_ = public_key
        self.puzzle = puzzle_for_pk(self.pk_)
        self.puzzle_hash = self.puzzle.get_tree_hash()

    def pk(self):
        return self.pk_

    def close(self):
        pass

    def not_our_turn(self):
        raise Exception("Tried to take an action but it's not our turn")

    async def launch_smart_coin(self, program, amt=None, launcher=None):
        self.not_our_turn()

    async def spend_coin(self, coin, push_tx=True, amt=None, args=None):
        self.not_our_turn()

class CheckersRunnerWallet:
    def __init__(self,netname,blocks_ago):
        self.parent = None
        self.blocks_ago = blocks_ago
        self.wallet_rpc_client = None
        self.netname = netname
        self.mover = None
        self.public_key_fingerprints = []
        self.pk_ = None
        self.sk_ = None
        self.puzzle = None
        self.puzzle_hash = None
        self.wallet = None
        self.usable_coins = {}
        self.game_records = None

    def pk_to_sk(self,pk):
        if pk == self.pk_:
            return self.sk_

    def balance(self):
        return 0

    def close(self):
        if self.parent:
            self.parent.close()
        if self.wallet_rpc_client:
            self.wallet_rpc_client.close()

    def pk(self):
        return self.pk_

    async def public_key_matches(self,pk):
        for pkdata in self.public_key_fingerprints:
            private_key = await self.wallet_rpc_client.get_private_key(pkdata)
            sk_data = binascii.unhexlify(private_key['sk'])
            for i in range(100):
                sk_ = master_sk_to_wallet_sk(PrivateKey.from_bytes(sk_data), i)
                pk_ = sk_.get_g1()
                if pk_ == pk:
                    puzzle = puzzle_for_pk(pk_)
                    puzzle_hash = puzzle.get_tree_hash()

                    self.sk_ = sk_
                    self.pk_ = pk_
                    self.puzzle = puzzle
                    self.puzzle_hash = puzzle_hash
                    return True

        return False

    async def create_rpc_connections(self):
        root_dir = os.environ['CHIA_ROOT'] if 'CHIA_ROOT' in os.environ \
            else os.path.join(
                    os.environ['HOME'], '.chia/mainnet'
            )

        config = load_config(Path(root_dir), 'config.yaml')

        self.parent = await FullNodeRpcClient.create(
            rpc_host, uint16(full_node_rpc_port), Path(root_dir), config
        )
        self.wallet_rpc_client = await WalletRpcClient.create(
            rpc_host, uint16(wallet_rpc_port), Path(root_dir), config
        )

    async def wallet_get_pk(self,pkf_optional: Optional['Number']):
        await self.create_rpc_connections()
        self.public_key_fingerprints = await self.wallet_rpc_client.get_public_keys()

        if len(self.public_key_fingerprints) == 0:
            raise Exception('No key fingerprints available')

        if pkf_optional is not None:
            pkdata = pkf_optional
        else:
            pkdata = self.public_key_fingerprints[0]

        private_key = await self.wallet_rpc_client.get_private_key(pkdata)
        sk_data = binascii.unhexlify(private_key['sk'])
        sk_ = master_sk_to_wallet_sk(PrivateKey.from_bytes(sk_data), 0)
        pk_ = sk_.get_g1()
        puzzle = puzzle_for_pk(pk_)
        puzzle_hash = puzzle.get_tree_hash()

        self.sk_ = sk_
        self.pk_ = pk_
        self.puzzle = puzzle
        self.puzzle_hash = puzzle_hash

        return self.pk_

    async def start(self,mover):
        self.mover = mover

        await self.create_rpc_connections()

        self.game_records = GameRecords(
            self.blocks_ago, self.netname, self.mover, self.parent
        )

        self.game_records.set_self_hash(self.puzzle_hash)

        self.public_key_fingerprints = await self.wallet_rpc_client.get_public_keys()
        print(self.public_key_fingerprints)

        # Get usable coins
        wallets = await self.wallet_rpc_client.get_wallets()
        self.wallet = wallets[0]
        transactions = await self.wallet_rpc_client.get_transactions(self.wallet['id'])
        print(transactions)
        for t in transactions:
            for a in t.additions:
                if a.parent_coin_info in self.usable_coins:
                    del self.usable_coins[a.parent_coin_info]

                self.usable_coins[a.name()] = a

            for r in t.removals:
                if r.name() in self.usable_coins:
                    del self.usable_coins[r.name()]

        print(f'usable_coins {self.usable_coins}')

        await self.game_records.update_to_current_block(self.blocks_ago)

    async def find_coin_by_name(self,name):
        coin_record = await self.parent.get_coin_record_by_name(name)
        return coin_record

    async def select_identity_for_coin(self,coin):
        for pkdata in self.public_key_fingerprints:
            private_key = await self.wallet_rpc_client.get_private_key(pkdata)
            sk_data = binascii.unhexlify(private_key['sk'])
            for i in range(100):
                sk_ = master_sk_to_wallet_sk(PrivateKey.from_bytes(sk_data), i)
                pk_ = sk_.get_g1()
                puzzle = puzzle_for_pk(pk_)
                puzzle_hash = puzzle.get_tree_hash()

                if puzzle_hash == coin.puzzle_hash:
                    self.sk_ = sk_
                    self.pk_ = pk_
                    self.puzzle = puzzle
                    self.puzzle_hash = puzzle_hash

                    self.game_records.set_self_hash(self.puzzle_hash)

                    return

        raise Exception('Could not find a wallet identity that matches the coin')

    def compute_combine_action(
        self, amt: uint64, actions: List, usable_coins: Dict[bytes32, Coin]
    ) -> Optional[List[Coin]]:
        # No one coin is enough, try to find a best fit pair, otherwise combine the two
        # maximum coins.
        searcher = CoinPairSearch(amt)

        # Process coins for this round.
        for k, c in usable_coins.items():
            searcher.process_coin_for_combine_search(c)

        max_coins, total = searcher.get_result()

        if total >= amt:
            return max_coins
        else:
            return None

    async def choose_coin(self, amt):
        """Given an amount requirement, find a coin that contains at least that much chia"""
        start_balance: uint64 = self.balance()
        coins_to_spend: Optional[List[Coin]] = self.compute_combine_action(amt, [], dict(self.usable_coins))

        # Couldn't find a working combination.
        if coins_to_spend is None:
            return None

        if len(coins_to_spend) == 1:
            only_coin: Coin = coins_to_spend[0]
            return CoinWrapper(
                only_coin.parent_coin_info,
                only_coin.puzzle_hash,
                only_coin.amount,
                self.puzzle,
            )

        # We receive a timeline of actions to take (indicating that we have a plan)
        # Do the first action and start over.
        result: Optional[SpendResult] = await self.combine_coins(
            list(
                map(
                    lambda x: CoinWrapper(x.parent_coin_info, x.puzzle_hash, x.amount, self.puzzle),
                    coins_to_spend,
                )
            )
        )

        if result is None:
            return None

        assert self.balance() == start_balance
        return await self.choose_coin(amt)

    async def launch_smart_coin(self, source, **kwargs):
        """Create a new smart coin based on a parent coin and return the smart coin's living
        coin to the user or None if the spend failed."""
        amt = uint64(1)
        found_coin: Optional[CoinWrapper] = None

        if "amt" in kwargs:
            amt = kwargs["amt"]

        if "launcher" in kwargs:
            found_coin = kwargs["launcher"]
        else:
            found_coin = await self.choose_coin(amt)

        # Create a puzzle based on the incoming smart coin
        cw = SmartCoinWrapper(DEFAULT_CONSTANTS.GENESIS_CHALLENGE, source)
        condition_args: List[List] = [
            [ConditionOpcode.CREATE_COIN, cw.puzzle_hash(), amt],
        ]
        if amt < found_coin.amount:
            print(f'spending remaining {amt} to {self.puzzle_hash}')
            condition_args.append([ConditionOpcode.CREATE_COIN, self.puzzle_hash, found_coin.amount - amt])

        delegated_puzzle_solution = Program.to((1, condition_args))
        solution = Program.to([[], delegated_puzzle_solution, []])

        # Sign the (delegated_puzzle_hash + coin_name) with synthetic secret key
        signature: G2Element = AugSchemeMPL.sign(
            calculate_synthetic_secret_key(self.sk_, DEFAULT_HIDDEN_PUZZLE_HASH),
            (
                delegated_puzzle_solution.get_tree_hash()
                + found_coin.name()
                + DEFAULT_CONSTANTS.AGG_SIG_ME_ADDITIONAL_DATA
            ),
        )

        spend_bundle = SpendBundle(
            [
                CoinSpend(
                    found_coin.as_coin(),  # Coin to spend
                    self.puzzle,  # Puzzle used for found_coin
                    solution,  # The solution to the puzzle locking found_coin
                )
            ],
            signature,
        )

        print('debug spend bundle?')
        print(spend_bundle.to_json_dict())
        print(binascii.hexlify(bytes(spend_bundle)))
        spend_bundle.debug()
        print('^--- spend_bundle.debug()')

        pushed: Dict[str, Union[str, List[Coin]]] = await self.parent.push_tx(spend_bundle)
        if "error" not in pushed:
            return cw.custom_coin(found_coin, amt)
        else:
            return None

    async def spend_coin(self, coin, pushtx: bool = True, **kwargs):
        """Given a coin object, invoke it on the blockchain, either as a standard
        coin if no arguments are given or with custom arguments in args="""

        print(f'spend coin {coin}')

        amt = uint64(1)
        if "amt" in kwargs:
            amt = kwargs["amt"]

        delegated_puzzle_solution: Optional[Program] = None
        if "args" not in kwargs:
            target_puzzle_hash: bytes32 = self.puzzle_hash
            # Allow the user to 'give this much chia' to another user.
            if "to" in kwargs:
                target_puzzle_hash = kwargs["to"].puzzle_hash

            # Automatic arguments from the user's intention.
            if "custom_conditions" not in kwargs:
                solution_list: List[List] = [[ConditionOpcode.CREATE_COIN, target_puzzle_hash, amt]]
            else:
                solution_list = kwargs["custom_conditions"]
            if "remain" in kwargs:
                remainer: Union[SmartCoinWrapper, Wallet] = kwargs["remain"]
                remain_amt = uint64(coin.amount - amt)
                if isinstance(remainer, SmartCoinWrapper):
                    solution_list.append(
                        [
                            ConditionOpcode.CREATE_COIN,
                            remainer.puzzle_hash(),
                            remain_amt,
                        ]
                    )
                elif isinstance(remainer, Wallet):
                    solution_list.append([ConditionOpcode.CREATE_COIN, remainer.puzzle_hash, remain_amt])
                else:
                    raise ValueError("remainer is not a wallet or a smart coin")

            delegated_puzzle_solution = Program.to((1, solution_list))
            # Solution is the solution for the old coin.
            solution = Program.to([[], delegated_puzzle_solution, []])
        else:
            delegated_puzzle_solution = Program.to(kwargs["args"])
            solution = delegated_puzzle_solution

        solution_for_coin = CoinSpend(
            coin.as_coin(),
            coin.puzzle(),
            solution,
        )

        # The reason this use of sign_coin_spends exists is that it correctly handles
        # the signing for non-standard coins.  I don't fully understand the difference but
        # this definitely does the right thing.
        try:
            spend_bundle: SpendBundle = await sign_coin_spends(
                [solution_for_coin],
                self.pk_to_sk,
                DEFAULT_CONSTANTS.AGG_SIG_ME_ADDITIONAL_DATA,
                DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM,
            )
        except ValueError:
            spend_bundle = SpendBundle(
                [solution_for_coin],
                G2Element(),
            )

        if pushtx:
            pushed: Dict[str, Union[str, List[Coin]]] = await self.parent.push_tx(spend_bundle)
            return SpendResult(pushed)
        else:
            return spend_bundle

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
                raise ValueError(f"could not find available coin containing {amt} mojo")
            await mywallet.select_identity_for_coin(found_coin)

            launcher_coin, run_coin = await mover.launch_game(found_coin)

            print(f'you are playing black, identifier: {launcher_coin.name()}-{binascii.hexlify(bytes(mywallet.pk())).decode("utf-8")}-{binascii.hexlify(bytes(notmywallet.pk())).decode("utf-8")}')

            mywallet.game_records.remember_coin(launcher_coin.name(), run_coin.name(), mover.get_board())
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
                # We're playing red so reconfigure.
                mywallet.close()

                mywallet = CheckersRunnerWallet(NETNAME, LARGE_NUMBER_OF_BLOCKS)
                red_wallet = mywallet
                black_wallet = NotMeWallet(black_public_key)
                mover = CheckersMover(inner_puzzle_code, black_wallet, red_wallet)
                mover.set_launch_coin(FakeCoin(bytes32(binascii.unhexlify(launcher_coin_name))))
                await mywallet.start(mover)

                # Select identity based on key embedded in game id
                await mywallet.public_key_matches(red_public_key)

            if mover.current_coin is None:
                print(f'launcher_coin_name {launcher_coin_name}')
                current_coin_name_and_board = mywallet.game_records.get_coin_for_launcher(binascii.unhexlify(launcher_coin_name))
                print(f'found current game coin: {current_coin_name_and_board}')
                if current_coin_name_and_board:
                    current_coin_name, current_board = current_coin_name_and_board
                    current_coin = await mywallet.find_coin_by_name(current_coin_name)
                    if not current_coin:
                        print(f"Couldn't yet find the most recent coin for the game.  Try again in a moment.")
                        return

                    print(f'set_current_coin {current_coin}')
                    mover.set_current_coin(current_coin)
                    mover.set_board(current_board)

            else:
                mywallet.game_records.remember_coin(binascii.unhexlify(launcher_coin_name), mover.current_coin.name(), mover.get_board())

            print(f'current coin for game {mover.current_coin}')

            if fromX is not None:
                launch_coin = await mywallet.find_coin_by_name(
                    binascii.unhexlify(launcher_coin_name)
                )

                if not matches_red:
                    await mywallet.select_identity_for_coin(launch_coin.coin)

                    mover.set_launch_coin(
                        CoinWrapper.from_coin(launch_coin.coin, None)
                    )

                coin_puzzle = mover.get_coin_puzzle()
                target_coin = None

                if hasattr(mover.current_coin,'coin'):
                    target_coin = \
                        CoinWrapper.from_coin(
                            mover.current_coin.coin,
                            coin_puzzle
                        )
                else:
                    target_coin = \
                        CoinWrapper.from_coin(
                            mover.current_coin,
                            coin_puzzle
                        )

                mover.set_current_coin(target_coin)

                await mover.make_move(fromX, fromY, toX, toY)
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
