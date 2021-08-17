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

def maskFor(x,y):
    return 1 << ((8 * x) + y)

def make_move_sexp(fromX,fromY,toX,toY):
    return fromX + (fromY << 8) + (toX << 16) + (toY << 24)

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

        await network.farm_block(farmer=alice)

        try:
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

            args = SExp.to([[], maybeMove, [("board", result.rest()), ("launcher", launched_coin.name())]])
            after_first_move = await alice.spend_coin(
                launched_coin,
                push_tx=True,
                amt=GAME_MOJO,
                args=args
            )

            assert 'error' not in after_first_move.result

        finally:
            await network.close()

    # Wrong player can't move
    @pytest.mark.asyncio
    async def test_cant_make_invalid_move(self, setup):
        inner_puzzle_code, network, alice, bob = setup

        await network.farm_block(farmer=alice)

        try:
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
            after_first_move = await bob.spend_coin(
                launched_coin,
                push_tx=True,
                amt=GAME_MOJO,
                args=args
            )

            assert 'error' in after_first_move.result

        finally:
            await network.close()

    # Can't make invalid move.
    @pytest.mark.asyncio
    async def test_wrong_person_cant_move(self, setup):
        inner_puzzle_code, network, alice, bob = setup

        await network.farm_block(farmer=alice)

        try:
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

            args = SExp.to([[], maybeMove, [("board", result.get_tree_hash()), ("launcher", launched_coin.name())]])
            after_first_move = await bob.spend_coin(
                launched_coin,
                push_tx=True,
                amt=GAME_MOJO,
                args=args
            )

            assert 'error' in after_first_move.result

        finally:
            await network.close()

    @pytest.mark.asyncio
    async def test_can_move_each_player(self, setup):
        inner_puzzle_code, network, alice, bob = setup

        await network.farm_block(farmer=alice)

        try:
            launch_coin, launched_coin = \
                await self.launch_game(inner_puzzle_code,alice,bob)
            assert launched_coin

            move = make_move_sexp(0,2,1,3)
            maybeMove = SExp.to(move).cons(SExp.to([]))

            simArgs = SExp.to(["simulate", maybeMove, []])
            cost, result = run_program(
                launched_coin.puzzle(),
                simArgs,
                OPERATOR_LOOKUP
            )

            expectedPuzzleHash = bytes32(result.first().as_python())
            args = SExp.to([[], maybeMove, [("board", result.rest()), ("launcher", launched_coin.name())]])
            after_first_move = await alice.spend_coin(
                launched_coin,
                push_tx=True,
                amt=GAME_MOJO,
                args=args
            )

            assert 'error' not in after_first_move.result
            bare_coin = after_first_move.result['additions'][0]
            assert bare_coin.puzzle_hash == expectedPuzzleHash

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

            assert expectedPuzzleHash == after_alice_move.get_tree_hash()

            self.coin = CoinWrapper(
                bare_coin.parent_coin_info,
                after_alice_move.get_tree_hash(),
                GAME_MOJO,
                after_alice_move
            )

            move = make_move_sexp(1,5,2,4)
            maybeMove = SExp.to(move).cons(SExp.to([]))

            simArgs = SExp.to(["simulate", maybeMove, []])
            cost, result = run_program(
                after_alice_move,
                simArgs,
                OPERATOR_LOOKUP
            )

            args = SExp.to([[], maybeMove, [("board", result.rest()), ("launcher", launched_coin.name())]])
            after_second_move = await bob.spend_coin(
                self.coin,
                push_tx=True,
                amt=GAME_MOJO,
                args = args)

            assert 'error' not in after_second_move.result

        finally:
            await network.close()
