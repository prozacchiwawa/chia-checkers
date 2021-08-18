(mod (BASE_INNER_PUZZLE_HASH LAUNCHER P1_PK P2_PK P1_PH P2_PH AMT BOARD d1 m extra)

    (include "constants.clinc")
    (include "util.clinc")
    (include "math.clinc")
    (include "list.clinc")
    (include "option.clinc")

    (include "curry.clinc")

    (include "checker.clinc")
    (include "move.clinc")
    (include "board.clinc")

    (include "jump.clinc")

    (defun nextMove (b)
      (list
      (otherColor (board$next b))
      (board$king b)
      (board$red b)
      (board$black b)))

    (defun filterIsForwardMove (color l)
      (if l
          (if (forward color (r (f l)))
              (c (f l) (filterIsForwardMove color (r l)))
              (filterIsForwardMove color (r l))
              )
        ()
        )
      )

    (defun oneSpaceMovesRaw1 (checker allOneSpaceMoves)
      (if (isKing checker)
          allOneSpaceMoves
          (filterIsForwardMove (checkerColor checker) allOneSpaceMoves)
          )
      )

    (defun oneSpaceMovesRaw (checker)
      (oneSpaceMovesRaw1 checker (list (c -1 1) (c -1 -1) (c 1 1) (c 1 -1))))

    (defun oneSpaceMovesInBounds1 (X Y dx dy head rest)
      (if (inBounds (+ dx X) (+ dy Y)) (c head rest) rest))

    (defun oneSpaceMovesInBounds (pt l)
      (if l
          (oneSpaceMovesInBounds1
          (f pt)
          (r pt)
          (f (f l))
          (r (f l))
          (f l)
          (oneSpaceMovesInBounds pt (r l)))
          ()
          )
      )

    (defun oneSpaceMovesNotBlocked1 (pt b dx dy rest)
      (if (checkerAt (c (+ dx (f pt)) (+ dy (r pt))) b)
          rest
          (c (c dx dy) rest)
          )
      )
    (defun oneSpaceMovesNotBlocked (pt b l)
      (if l
          (oneSpaceMovesNotBlocked1
          pt b (f (f l)) (r (f l)) (oneSpaceMovesNotBlocked pt b (r l)))
          ()
          )
      )

    (defun mapToAvailableJumps1 (color pt b x y rest)
      (c (availableJumps () 2 color x y (f pt) (r pt) b)
        (mapToAvailableJumps color pt b rest))
      )

    (defun mapToAvailableJumps (color pt b l)
      (if l
          (mapToAvailableJumps1 color pt b (f (f l)) (r (f l)) (r l))
          ()
          )
      )
    (defun availableMovesForChecker1 (color pt b movesInBounds)
      (please_append_my_lists_i_promise_i_wont_use_a_reserved_word_to_describe_that_process
      (list
        (allowedJumps color pt b movesInBounds)
        (offsetPoints pt (oneSpaceMovesNotBlocked pt b movesInBounds))
        )
      )
      )

    (defun availableMovesForChecker (ch pt b)
      (availableMovesForChecker1
       (checkerColor ch)
       pt
       b
       (oneSpaceMovesInBounds pt (oneSpaceMovesRaw ch pt b))
       )
      )

    (defun offsetPoints (pt l)
      (if l
          (c
          (c (+ (f pt) (f (f l))) (+ (r pt) (r (f l))))
          (offsetPoints pt (r l))
          )
          ()
          )
      )

    (defun createCheckerMoves2 (X Y target) (c (c X Y) target))

    (defun createCheckerMoves1 (X Y targets)
      (if targets
          (c
          (createCheckerMoves2 X Y (f targets))
          (createCheckerMoves1 X Y (r targets))
          )
          ()
          )
      )

    (defun createCheckerMoves (c pt b)
      (createCheckerMoves1 (f pt) (r pt) (availableMovesForChecker c pt b))
      )

    (defun mapAvailableMovesForChecker1 (b pcpair)
      (createCheckerMoves (r pcpair) (f pcpair) b))

    (defun mapAvailableMovesForChecker (b l)
      (if l
          (c
          (mapAvailableMovesForChecker1 b (f l))
          (mapAvailableMovesForChecker b (r l))
          )
          ()
          )
      )

    (defun availableMoves (b)
      (please_append_my_lists_i_promise_i_wont_use_a_reserved_word_to_describe_that_process
      (mapAvailableMovesForChecker b (listCheckersWithColor 0 (board$next b) b))
      )
      )

    (defun filterCorrectColor (b ch)
      (if ch (if (= (checkerColor (fromJust ch)) (board$next b)) ch ()) ()))

    (defun filterValidDiagonal (m ch) (if ch (if (validDiagonal m) ch ()) ()))
    (defun filterSpaceIsFree (m b ch) (if ch (if (checkerAt (r m) b) () ch) ()))
    (defun filterToIsKing (ch) (if ch (just (isKing (fromJust ch))) ()))
    (defun filterKingOrForward (m b mKing)
      (if mKing
          (if (+ (fromJust mKing) (forward (board$next b) (r (direction m))))
              mKing
              ()
              )
          ()
          )
      )

    (defun mapKingToChecker (b mKing)
      (if mKing (just (c (fromJust mKing) (board$next b))) ()))

    (defun rejectIfLongDistanceAndNoJumps1 (ch jumps)
      (if jumps (just (c ch (fromJust jumps))) ()))

    (defun rejectIfLongDistanceAndNoJumps (m b ch)
      (if ch
          (if (= (manhattanDistance m) 1)
              (just (c (fromJust ch) ()))
              (rejectIfLongDistanceAndNoJumps1
              (fromJust ch) (jumps (board$next b) m b))
              )
          ()
          )
      )

    (defun removePieces (b jumps)
      (if jumps (removePieces (removeChecker (f jumps) b) (r jumps)) b))

    (defun updateBoardWithRemovedJumps (m b ch) (addChecker (r m) ch b))
    (defun maybePromote (m ch)
      (if (= (kingRow (checkerColor ch)) (r (r m))) (c 1 (checkerColor ch)) ch))

    (defun updateBoardWithMove (m b tmj)
      (if tmj
          (just
          (updateBoardWithRemovedJumps
            m
            (removePieces (removeChecker (f m) b) (r (fromJust tmj)))
            (maybePromote m (f (fromJust tmj)))
            )
          )
          ()
          )
      )

    (defun mapNextMove (b) (if b (just (nextMove (fromJust b))) ()))

    (defun move2 (m b)
      (mapNextMove
      (updateBoardWithMove m b
      (rejectIfLongDistanceAndNoJumps m b
      (mapKingToChecker b
      (filterKingOrForward m b
      (filterToIsKing
      (filterSpaceIsFree m b
      (filterValidDiagonal m
      (filterCorrectColor b (checkerAt (f m) b))
      )))))))))

    (defun move1 (mB) (if mB (fromJust mB) (x "invalid move")))
    (defun move (m b) (move1 (move2 m b)))

    (defun computeNextPuzzleHash (BASE_INNER_PUZZLE_HASH LAUNCHER P1_PK P2_PK P1_PH P2_PH AMT b)
      (puzzle-hash-of-curried-function
       BASE_INNER_PUZZLE_HASH
       (sha256tree b)
       (sha256 ONE AMT)
       (sha256 ONE P2_PH)
       (sha256 ONE P1_PH)
       (sha256 ONE P2_PK)
       (sha256 ONE P1_PK)
       (sha256 ONE LAUNCHER)
       (sha256 ONE BASE_INNER_PUZZLE_HASH)
       )
      )

    ; Return the conditions this layer requires.
    (defun makeMove (BASE_INNER_PUZZLE_HASH LAUNCHER P1_PK P2_PK P1_PH P2_PH AMT mM b)
      (list
       (list AGG_SIG_ME
             (if (board$next b) P2_PK P1_PK)
             (sha256tree mM)
             )

       (list CREATE_COIN
             (computeNextPuzzleHash
              BASE_INNER_PUZZLE_HASH LAUNCHER P1_PK P2_PK P1_PH P2_PH AMT b)
             AMT
             )
       )
      )

    (defun takeWin (P1_PK P2_PK P1_PH P2_PH AMT b)
      ; Note: P2_PK and P1_PK reverse of above since the player that can't
      ; move loses.
      (list
       (list AGG_SIG_ME (if (board$next b) P2_PK P1_PK) (sha256tree b))
       (list CREATE_COIN (if (board$next b) P2_PH P1_PH) AMT)
       )
      )

    (defun toMove1 (ul) (c (moddiv (f ul) 256) (moddiv (r ul) 256)))
    (defun toMove (m) (toMove1 (moddiv m 65536)))

    (defun validateLauncher1 (LAUNCHER fst rst)
      (if (= (f fst) "launcher") (= (r fst) LAUNCHER) rst)
      )

    (defun validateLauncher (LAUNCHER extra)
      1
      ;; (if extra
      ;;     (validateLauncher1 LAUNCHER (f extra) (validateLauncher LAUNCHER (r extra)))
      ;;   0)
        )

    (defun validateBoard1 (board_hash fst rst)
      (if (= (f fst) "board") (= (sha256tree (r fst)) board_hash) rst)
      )

    (defun validateBoard (board_hash extra)
      (if extra
          (validateBoard1 board_hash (f extra) (validateBoard board_hash (r extra)))
        0)
      )

    (defun validateInputs (LAUNCHER BOARD extra)
      (if (validateLauncher LAUNCHER extra)
          (if (validateBoard (sha256tree BOARD) extra)
              BOARD
            (x "board was not what was expected")
            )
        (x "launcher was not what was expected")
        )
      )

    (defun simulationResponse (BASE_INNER_PUZZLE_HASH LAUNCHER P1_PK P2_PK P1_PH P2_PH AMT b)
      (c
       (computeNextPuzzleHash
        BASE_INNER_PUZZLE_HASH LAUNCHER P1_PK P2_PK P1_PH P2_PH AMT b)
       b
       )
      )

    ; If a move is chosen, makeMove will return a new coin unless
    ; fromJust will throw if move didn't return a board, indicating that
    ; the move wasn't valid.
    (label "main"
           (if m
               (if (= d1 "simulate")
                   (simulationResponse BASE_INNER_PUZZLE_HASH LAUNCHER P1_PK P2_PK P1_PH P2_PH AMT (move (toMove (fromJust m)) BOARD))

                 (makeMove
                  BASE_INNER_PUZZLE_HASH
                  LAUNCHER
                  P1_PK
                  P2_PK
                  P1_PH
                  P2_PH
                  AMT
                  m ;; move as the parent signs this as an argument.
                  (validateInputs LAUNCHER (move (toMove (fromJust m)) BOARD) extra)
                  )
                 )

             (if (availableMoves BOARD)
                 (x "not a win yet")
               (label "takeWin" (takeWin P1_PK P2_PK P1_PH P2_PH AMT BOARD))
               )
             )
           )
    )
