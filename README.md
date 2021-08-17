# chia-checkers

Theory of operation:

This contract creates a playable game of checkers which carries some attributes
of the game in its solution so that they can be picked out by another
participant, including the identity of the coin that launched it, which must
be restated to interact with it.

The game is a function that accepts 3 arguments, for a normal move:

```(() (move) (("launcher" . launcher-coin) ("board" . board)))```

The game uses a board state like this:

```(black-to-move king-mask red-mask black-mask)```

Where black-to-move is treated as boolean and the rest are integers where each bit
`1 << ((8 * x) + y)` represents that the mask contains a true value at (`x`,`y`).

And it is curried in at each stage.  The copy in the third parameter, which
as I understand things is intended to be an alist containing data we want to
communicate to other users should contain the identity of the original parent
coin, "launcher", which will be verified and the board state "board", which is
also verified before any operation.  The next move is emitted with an AGG_SIG_ME
for the player who's turn it was, so that turn order is enforced.

A move is a number as in make_move_sexp.

When no moves can be taken by the next player, the winning player may win the
game by passing () for move and the chia is given to that player.

The first argument may be given as "simulate" in which case, the contract can
be asked to give its conception of the next puzzle hash and the board state
that goes with it, given a move.  This is used in a rudimentary way for driver
code to be able to ask the contract what will happen when a move is requested.
