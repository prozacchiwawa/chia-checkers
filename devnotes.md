# How I made chia checkers (so far)

I made chia checkers in a few stages.  It started with a web app that included
the checkers game logic.  Some good aspects of this code were leveraging large
integers for board positions (king, red and black are bitmasks in this code),
and use of optional in the typed code to return a new board state given the old
curried in board state and a proposed move, which, when ported to chialisp
allowed Nothing values to be falsey and a "fromJust" function to be inserted
wherever the host code would extract from option.  fromJust throws if the
value is Nothing.

By writing a small DSL around the chialisp code and running it in place, I was
able to write one function at a time in chialisp and replace the host code with
it.  This allowed me to fairly quickly write checkers in chialisp.

I started with test cases in quexington's cdv tool running the raw chialisp code
and making assertions about it, which allowed me to work out a few things:

1) Figure out a way of representing the ongoing state of the contract code as
curried parameters.  This is an important aspect of chialisp.

The curry method of Program appends arguments to a chialisp program, returning
a program that takes fewer parameters, and models them as invoking the function
with the new subsequent parameters appended.  Because this returns a new program,
encoding the curried parameters in executable code, the resulting program has
a tree hash that's specific to this program and this set of parameters.  An
important thing to note is that when writing a game with a large state space,
the tree hash of the coin representing each subsequent game state depends on
the game state yielded by the most recent turn taken.

Something to consider is how wallet code tracks the game on the blockchain.
Because currying adds code in a simple and specific way to a given program,
it's possible to predict the outcome given just the tree hash of the program
and the tree hashes (note, parameter hashes consumed by 
```puzzle-hash-of-curried-function```
are tree hashes, which means that atoms and integers are passed as (sha256 1 arg).
A convention which seems to be emerging is to require the user to provide an
indication of the outcome state of whatever action they're taking in the program's
arguments as the arguments are available via the full node's RPC api: ```get_puzzle_and_solution```.  Using that, it's possible to get the chialisp expression
given to the program (and that actually yielded a spendable result).  Lots of
methods are possible to use to authenticate the arguments when scanning
transactions (and one may need to do this in a cooperative setting because another
user might have originated or mutated the game).  I used a very simple system in
the checkers game; the third argument is expected to be an alist with labels
of "launcher" and "board".  If these exist in the alist in the right form, the
solution (and the coin) are accepted as an update for the game with the
corresponding launcher.  A future iteration will convert the checkers code to use
singleton infrastructure and guarantee that it is the only copy of itself, but this
was enough to demo.

If using this method, currying in current board state and requiring the next game
state as an argument, the game is able to generate a spend of itself to a new
puzzle hash, and the new puzzle hash is derived from the fully general program's
tree hash and those of all curried in parameters, including the newly specified
game state.

In order to spend that coin, driver code needs:

1) The fully general inner program without curried arguments
2) Values of all fixed arguments (like the unique coin that spawned this object,
   puzzle hashes, pks or both of allowed participants
3) Values of state that varies over time, which would be curried last.

In order to share code and verify that the chialisp and host code are in sync,
one can run the existing block program in order to find out what it believes the
next tree hash will be.  I'd advise one to do this in the test case as things can
diverge unnoticed and differences can be hard to diagnose if they linger.

So it needs to first determine the current state of the game by examining recent
blocks and looking at solutions until it finds one that seems to update the game,
then generate a program to run locally, then run it with the final arguments and
see what it produces.  Doing things this way, one can ensure that the puzzle hash
used host side is derived from code that spends correctly.  It can be a bit
confusing to have multiple game states (the previous state and the next state)
in play in this way; you're required to compute the next state after all so that if
it's accepted then it can be picked up off the block chain.  Other arrangements are
possible, but care must be taken to ensure that enough information is available
to determine how to spend the previous coin and guarantee that the next one is
valid and can spend.

# The road to the blockchain

The objects in the test cases provide simplified versions of the existing rpc api
to the full node (we intend to remove any inconsistencies in the future).  A few
adjustments are needed:

1) The address given by ```chia wallet get_address``` is one of many that can
be derived from the wallet's master secure key.  Beause of this, when one finds
a coin that belongs to the game, if its conditions require AGG_SIG_ME then the
party that spends it must use the indexed secure key derived from the master key
corresponding with the coin being used.  A search is likely necessary of the
derived keys to find one that matches.

2) Additions and removals are debug artifacts from push_tx but may be retrieved
in a similar form once the block is farmed via the full node's
```get_additions_and_removals``` method if needed.

3) Use sqlite or something similar for persistence on the host side while running
a game in this way.  Store anything that can take time to scan the blockchain
for.

4) SpendBundle has a debug method that's really neat.  Use it if your spends
fail for any reason.

5) Because of the way chialisp's code uses sqlite, requesting tens of thousands
of blocks or block header hashes can result in a thrown exception from the rpc
service.  Ensure that updates are kept to a 1000 or so hashes or block numbers
per run.

6) Layering objects carefully between ones that coordinate blockchain interaction
and ones that require more definite information to avoid chicken and egg scenarios.

# The host code

The host code is fairly complicated and should be simplified, but it centers
around providing a few services:

1) Driver code that cooperates with the contract itself.  This code understands
how to produce the puzzle for a coin given the revealed information in the
arguments and how to anticipate the next puzzle hash.  It also covers extracting
information about the moves taken from the block chain.  This represents closure
over the puzzle and its arguments and so allows an upper edge interface to
be provided that is agnostic to these.

2) A wallet that stores tracked information as well as being able to provide the
necessary crypto keys that go along with the process of signing transactions.  It
also locates and provides standard coins to use to place one mojo in the coin.

3) An interface to the outside world that wires these pieces together as well as
displaying information and responding to user requests using the lower layers.

## The driver:

The main parts that I missed and had to debug:

- The curry method of Program takes SExp objects, but code computing the puzzle
  hash of a curried function takes tree hashes; either ```(sha256 1 atom)``` or
  ```(sha256 2 (sha256tree pair))``` depending on the argument content.
  
- When converting to the real blockchain, the main "private key" for a given wallet
  gives a "master" key, but actual coins are always signed with derived keys, so
  the keys curried in must belong with indexed derived keys and should in the
  launcher correspond to the key that received the chia that will be used.
  
- The procedure for scanning blocks and getting the puzzle solutions for coins
  wasn't the obviously right approach.  Specifically, I started by wanting to
  execute the block program and produce all inputs and outputs in one go, but
  it's generally advised against.
  
- Having a "simulate" method on the contract helped because i could execute it
  with the expected arguments and receive a more understandable exception.  I
  took quexington's advice and bisected code with intentional exceptions to
  determine the site of any specific error (really, we should annotate individual
  atoms with the location of the object in the source that led to producing them.
  In order to diagnose problems generating puzzle hashes, I annotated the code in
  clvm so i could see the produced hashes and their inputs, putting these in a
  table and tracking them:
  
    left is treehash of inner_puzzle_code.curry
    right is puzzle_hash_of_curried_function

    dc778a6 vs 86b7d33 rest (19b49b5 vs 2e0f14d)
      19b49b5 vs 2e0f14d rest (d91d16d vs aa26ea3)
        d91d16d vs aa26ea3 frst (9b2171d vs 54d2721)
          9b2171d vs 54d2721 rest (ef297ec vs a836638)
            ef297ec vs a836638 both (371f631 vs fd4e2e6 and 19cce02 vs dcb2094)
              371f631 vs fd4e2e6 rest (e560435 vs 29da174)
                e560435 vs 29da174 diff (left missing (q .))
              19cce02 vs dcb2094 frst (c13da40 vs a12ac5a)
                c13da40 vs a12ac5a rest (1eac656 vs d99aea4)
                  1eac656 vs d99aea4 both (1a2f4f7 vs d36aadb and 7246615 vs f4f0c56)
                    1a2f4f7 vs d36aadb rest (50332fe vs ef431f0)
                      50332fe vs ef431f0 diff (left missing (q .))
                    7246615 vs f4f0c56 frst (4aeca11 vs b87093a)

## The wallet:

The wallet code turned out to be complicated and intriciate.  There wasn't a single
specific thing that was difficult but few things lined up precisely:

- Since the test framework hands off coin spends to wallets, the wallet code also
  contains a call to SpendBundle::debug, which is informative.
  
- Several times, coins needed to be wrapped in the provided CoinWrapper, which
  contains the same info as Coin, but also the full puzzle, which is useful if
  you need to reveal the puzzle or run it.
  
- Since the wallet needs to interact with tracking that in turn knows what the
  user's identity is, but also derives that identity from the launcher coin,
  a dance is needed that allows us to construct these in the right way.  This
  structure can definitely be improved.
  
## The interface

- The main diffucult was deciding how deeply we're able to depend on blockchain
  provided data vs dev time, and I think I struck an appropriate balance by
  providing a single identifier for the game and allowing the game to scan
  the blockchain to recover its state.  This code should in the future use the
  singleton code.

- Sequencing the startups of the various objects could be a lot better.

