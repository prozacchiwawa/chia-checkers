import pytest

import io
import os
import os.path
import sys

from binascii import unhexlify

from clvm import SExp, to_sexp_f
from clvm.casts import int_from_bytes
from clvm.serialize import sexp_from_stream
from clvm.operators import OPERATOR_LOOKUP
from clvm.run_program import run_program

from clvm_tools.binutils import disassemble

from chia.clvm.singleton import SINGLETON_LAUNCHER
from chia.consensus.default_constants import DEFAULT_CONSTANTS
from chia.types.blockchain_format.sized_bytes import bytes32

from cdv.util.load_clvm import load_clvm
from cdv.test import setup as setup_test
from cdv.test import CoinWrapper

from checkers.driver import CheckersMover, INITIAL_BOARD, GAME_MOJO, make_move_sexp

def maskFor(x,y):
    return 1 << ((8 * x) + y)

def presentMask(bytesData,x,y):
    return int_from_bytes(bytesData) & maskFor(x,y)

#
# Theory of operation:
#
# This contract creates a playable game of checkers which carries some attributes
# of the game in its solution so that they can be picked out by another
# participant, including the identity of the coin that launched it, which must
# be restated to interact with it.
#
# The game is a function that accepts 3 arguments, for a normal move:
#
# (() (move) (("launcher" . launcher-coin) ("board" board)))
#
# The game uses a board state like this:
#
# (black-to-move king-mask red-mask black-mask)
#
# And it is curried in at each stage.  The copy in the third parameter, which
# as I understand things is intended to be an alist containing data we want to
# communicate to other users should contain the identity of the original parent
# coin, "launcher", which will be verified and the board state "board", which is
# also verified before any operation.  The next move is emitted with an AGG_SIG_ME
# for the player who's turn it was, so that turn order is enforced.
#
# A move is a number as in make_move_sexp.
#
# When no moves can be taken by the next player, the winning player may win the
# game by passing () for move and the chia is given to that player.
#
# The first argument may be given as "simulate" in which case, the contract can
# be asked to give its conception of the next puzzle hash and the board state
# that goes with it, given a move.  This is used in a rudimentary way for driver
# code to be able to ask the contract what will happen when a move is requested.
#
class TestCheckers:
    @pytest.fixture(scope="function")
    async def setup(self):
        inner_puzzle_code = load_clvm("checkers.cl", "checkers.code", search_paths=["checkers/code"])

        network, alice, bob = await setup_test()

        # Whole network value
        await network.farm_block()

        self.game_state = INITIAL_BOARD

        yield inner_puzzle_code, network, alice, bob

    # Code cribs a lot from pools code in chia-blockchain, also Quexington's
    # example piggy bank.
    @pytest.mark.asyncio
    async def test_can_launch(self, setup):
        inner_puzzle_code, network, alice, bob = setup

        await network.farm_block(farmer=alice)

        try:
            mover = CheckersMover(inner_puzzle_code, alice, bob)
            launch_coin = await alice.choose_coin(GAME_MOJO)
            _, launched_coin = await mover.launch_game(launch_coin)
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
            mover = CheckersMover(inner_puzzle_code, alice, bob)
            launch_coin = await alice.choose_coin(GAME_MOJO)
            _, launched_coin = await mover.launch_game(launch_coin)
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
            mover = CheckersMover(inner_puzzle_code, alice, bob)
            launch_coin = await alice.choose_coin(GAME_MOJO)
            _, launched_coin = await mover.launch_game(launch_coin)
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
            mover = CheckersMover(inner_puzzle_code, alice, bob)
            launch_coin = await alice.choose_coin(GAME_MOJO)
            _, launched_coin = await mover.launch_game(launch_coin)
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
            runner = CheckersMover(inner_puzzle_code, alice, bob)
            launch_coin = await alice.choose_coin(GAME_MOJO)
            launch_coin, launched_coin = await runner.launch_game(launch_coin)
            assert launched_coin

            bare_coin = await runner.make_move(0,2,1,3)
            assert bare_coin

            height = network.get_height()
            await runner.absorb_state(height, network)
            board = runner.get_board()
            assert not presentMask(board['black'], 0,2)
            assert presentMask(board['black'], 1,3)

            bare_coin = await runner.make_move(1,5,2,4)
            assert bare_coin

            height = network.get_height()
            await runner.absorb_state(height, network)
            board = runner.get_board()
            assert not presentMask(board['red'], 1,5)
            assert presentMask(board['red'], 2,4)

        finally:
            await network.close()
