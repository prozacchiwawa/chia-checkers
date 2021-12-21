import io
import os
import time
from typing import List, Tuple, Optional
from binascii import hexlify, unhexlify

from blspy import AugSchemeMPL

from clvm import SExp, to_sexp_f
from clvm.casts import int_from_bytes, int_to_bytes
from chia.consensus.default_constants import DEFAULT_CONSTANTS
from clvm.operators import OPERATOR_LOOKUP
from clvm.run_program import run_program
from clvm.serialize import sexp_from_stream

from clvm_tools.binutils import disassemble

from chia.types.blockchain_format.coin import Coin
from chia.types.blockchain_format.program import Program, SerializedProgram
from chia.types.blockchain_format.sized_bytes import bytes32
from chia.types.coin_spend import CoinSpend
from chia.types.condition_opcodes import ConditionOpcode
from chia.types.spend_bundle import SpendBundle
from chia.wallet.lineage_proof import LineageProof
from chia.wallet.puzzles.load_clvm import load_clvm
from chia.wallet.puzzles.singleton_top_layer import lineage_proof_for_coinsol, adapt_inner_to_singleton, generate_launcher_coin
# Singleton top layer methods
from chia.wallet.puzzles.singleton_top_layer import \
    puzzle_for_singleton, \
    launch_conditions_and_coinsol, \
    solution_for_singleton

from chia.util.condition_tools import conditions_dict_for_solution, pkm_pairs_for_conditions_dict
from chia.util.hash import std_hash
from chia.util.ints import uint64

from cdv.test import CoinWrapper

GAME_MOJO = 1 # 1 mojo, singleton requires odd number
INITIAL_BOARD_PYTHON = [1, 0, int_to_bytes(0xa040a040a040a040), int_to_bytes(0x205020502050205)]
INITIAL_BOARD = SExp.to(INITIAL_BOARD_PYTHON)

SINGLETON_MOD = load_clvm("singleton_top_layer.clvm")
SINGLETON_MOD_HASH = SINGLETON_MOD.get_tree_hash()
SINGLETON_LAUNCHER = load_clvm("singleton_launcher.clvm")
SINGLETON_LAUNCHER_HASH = SINGLETON_LAUNCHER.get_tree_hash()

def maskFor(x,y):
    return 1 << ((8 * x) + y)

def convert_to_int(b):
    if type(b) == type(0):
        return b
    elif type(b) == type(False):
        if b:
            return 1
        else:
            return 0
    else:
        return int_from_bytes(b)

def showBoard(b):
    outstr = io.StringIO()
    print(f'black move {b} {convert_to_int(b[0])}')

    if convert_to_int(b[0]):
        outstr.write('Black to move\n')
    else:
        outstr.write('Red to move\n')

    for i in range(64):
        x = i % 8
        y = int(i / 8)
        bit = maskFor(x,y)
        king = maskFor(x,y) & convert_to_int(b[1])
        red = maskFor(x,y) & convert_to_int(b[2])
        black = maskFor(x,y) & convert_to_int(b[3])

        if x == 0 and y != 0:
            outstr.write('\n')

        if red or black:
            if king:
                outstr.write('K')
            else:
                outstr.write('p')

            if red:
                outstr.write('R')
            elif black:
                outstr.write('B')
        else:
            outstr.write('  ')

    return outstr.getvalue()

def boardDictToLinear(b):
    return [b['blackmove'], b['king'], b['red'], b['black']]

def showBoardFromDict(b):
    return showBoard(boardDictToLinear(b))

def make_move_sexp(fromX,fromY,toX,toY):
    return fromX + (fromY << 8) + (toX << 16) + (toY << 24)

def tohex(b):
    if b is None:
        return None
    if type(b) == str:
        return b
    else:
        return hexlify(b).decode('utf8')

class CheckersMover:
    def __init__(self,inner_puzzle_code: Program,player_black,player_red,launcher_name: Optional[bytes] = None):
        self.inner_puzzle_code = inner_puzzle_code
        self.known_height = 1
        self.black = player_black
        self.red = player_red
        self.launch_coin_name = launcher_name
        self.current_coin_name = None
        self.parent_puzzle_hash = None
        self.board = INITIAL_BOARD_PYTHON

    async def launch_game(self,launch_coin):
        """
        Main game launcher.  Produce a new coin with GAME_MOJO balance whose
        puzzle hash is the hash of a curried puzzle containing the id of the
        launcher and suitable pk IDs for two participants.

        The resulting coin will only be spendable into another checkers coin
        given a replay of the current known board state and launcher, and a
        move that is allowed in that state.  This allows observers to recognize
        the game and update its state by looking at the arguments to the coin
        using the ```get_puzzle_and_solution``` rpc method.
        """

        print(f'launch with coin {launch_coin.name()}')

        game_comment = [
            ("game", "checkers"),
            ("board", INITIAL_BOARD),
            ("launcher", launch_coin.name())
        ]

        # Ensure black knows what wallet the coin we're using came from
        await self.black.select_identity_for_coin(launch_coin)

        # Figure out the full singleton solution
        self.launch_coin_name = Coin(
            launch_coin.name(),
            SINGLETON_LAUNCHER_HASH,
            GAME_MOJO
        ).name()

        original_coin_puzzle = await self.black.puzzle_for_puzzle_hash(launch_coin.as_coin().puzzle_hash)
        inner_puzzle = self.get_coin_puzzle()
        created_singleton_puzzle = puzzle_for_singleton(
            launch_coin.name(),
            inner_puzzle
        )
        created_singleton_puzzle_hash = created_singleton_puzzle.get_tree_hash()

        # Conditions is the second argument to a conventional spend of launch_coin
        # Spend is the subsequent spend of the launcher to become launched.
        print(f'creating launcher with pk {self.black.pk()} from launch coin {launch_coin.name()}')
        launch_conditions, spend = launch_conditions_and_coinsol(
            launch_coin.as_coin(),
            inner_puzzle,
            game_comment,
            GAME_MOJO
        )

        print(f'launch coin {hexlify(launch_coin.name())}')
        print(f'launch_conditions {Program.to(launch_conditions)}')
        print(f'proposed spend {spend}')
        print(f'spend.puzzle_reveal {spend.puzzle_reveal.get_tree_hash()} vs {SINGLETON_LAUNCHER_HASH}')

        launch_coin_2 = Coin(
            launch_coin.name(),
            spend.puzzle_reveal.get_tree_hash(),
            amount=GAME_MOJO
        )

        self.launch_coin_name = launch_coin_2.name()

        assert self.black
        assert self.black.puzzle_hash
        launch_coin_spend_into_singleton_launcher = await self.black.spend_coin(
            launch_coin,
            amt=GAME_MOJO,
            puzzle=self.black.puzzle,
            args=Program.to([[], (1, launch_conditions), []]),
            to=Program.fromhex(str(spend.puzzle_reveal)).get_tree_hash(),
            remain=self.black,
            pushtx=False
        )

        assert launch_coin_2.name() == Coin(
            launch_coin.name(),
            SINGLETON_LAUNCHER_HASH,
            GAME_MOJO
        ).name()

        print(f'second spend {spend}')
        print(f'puzzle hash of eve coin is {created_singleton_puzzle_hash}')
        print(f'expected parent of eve coin is {launch_coin_2.name()}')

        launch_coin_spend_into_singleton_launcher.coin_spends.append(spend)
        await self.black.push_tx(launch_coin_spend_into_singleton_launcher)

        result_coin = Coin(
            launch_coin_2.name(),
            created_singleton_puzzle_hash,
            GAME_MOJO
        )
        print(f'expected eve coin name is {hexlify(result_coin.name())}')

        self.current_coin = result_coin

        print(f'returning coins {self.current_coin.name()}')
        return self.launch_coin_name, self.current_coin

    def set_board(self, board):
        self.board = boardDictToLinear(board)

    def get_board(self):
        return {
            'blackmove': self.board[0] != b'',
            'king': convert_to_int(self.board[1]),
            'red': convert_to_int(self.board[2]),
            'black': convert_to_int(self.board[3])
        }

    def get_puzzle_for_board_state(self,board):
        """
        Prepare the bare checkers game to be used to play a specific game.
        By providing the hash of a specifically curried program, the coin
        will only be spendable by a matching program with similarly pre-
        specified arguments.
        """
        print(f'currying in identities BLACK {self.black.pk()} RED {self.red.pk()}')
        return self.inner_puzzle_code.curry(
            SINGLETON_MOD_HASH,
            SINGLETON_LAUNCHER_HASH,
            self.inner_puzzle_code.get_tree_hash(),
            self.launch_coin_name, # Launcher
            self.black.pk(),
            self.red.pk(),
            self.black.puzzle_hash,
            self.red.puzzle_hash,
            GAME_MOJO,
            SExp.to(board)
        )

    def get_coin_puzzle(self):
        return self.get_puzzle_for_board_state(self.board)

    def get_next_mover(self):
        """Return the wallet whose move is next"""
        if self.board[0] != b'':
            return self.black
        else:
            return self.red

    def set_current_coin_name(self,current_coin_name: bytes):
        self.current_coin_name = current_coin_name

    def set_launch_coin_name(self,launch_coin_name: bytes):
        self.launch_coin_name = launch_coin_name

    def own_conception_of_coin_id(self,launch_coin_name,launcher_puzzle_hash,amount):
        _, sha256_result = run_program(
            Program.to([11, (1, launch_coin_name), (1, launcher_puzzle_hash), (1, amount)]),
            [],
            OPERATOR_LOOKUP
        )

        return sha256_result

    async def make_move(self,parent_list,fromX,fromY,toX,toY):
        """
        Given from and to coordinates, prepare arguments and spend the latest
        coin of the game to perform the user's move.  If anything about this
        is incorrect, the coin won't allow the spend.
        """

        print('do move based on')
        for p in parent_list:
            print(f'{p.name} p')

        move = make_move_sexp(fromX,fromY,toX,toY)
        maybeMove = SExp.to(move).cons(SExp.to([]))

        current_puzzle = self.get_coin_puzzle()

        simArgs = SExp.to([0, "simulate", maybeMove, []])
        cost, result = run_program(
            current_puzzle,
            simArgs,
            OPERATOR_LOOKUP
        )
        self.parent_puzzle_hash = current_puzzle.get_tree_hash()

        print(f'result {result}')

        expectedPuzzleHash = bytes32(result.first().as_python())

        player_to_move = self.get_next_mover()
        moveTail = [
            ("game", "checkers"),
            ("board", result.rest()),
            ("launcher", self.launch_coin_name)
        ]

        sing_adapted_puzzle = puzzle_for_singleton(
            self.launch_coin_name,
            current_puzzle
        )
        new_adapted_puzzle = puzzle_for_singleton(
            self.launch_coin_name,
            self.get_puzzle_for_board_state(result.rest())
        )
        inner_program_args = SExp.to([[], maybeMove, moveTail])

        # A fake coin spend that will be used as a container for the lineage
        # Proof calculation.
        print(f'game coin is {hexlify(self.current_coin_name)}')

        # Lineage proof is constructed differently depending on whether this is
        # the first spend.  In the case of checkers, we give the originator the
        # first move so they will be responsible for constructing it differently.
        #
        # We'll do this based on the game state.  This game can't return to the
        # start state so the following is ok.
        start_state = True
        for i in range(4):
            if Program.to(self.board[i]) != Program.to(INITIAL_BOARD_PYTHON[i]):
                start_state = False

        use_puzzle_hash_for_lineage = None
        if not start_state:
            use_puzzle_hash_for_lineage = parent_list[0].coin.puzzle_hash

        print(f'use_puzzle_hash_for_lineage {type(use_puzzle_hash_for_lineage)}')

        print(f'parent coin spending {hexlify(self.current_coin_name)}')
        print(f'board state curried into spend {self.board}')

        args = solution_for_singleton(
            LineageProof(
                parent_list[0].coin.parent_coin_info,
                use_puzzle_hash_for_lineage,
                GAME_MOJO
            ),
            GAME_MOJO,
            inner_program_args
        )
        print(f'singleton args {args}')

        print(f'run adapted puzzle {sing_adapted_puzzle}')
        print(f'with args {args}')
        puzzle_result = sing_adapted_puzzle.run(args)

        print(f'doing spend from {player_to_move.puzzle_hash}')
        print(f'coin heritage: {parent_list}')
        after_move_txn = await player_to_move.spend_coin(
            parent_list[-1].coin,
            puzzle=sing_adapted_puzzle,
            amt=GAME_MOJO,
            args=args,
            debug=True
        )

        assert 'error' not in after_move_txn.result
        if hasattr(after_move_txn.result, 'additions'):
            bare_coin = after_move_txn.result['additions'][0]

            current_coin = CoinWrapper(
                bare_coin.parent_coin_info,
                new_adapted_puzzle.get_tree_hash(),
                GAME_MOJO,
                new_adapted_puzzle
            )

            return current_coin
        else:
            return True

    def take_new_coin(self,coin,solution):
        """
        Given a coin and solution from the blockchain, determine whether
        the coin refers to a game we're watching and if so use it as the
        current game state.
        """
        try:
            kv_pairs = solution.rest().rest().first().rest().rest().first()
        except:
            print(f'bailing take_new_coin on solution {solution}')
            return

        board = None
        launcher = None

        if not kv_pairs.listp():
            return

        for p in kv_pairs.as_python():
            if len(p) < 2:
                continue

            if p[0] == b'launcher':
                launcher = p[1]
            elif p[0] == b'board':
                board = p[1:]

        want_launch_name = self.launch_coin_name

        print(f'launcher {tohex(launcher)} want {tohex(want_launch_name)}')
        if board and launcher and tohex(launcher) == tohex(want_launch_name):
            print(f'found board {board}')
            self.current_coin_name = coin.name()
            self.board = board

    async def absorb_state(self,height,network):
        blockrec = await network.get_block_record_by_height(height)
        header_hash = blockrec.header_hash

        additions, _ = await network.get_additions_and_removals(header_hash)

        for a in additions:
            if a.coin.amount >= 1000:
                continue

            spend = await network.get_puzzle_and_solution(a.coin.parent_coin_info, height)
            if spend:
                print(f'coin: {a.coin.name()} spend {spend}')
                solution = Program.to(sexp_from_stream(io.BytesIO(unhexlify(str(spend.solution))), to_sexp_f))
                self.take_new_coin(a.coin,solution)

        self.known_height = height

