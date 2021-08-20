import os
import os.path
import sqlite3
import yaml
import sys
import asyncio
from pathlib import Path
import binascii

from clvm import SExp, to_sexp_f

from chia.util.ints import uint16
from chia.wallet.puzzles.p2_delegated_puzzle_or_hidden_puzzle import puzzle_for_pk

from chia.rpc.rpc_client import RpcClient
from chia.rpc.full_node_rpc_client import FullNodeRpcClient
from chia.rpc.wallet_rpc_client import WalletRpcClient

from chia.util.config import load_config, save_config

from cdv.util.load_clvm import load_clvm
from cdv.test import SmartCoinWrapper

from checkers.driver import CheckersMover

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

    def __init__(self,netname,mover,client):
        self.netname = netname
        self.client = client
        self.mover = mover

        self.db = sqlite3.connect('checkers.db')
        self.run_db("create table if not exists height (net text, block integer)")
        self.run_db("create table if not exists checkers (block integer, launcher text, board text)")
        self.db.commit()

    def close(self):
        self.db.close()

    def retrieve_current_block(self):
        cursor = self.db.cursor()
        current_block = 1

        for row in cursor.execute("select block from height where net = ? limit 1", (self.netname,)):
            current_block = row[0]

        cursor.close()
        return current_block

    def set_current_block(self,new_height):
        cursor = self.db.cursor()
        cursor.execute("insert or replace into height (net, block) values (?,?)", (self.netname, new_height))
        cursor.close()
        self.db.commit()

    async def update_to_current_block(self):
        current_block = self.retrieve_current_block()

        blockchain_state = await self.client.get_blockchain_state()
        new_height = blockchain_state['peak'].height

        while new_height > current_block:
            if new_height - current_block > 50:
                new_height = current_block + 50

            await self.mover.absorb_state(new_height, self.client)
            self.set_current_block(new_height)
            current_block = new_height
            blockchain_state = await self.client.get_blockchain_state()
            new_height = blockchain_state['peak'].height

class NotMeWallet:
    def __init__(self):
        pass

    def close(self):
        pass

    def not_our_turn(self):
        raise Exception("Tried to take an action but it's not our turn")

    async def launch_smart_coin(self, program, amt=None, launcher=None):
        self.not_our_turn()

    async def spend_coin(self, coin, push_tx=True, amt=None, args=None):
        self.not_our_turn()

class CheckersRunnerWallet:
    def __init__(self,netname):
        self.parent = None
        self.wallet_rpc_client = None
        self.game_records = None
        self.netname = netname
        self.mover = None
        self._pk = None
        self.puzzle_hash = None

    def close(self):
        if self.parent:
            self.parent.close()
        if self.wallet_rpc_client:
            self.wallet_rpc_client.close()

    async def pk(self):
        return self._pk

    async def start(self,mover):
        self.mover = mover

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

        self.game_records = GameRecords(
            self.netname, self.mover, self.parent
        )

        public_key_fingerprints = await self.wallet_rpc_client.get_public_keys()
        last_private_key = await self.wallet_rpc_client.get_private_key(
            public_key_fingerprints[-1]
        )
        self._pk = binascii.unhexlify(last_private_key['pk'])
        self.puzzle_hash = puzzle_for_pk(self._pk)

        await self.game_records.update_to_current_block()

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

    async def launch_smart_coin(self, program, **kwargs):
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

        if found_coin is None:
            raise ValueError(f"could not find available coin containing {amt} mojo")

        # Create a puzzle based on the incoming smart coin
        cw = SmartCoinWrapper(DEFAULT_CONSTANTS.GENESIS_CHALLENGE, source)
        condition_args: List[List] = [
            [ConditionOpcode.CREATE_COIN, cw.puzzle_hash(), amt],
        ]
        if amt < found_coin.amount:
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
        pushed: Dict[str, Union[str, List[Coin]]] = await self.parent.push_tx(spend_bundle)
        if "error" not in pushed:
            return cw.custom_coin(found_coin, amt)
        else:
            return None

    async def spend_coin(self, coin, push_tx=True, amt=None, args=None):
        pass

async def main():
    black_wallet = None
    red_wallet = None
    mover = None

    try:
        inner_puzzle_code = load_clvm(
            "checkers.cl", "checkers.code", search_paths=["checkers/code"]
        )

        do_launch = False
        launcher = None
        color = None

        if '--launch' in sys.argv[1:]:
            do_launch = True
        elif len(sys.argv) < 3:
            print('usage:')
            print('gamewallet.py --launch # Launch a game, returning its identifier')
            print('gamewallet.py [identifier] # Show a game by identifier')
            print('gamewallet.py [identifier] [move] # Make a move in the game')
            sys.exit(1)
        else:
            launcher = sys.argv[1]
            color = sys.argv[2]

        mywallet = CheckersRunnerWallet('testnet7')

        black_wallet = mywallet if color == 'black' else NotMeWallet()
        red_wallet = mywallet if black_wallet is not mywallet else NotMeWallet()

        mover = CheckersMover(inner_puzzle_code, black_wallet, red_wallet)
        await mywallet.start(mover)

        if do_launch:
            launch_tx = await r.launch_smart_coin(inner_puzzle_code)
            if 'error' in launch_tx.result:
                print(f'error launching coin: {launch_tx}')
            else:
                launcher_coin = launch_tx.result['additions'][0]
                print(f'you are playing black, launcher: {r.puzzle_hash}-{launcher_coin.name()}')
    finally:
        if black_wallet:
            black_wallet.close()
        if red_wallet:
            red_wallet.close()

if __name__ == '__main__':
    asyncio.run(main())
