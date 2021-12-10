import asyncio
import binascii
from blspy import AugSchemeMPL, G1Element, G2Element, PrivateKey
import os
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Union

from clvm import SExp, to_sexp_f

from chia.types.blockchain_format.coin import Coin
from chia.types.blockchain_format.program import Program
from chia.types.blockchain_format.sized_bytes import bytes32
from chia.types.spend_bundle import SpendBundle
from chia.types.coin_spend import CoinSpend
from chia.types.coin_record import CoinRecord

from chia.consensus.default_constants import DEFAULT_CONSTANTS

from chia.rpc.rpc_client import RpcClient
from chia.rpc.full_node_rpc_client import FullNodeRpcClient
from chia.rpc.wallet_rpc_client import WalletRpcClient

from chia.util.config import load_config, save_config
from chia.util.hash import std_hash
from chia.util.ints import uint16, uint64

from chia.wallet.derive_keys import master_sk_to_wallet_sk
from chia.wallet.sign_coin_spends import sign_coin_spends
from chia.wallet.puzzles.p2_delegated_puzzle_or_hidden_puzzle import (  # standard_transaction
    puzzle_for_pk,
    calculate_synthetic_secret_key,
    DEFAULT_HIDDEN_PUZZLE_HASH,
)

from cdv.test import SmartCoinWrapper, CoinPairSearch, CoinWrapper, Wallet

from checkers.gamerecords import GameRecords
from support import SpendResult, FakeCoin, GAME_MOJO, LARGE_NUMBER_OF_BLOCKS
from wallet import rpc_host, full_node_rpc_port, wallet_rpc_port, AGG_SIG_ME_ADDITIONAL_DATA

class CheckersRunnerWallet:
    def __init__(self,netname,blocks_ago):
        self.parent = None
        self.blocks_ago = blocks_ago
        self.wallet_rpc_client = None
        self.netname = netname
        self.mover = None
        self.public_key_fingerprints = []
        self.pk_ = None
        self.primary_sk_ = None
        self.sk_ = None
        self.puzzle = None
        self.puzzle_hash = None
        self.wallet = None
        self.usable_coins = {}
        self.game_records = None

    def pk_to_sk(self,pk):
        print('want pk %s (%s) have %s' % (pk, type(pk), self.pk_))
        if pk == self.pk_:
            return self.sk_

        print('primary_sk %s' % self.primary_sk_)
        try_sk = calculate_synthetic_secret_key(self.sk_, DEFAULT_HIDDEN_PUZZLE_HASH)
        try_pk = try_sk.get_g1()
        if pk == try_sk.get_g1():
            return try_sk

        # Maybe given a puzzle hash
        if pk == self.puzzle_hash:
            print('was given a puzzle hash but wanted a pk')

    async def puzzle_for_puzzle_hash(self, puzzle_hash):
        for pkdata in self.public_key_fingerprints:
            private_key = await self.wallet_rpc_client.get_private_key(pkdata)
            sk_data = binascii.unhexlify(private_key['sk'])
            for i in range(1000):
                sk_ = master_sk_to_wallet_sk(PrivateKey.from_bytes(sk_data), i)
                pk_ = sk_.get_g1()
                puzzle_ = puzzle_for_pk(pk_)
                if puzzle_.get_tree_hash() == puzzle_hash:
                    return puzzle_

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
            for i in range(1000):
                sk_ = master_sk_to_wallet_sk(PrivateKey.from_bytes(sk_data), i)
                pk_ = sk_.get_g1()
                if pk_ == pk:
                    puzzle = puzzle_for_pk(pk_)
                    puzzle_hash = puzzle.get_tree_hash()
                    print(puzzle)

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
        primary_sk_ = PrivateKey.from_bytes(sk_data)
        sk_ = master_sk_to_wallet_sk(primary_sk_, 0)
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

        await self.game_records.update_to_current_block(self.blocks_ago)

    async def find_coin_by_name(self,name):
        coin_record = await self.parent.get_coin_record_by_name(name)
        return coin_record

    async def select_identity_for_coin(self,coin):
        print('want puzzle hash %s' % coin.puzzle_hash)
        for pkdata in self.public_key_fingerprints:
            private_key = await self.wallet_rpc_client.get_private_key(pkdata)
            sk_data = binascii.unhexlify(private_key['sk'])
            for i in range(1000):
                primary_sk = PrivateKey.from_bytes(sk_data)
                sk_ = master_sk_to_wallet_sk(primary_sk, i)
                pk_ = sk_.get_g1()
                puzzle = puzzle_for_pk(pk_)
                print(i, puzzle)
                puzzle_hash = puzzle.get_tree_hash()

                print('try puzzle hash %s pk %s' % (puzzle_hash, pk_))
                if puzzle_hash == coin.puzzle_hash:
                    self.primary_sk_ = primary_sk
                    self.sk_ = sk_
                    self.pk_ = pk_

                    self.puzzle = puzzle_for_pk(self.pk_)
                    self.puzzle_hash = self.puzzle.get_tree_hash()

                    self.game_records.set_self_hash(self.puzzle_hash)
                    print('selected identity %s' % self.puzzle_hash)
                    print('pk %s' % self.pk_)
                    print('sk %s' % self.sk_)

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

        #
        # A note about what's going on here:
        #
        #  The standard coin is a 'delegated puzzle', and takes 3 arguments,
        #  - Either () in the delegated case or a secret key if the puzzle is hidden.
        #  - Code to run to generate conditions if the spend is allowed (a 'delegated'
        #    puzzle.  The puzzle given here quotes the desired conditions.
        #  - A 'solution' to the given puzzle: since this puzzle does not use its
        #    arguments, the argument list is empty.
        #
        delegated_puzzle_solution = Program.to((1, condition_args))
        solution = Program.to([[], delegated_puzzle_solution, []])

        #
        # Sign the (delegated_puzzle_hash + coin_name) with synthetic secret key
        #
        # Note that calculate_synthetic_secret_key must be used in sk_to_pk if
        # downstream puzzles are to be used compatibly to any of the chia
        # infrastructure.
        #
        original_coin_puzzle = self.puzzle_for_puzzle_hash(found_coin.as_coin().puzzle_hash)
        print(f'original coin puzzle %s' % original_coin_puzzle)
        solution_for_coin = CoinSpend(
            found_coin.as_coin(),
            original_coin_puzzle,
            solution
        )

        spend_bundle: SpendBundle = await sign_coin_spends(
            [solution_for_coin],
            pk_to_sk,
            AGG_SIG_ME_ADDITIONAL_DATA,
            DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM,
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

    async def spend_coin(self, coin, pushtx: bool = True, debug: bool = False, **kwargs):
        """Given a coin object, invoke it on the blockchain, either as a standard
        coin if no arguments are given or with custom arguments in args="""

        print(f'spend coin {coin}')

        amt = uint64(1)
        if "amt" in kwargs:
            amt = kwargs["amt"]

        if "puzzle" in kwargs:
            puzzle = kwargs["puzzle"]
        else:
            puzzle = coin.puzzle()

        delegated_puzzle_solution: Optional[Program] = None
        if "args" not in kwargs:
            target_puzzle_hash: bytes32 = self.puzzle_hash
            # Allow the user to 'give this much chia' to another user.
            if "to" in kwargs:
                toward: Union[bytes32, Wallet] = kwargs["to"]
                if isinstance(toward, bytes32):
                    target_puzzle_hash = toward
                else:
                    target_puzzle_hash = kwargs["to"].puzzle_hash

            # Automatic arguments from the user's intention.
            if "custom_conditions" not in kwargs:
                solution_list: List[List] = [[ConditionOpcode.CREATE_COIN, target_puzzle_hash, amt]]
            else:
                solution_list = kwargs["custom_conditions"]

            print(f'solution list {solution_list}')

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
                elif hasattr(remainer, 'puzzle_hash'):
                    solution_list.append([ConditionOpcode.CREATE_COIN, remainer.puzzle_hash, remain_amt])
                else:
                    raise ValueError("remainer is not a wallet or a smart coin")

            #
            # A note about what's going on here:
            #
            #  The standard coin is a 'delegated puzzle', and takes 3 arguments,
            #  - Either () in the delegated case or a secret key if the puzzle is hidden.
            #  - Code to run to generate conditions if the spend is allowed (a 'delegated'
            #    puzzle.  The puzzle given here quotes the desired conditions.
            #  - A 'solution' to the given puzzle: since this puzzle does not use its
            #    arguments, the argument list is empty.
            #
            delegated_puzzle_solution = Program.to((1, solution_list))
            # Solution is the solution for the old coin.
            solution = Program.to([[], delegated_puzzle_solution, []])
            print(f'solution {solution}')
        else:
            delegated_puzzle_solution = Program.to(kwargs["args"])
            solution = delegated_puzzle_solution

        puzzle_hash = puzzle.get_tree_hash()

        use_coin = coin
        if hasattr(coin, 'as_coin'):
            use_coin = coin.as_coin()

        solution_for_coin = CoinSpend(
            use_coin,
            puzzle,
            solution,
        )

        def pk_to_sk(pk):
            print('doing pk to sk on %s' % pk)
            return self.pk_to_sk(pk)

        try:
            sign_coin_spend_args = [
                [solution_for_coin],
                pk_to_sk,
                AGG_SIG_ME_ADDITIONAL_DATA,
                DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM,
            ]
            print('sign_coin_spend_args', sign_coin_spend_args)
            spend_bundle: SpendBundle = await sign_coin_spends(
                *sign_coin_spend_args
            )

        except Exception as e:
            print('exception',e.args)
            print('our pk is %s' % self.pk_)
            print('our sk is %s' % self.sk_)
            raise e

        if debug:
            spend_bundle.debug()

        if pushtx:
            pushed: Dict[str, Union[str, List[Coin]]] = await self.parent.push_tx(spend_bundle)
            return SpendResult(pushed)
        else:
            return spend_bundle

    async def push_tx(self,bundle):
        pushed: Dict[str, Union[str, List[Coin]]] = await self.parent.push_tx(bundle)
        return SpendResult(pushed)
