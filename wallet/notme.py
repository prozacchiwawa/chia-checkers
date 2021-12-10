import asyncio

from chia.wallet.puzzles.p2_delegated_puzzle_or_hidden_puzzle import (  # standard_transaction
    puzzle_for_pk,
    calculate_synthetic_secret_key,
    DEFAULT_HIDDEN_PUZZLE_HASH,
)

from cdv.test import SmartCoinWrapper, CoinPairSearch, CoinWrapper, Wallet

class NotMeWallet(Wallet):
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
