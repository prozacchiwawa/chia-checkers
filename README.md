# chia-checkers

# Description

This is a checkers game for the chia blockchain, demonstrating a number of things that
are necessary for implementing multiparty apps on the chia blockchain.  It depends on
PR https://github.com/Chia-Network/chia-blockchain/pull/9453 for now.

Running chia checkers:

- Show an ID that someone can use with ```--launch``` to launch a checkers game.

    python gamewallet.py --my-pk

- Launch a new game with someone by ID

    python gamewallet.py --launch [other-player's id]
    ...
    you are playing black, identifier: e7...84
    
  The given identifier is used to check for updates to the game and make moves.
  
- Check the state of a game
    
    python gamewallet.py [game-identifier]

- Try to make a move

    python gamewallet.py [game-identifier] from_x,from_y:to_x,to_y
    
    Board positions count from 0,0 (upper left of the board, black side) to
    7,7 (lower right of the board, red side), so a valid first move for black
    is ```0,2:1,3```.

- The coin program only allows valid moves by the current player.  When the
  current player has no valid moves the game is over.
    
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
