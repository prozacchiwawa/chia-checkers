import pytest

import hashlib
import os
import os.path
import sys

from clvm import SExp
from clvm.more_ops import op_sha256
from clvm.operators import OPERATOR_LOOKUP
from clvm.run_program import run_program

from clvm_tools.binutils import disassemble

from chia.clvm.singleton import SINGLETON_LAUNCHER
from chia.consensus.default_constants import DEFAULT_CONSTANTS
from chia.types.blockchain_format.sized_bytes import bytes32

from cdv.util.load_clvm import load_clvm
from cdv.test import setup as setup_test
from cdv.test import ContractWrapper, CoinWrapper

LAUNCHER_MOD = load_clvm("singleton_launcher.clvm", "chia.wallet.puzzles")
SINGLETON_MOD = load_clvm("singleton_top_layer.clvm", "chia.wallet.puzzles")

GAME_MOJO = 1 # 1 mojo
INITIAL_BOARD = SExp.to([1, 0, 0xa040a040a040a040, 0x205020502050205])

def appendlog(s):
    with open('test.log','a') as f:
        f.write(s)
        f.write('\n')

def maskFor(x,y):
    return 1 << ((8 * x) + y)

def make_move_sexp(fromX,fromY,toX,toY):
    return fromX + (fromY << 8) + (toX << 16) + (toY << 24)

ONE = SExp.to(1)
TWO = SExp.to(2)
Q_KW = SExp.to(1)
A_KW = SExp.to(2)
C_KW = SExp.to(4)

def sha256(*args):
    return op_sha256(SExp.to(list(args)))[1]

def sha256tree(s):
    if s.pair:
        return sha256(SExp.to(2), sha256tree(s.pair[0]), sha256tree(s.pair[1]))
    else:
        return sha256(SExp.to(1), s)

def tree_hash_of_apply(function_hash,environment_hash):
    return sha256(
        TWO,
        sha256(ONE, A_KW),
        sha256(
            TWO,
            sha256(TWO, sha256(ONE, Q_KW), function_hash),
            sha256(TWO, environment_hash, sha256(ONE, 0))
        )
    )

def update_hash_for_parameter_hash(parameter_hash,environment_hash):
    return sha256(
        TWO,
        sha256(ONE, C_KW),
        sha256(
            TWO,
            sha256(TWO, sha256(ONE, Q_KW), parameter_hash),
            sha256(TWO, environment_hash, sha256(ONE, 0))
        )
    )

def build_curry_list(reversed_curry_parameter_hashes,environment_hash):
    if reversed_curry_parameter_hashes.listp():
        return build_curry_list(reversed_curry_parameter_hashes.rest(), update_hash_for_parameter_hash(reversed_curry_parameter_hashes.first(), environment_hash))
    else:
        return environment_hash

def puzzle_hash_of_curried_function(function_hash,*reversed_curry_parameter_hashes):
    return tree_hash_of_apply(
        function_hash,
        build_curry_list(SExp.to(list(reversed_curry_parameter_hashes)),sha256(ONE,ONE))
    )

#
# # A checkers game starts out with a knowable puzzle hash.
# Knowing the parent coin and amount allows us to identify it.
#
# It incorporates a currying of fixed parameters and the board, leaving just
# a move as a usable parameter for the solution.
#
class TestCheckers:
    @pytest.fixture(scope="function")
    async def setup(self):
        inner_puzzle_code = load_clvm("checkers.cl", "checkers.code")

        network, alice, bob = await setup_test()

        self.game_state = INITIAL_BOARD

        yield inner_puzzle_code, network, alice, bob

    def find_board_in_coin(self,coinrec):
        appendlog(f'coin {coinrec}')
        puzzle_solution = coinrec.solution.to_program()

        appendlog(f'solution {disassemble(puzzle_solution)}')

        extra_data_list = puzzle_solution.rest().rest().first()

        while not extra_data_list.nullp():
            ed_pair = extra_data_list.first()
            extra_data_list = extra_data_list.rest()

            ed_key = ed_pair.first()
            ed_value = ed_pair.rest()

            appendlog(f'k = {ed_key} v = {ed_value}')

            #puzzle_program = coinrec.puzzle_reveal.to_program()
            #cost, result = run_program(
            #    puzzle_program,
            #    puzzle_solution,
            #    OPERATOR_LOOKUP
            #)

    def block_callback(self,network):
        async def cb(height,block,additions,removals):
            for a in additions:
                coinrec = await network.get_puzzle_and_solution(a.name, height)
                if coinrec:
                    board = self.find_board_in_coin(coinrec)

        return cb

    async def launch_game(self,inner_puzzle_code,alice,bob):
        launch_coin = await alice.choose_coin(GAME_MOJO)
        assert launch_coin

        game_setup = inner_puzzle_code.curry(
            inner_puzzle_code.get_tree_hash(),
            launch_coin.name(), # Launcher
            alice.pk(),
            bob.pk(),
            alice.puzzle_hash,
            bob.puzzle_hash,
            GAME_MOJO,
            INITIAL_BOARD
        )

        appendlog(f'game_setup {game_setup}')
        appendlog(f'launch_coin name {launch_coin.name()}')

        result_coin = await alice.launch_smart_coin(
            game_setup,
            amt=GAME_MOJO,
            launcher=launch_coin
        )

        return launch_coin, result_coin

    # Code cribs a lot from pools code in chia-blockchain, also Quexington's
    # example piggy bank.
    @pytest.mark.asyncio
    async def test_can_launch(self, setup):
        inner_puzzle_code, network, alice, bob = setup

        await network.farm_block(farmer=alice)

        try:
            _, launched_coin = await self.launch_game(inner_puzzle_code,alice,bob)
            assert launched_coin

        finally:
            await network.close()

    # Code cribs a lot from pools code in chia-blockchain, also Quexington's
    # example piggy bank.
    @pytest.mark.asyncio
    async def test_can_move(self, setup):
        inner_puzzle_code, network, alice, bob = setup

        alice.add_block_callback(self.block_callback(network))
        await network.farm_block(farmer=alice)

        try:
            appendlog('test_can_move')
            _, launched_coin = await self.launch_game(inner_puzzle_code,alice,bob)
            assert launched_coin

            move = make_move_sexp(0,2,1,3)
            maybeMove = SExp.to(move).cons(SExp.to([]))

            simArgs = SExp.to(["simulate", maybeMove, []])
            appendlog(f'move is {simArgs}')
            cost, result = run_program(
                launched_coin.puzzle(),
                simArgs,
                OPERATOR_LOOKUP
            )

            appendlog(f'result {disassemble(result)}')

            args = SExp.to([[], maybeMove, [("board", result.rest()), ("launcher", launched_coin.name())]])
            appendlog(f'move is {args}')
            appendlog(f'puzzle is {disassemble(launched_coin.puzzle())}')
            appendlog(f'launched_coin {launched_coin}')

            after_first_move = await alice.spend_coin(
                launched_coin,
                push_tx=True,
                amt=GAME_MOJO,
                args=args
            )

            appendlog(f'after_first_move {after_first_move} {after_first_move.result} {after_first_move.outputs}')
            assert 'error' not in after_first_move.result

        finally:
            await network.close()

    # Wrong player can't move
    @pytest.mark.asyncio
    async def test_cant_make_invalid_move(self, setup):
        inner_puzzle_code, network, alice, bob = setup

        alice.add_block_callback(self.block_callback(network))
        await network.farm_block(farmer=alice)

        try:
            appendlog('test_can_move')
            _, launched_coin = await self.launch_game(inner_puzzle_code,alice,bob)
            assert launched_coin

            move = make_move_sexp(0,2,1,4)
            maybeMove = SExp.to(move).cons(SExp.to([]))

            black_start = 0x205020502050205
            source_mask = maskFor(0,2)
            target_mask = maskFor(1,4)
            black_move = black_start ^ source_mask ^ target_mask
            fake_board = SExp.to([1, 0, 0xa040a040a040a040, black_move])

            args = SExp.to([[], maybeMove, [("board", fake_board), ("launcher", launched_coin.name())]])
            appendlog(f'move is {args}')
            appendlog(f'puzzle is {disassemble(launched_coin.puzzle())}')
            appendlog(f'launched_coin {launched_coin}')

            after_first_move = await bob.spend_coin(
                launched_coin,
                push_tx=True,
                amt=GAME_MOJO,
                args=args
            )

            appendlog(f'after_first_move {after_first_move} {after_first_move.result} {after_first_move.outputs}')
            assert 'error' in after_first_move.result

        finally:
            await network.close()

    # Can't make invalid move.
    @pytest.mark.asyncio
    async def test_wrong_person_cant_move(self, setup):
        inner_puzzle_code, network, alice, bob = setup

        alice.add_block_callback(self.block_callback(network))
        await network.farm_block(farmer=alice)

        try:
            appendlog('test_can_move')
            _, launched_coin = await self.launch_game(inner_puzzle_code,alice,bob)
            assert launched_coin

            move = make_move_sexp(0,2,1,3)
            maybeMove = SExp.to(move).cons(SExp.to([]))

            simArgs = SExp.to(["simulate", maybeMove, []])
            cost, result = run_program(
                launched_coin.puzzle(),
                simArgs,
                OPERATOR_LOOKUP
            )

            appendlog(f'result {disassemble(result)}')

            args = SExp.to([[], maybeMove, [("board", result.get_tree_hash()), ("launcher", launched_coin.name())]])
            appendlog(f'move is {args}')
            appendlog(f'puzzle is {disassemble(launched_coin.puzzle())}')
            appendlog(f'launched_coin {launched_coin}')

            after_first_move = await bob.spend_coin(
                launched_coin,
                push_tx=True,
                amt=GAME_MOJO,
                args=args
            )

            appendlog(f'after_first_move {after_first_move} {after_first_move.result} {after_first_move.outputs}')
            assert 'error' in after_first_move.result

        finally:
            await network.close()

    @pytest.mark.asyncio
    async def test_can_move_each_player(self, setup):
        inner_puzzle_code, network, alice, bob = setup

        alice.add_block_callback(self.block_callback(network))
        await network.farm_block(farmer=alice)

        try:
            appendlog('test_can_move_each_player')
            launch_coin, launched_coin = \
                await self.launch_game(inner_puzzle_code,alice,bob)
            assert launched_coin

            move = make_move_sexp(0,2,1,3)
            maybeMove = SExp.to(move).cons(SExp.to([]))

            simArgs = SExp.to(["simulate", maybeMove, []])
            appendlog(f'alice sim')
            cost, result = run_program(
                launched_coin.puzzle(),
                simArgs,
                OPERATOR_LOOKUP
            )

            appendlog(f'result {disassemble(result)}')

            expectedPuzzleHash = bytes32(result.first().as_python())
            args = SExp.to([[], maybeMove, [("board", result.rest()), ("launcher", launched_coin.name())]])
            appendlog(f'move is {args}')
            appendlog(f'puzzle is {disassemble(launched_coin.puzzle())}')
            appendlog(f'launched_coin {launched_coin}')

            appendlog(f'alice spend')
            after_first_move = await alice.spend_coin(
                launched_coin,
                push_tx=True,
                amt=GAME_MOJO,
                args=args
            )

            assert 'error' not in after_first_move.result
            bare_coin = after_first_move.result['additions'][0]
            appendlog(f'bare_coin.puzzle_hash {bare_coin.puzzle_hash}')
            assert bare_coin.puzzle_hash == expectedPuzzleHash

            for coin in after_first_move.result['additions']:
                appendlog(f'add coin {coin.name()}')

            appendlog(f'launcher name {launch_coin.name()}')
            appendlog(f'next board {disassemble(result.rest())}')

            after_alice_move = inner_puzzle_code.curry(
                inner_puzzle_code.get_tree_hash(),
                launch_coin.name(), # Launcher
                alice.pk(),
                bob.pk(),
                alice.puzzle_hash,
                bob.puzzle_hash,
                GAME_MOJO,
                result.rest(),
            )

            appendlog(f'computing tree hash of after_alice_move')
            appendlog(f'computed via get_tree_hash {after_alice_move.get_tree_hash()}')
            appendlog(f'{sha256tree(after_alice_move)} {disassemble(after_alice_move)}')
            appendlog(f'new puzzle {sha256tree(after_alice_move)} vs {expectedPuzzleHash}')
            assert expectedPuzzleHash == after_alice_move.get_tree_hash()

            self.coin = CoinWrapper(
                bare_coin.parent_coin_info,
                after_alice_move.get_tree_hash(),
                GAME_MOJO,
                after_alice_move
            )

            move = make_move_sexp(1,5,2,4)

            appendlog(f'simulate bob spend')
            appendlog(f'spend coin {self.coin.name()}')
            appendlog(f'after_alice_move {after_alice_move}')

            maybeMove = SExp.to(move).cons(SExp.to([]))

            simArgs = SExp.to(["simulate", maybeMove, []])
            cost, result = run_program(
                after_alice_move,
                simArgs,
                OPERATOR_LOOKUP
            )

            args = SExp.to([[], maybeMove, [("board", result.rest()), ("launcher", launched_coin.name())]])
            appendlog(f'move is {args}')
            appendlog(f'self.coin {self.coin.name()} data {self.coin}')

            appendlog(f'do bob spend')
            after_second_move = await bob.spend_coin(
                self.coin,
                push_tx=True,
                amt=GAME_MOJO,
                args = args)

            assert 'error' not in after_second_move.result

        finally:
            await network.close()
