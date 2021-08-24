import io
import time
from binascii import unhexlify

from clvm import SExp, to_sexp_f
from clvm.casts import int_from_bytes, int_to_bytes
from clvm.operators import OPERATOR_LOOKUP
from clvm.run_program import run_program
from clvm.serialize import sexp_from_stream

from clvm_tools.binutils import disassemble

from chia.types.blockchain_format.program import Program
from chia.types.blockchain_format.sized_bytes import bytes32

from cdv.test import CoinWrapper

GAME_MOJO = 1 # 1 mojo
INITIAL_BOARD_PYTHON = [1, 0, int_to_bytes(0xa040a040a040a040), int_to_bytes(0x205020502050205)]
INITIAL_BOARD = SExp.to(INITIAL_BOARD_PYTHON)

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

class CheckersMover:
    def __init__(self,inner_puzzle_code,player_black,player_red,launcher_name = None):
        self.inner_puzzle_code = inner_puzzle_code
        self.known_height = 1
        self.black = player_black
        self.red = player_red
        self.launch_coin = launcher_name
        self.first_coin = None
        self.current_coin = None
        self.board = INITIAL_BOARD_PYTHON

    async def launch_game(self,launch_coin):
        game_setup = self.inner_puzzle_code.curry(
            self.inner_puzzle_code.get_tree_hash(),
            launch_coin.name(), # Launcher
            self.black.pk(),
            self.red.pk(),
            self.black.puzzle_hash,
            self.red.puzzle_hash,
            GAME_MOJO,
            INITIAL_BOARD
        )

        result_coin = await self.black.launch_smart_coin(
            game_setup,
            amt=GAME_MOJO,
            launcher=launch_coin
        )

        self.launch_coin = launch_coin
        self.first_coin = result_coin
        self.current_coin = result_coin

        return launch_coin, result_coin

    def set_board(self, board):
        self.board = boardDictToLinear(board)

    def get_board(self):
        return {
            'blackmove': self.board[0] != b'',
            'king': convert_to_int(self.board[1]),
            'red': convert_to_int(self.board[2]),
            'black': convert_to_int(self.board[3])
        }

    def get_coin_puzzle(self):
        return self.inner_puzzle_code.curry(
            self.inner_puzzle_code.get_tree_hash(),
            self.launch_coin.name(), # Launcher
            self.black.pk(),
            self.red.pk(),
            self.black.puzzle_hash,
            self.red.puzzle_hash,
            GAME_MOJO,
            SExp.to(self.board)
        )


    def get_next_mover(self):
        if self.board[0] != b'':
            return self.black
        else:
            return self.red

    def set_current_coin(self,current_coin):
        self.current_coin = current_coin

    def set_launch_coin(self,launch_coin):
        self.launch_coin = launch_coin

    async def make_move(self,fromX,fromY,toX,toY):
        move = make_move_sexp(fromX,fromY,toX,toY)
        maybeMove = SExp.to(move).cons(SExp.to([]))

        current_puzzle = self.inner_puzzle_code.curry(
            self.inner_puzzle_code.get_tree_hash(),
            self.launch_coin.name(), # Launcher
            self.black.pk(),
            self.red.pk(),
            self.black.puzzle_hash,
            self.red.puzzle_hash,
            GAME_MOJO,
            SExp.to(self.board)
        )

        simArgs = SExp.to(["simulate", maybeMove, []])
        cost, result = run_program(
            current_puzzle,
            simArgs,
            OPERATOR_LOOKUP
        )

        # if self.current_coin is not self.first_coin:
        #     assert current_puzzle.get_tree_hash() == self.current_coin.puzzle_hash

        expectedPuzzleHash = bytes32(result.first().as_python())
        after_move_puzzle = self.inner_puzzle_code.curry(
            self.inner_puzzle_code.get_tree_hash(),
            self.launch_coin.name(), # Launcher
            self.black.pk(),
            self.red.pk(),
            self.black.puzzle_hash,
            self.red.puzzle_hash,
            GAME_MOJO,
            result.rest(),
        )
        assert after_move_puzzle.get_tree_hash() == expectedPuzzleHash

        player_to_move = self.get_next_mover()
        moveTail = [
            ("board", result.rest()),
            ("launcher", self.launch_coin.name())
        ]
        args = SExp.to([[], maybeMove, moveTail])
        print(f'doing spend from {player_to_move.puzzle_hash}')
        after_move_txn = await player_to_move.spend_coin(
            self.current_coin,
            push_tx=True,
            amt=GAME_MOJO,
            args=args
        )

        assert 'error' not in after_move_txn.result
        if hasattr(after_move_txn.result, 'additions'):
            bare_coin = after_move_txn.result['additions'][0]

            self.current_coin = CoinWrapper(
                bare_coin.parent_coin_info,
                after_move_puzzle.get_tree_hash(),
                GAME_MOJO,
                after_move_puzzle
            )

            assert self.current_coin.puzzle_hash == expectedPuzzleHash
            return self.current_coin
        else:
            return True

    def take_new_coin(self,coin,solution):
        kv_pairs = solution.rest().rest().first()

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

        want_launch_name = None
        if hasattr(self.launch_coin,'name'):
            want_launch_name = self.launch_coin.name()
        else:
            want_launch_name = self.launch_coin

        if board and launcher and launcher == want_launch_name:
            self.current_coin = coin
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
                solution = Program.to(sexp_from_stream(io.BytesIO(unhexlify(str(spend.solution))), to_sexp_f))
                self.take_new_coin(a.coin,solution)

        self.known_height = height

