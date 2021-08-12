(mod (BASE_INNER_PUZZLE_HASH P1_PK P2_PK P1_PH P2_PH AMT BOARD d1 m extra)

    (defconstant AGG_SIG_ME 50)
    (defconstant CREATE_COIN 51)

    ;; curry support
    ;; The code below is used to calculate of the tree hash of a curried function
    ;; without actually doing the curry, and using other optimization tricks
    ;; like unrolling `sha256tree`.

    (defconstant ONE 1)
    (defconstant TWO 2)
    (defconstant A_KW #a)
    (defconstant Q_KW #q)
    (defconstant C_KW #c)

    ;; Given the tree hash `environment-hash` of an environment tree E
    ;; and the tree hash `parameter-hash` of a constant parameter P
    ;; return the tree hash of the tree corresponding to
    ;; `(c (q . P) E)`
    ;; This is the new environment tree with the addition parameter P curried in.
    ;;
    ;; Note that `(c (q . P) E)` = `(c . ((q . P) . (E . 0)))`

    (defun-inline update-hash-for-parameter-hash (parameter-hash environment-hash)
      (sha256 TWO (sha256 ONE C_KW)
                  (sha256 TWO (sha256 TWO (sha256 ONE Q_KW) parameter-hash)
                              (sha256 TWO environment-hash (sha256 ONE 0))))
    )

    ;; This function recursively calls `update-hash-for-parameter-hash`, updating `environment-hash`
    ;; along the way.

    (defun build-curry-list (reversed-curry-parameter-hashes environment-hash)
      (if reversed-curry-parameter-hashes
          (build-curry-list (r reversed-curry-parameter-hashes)
                            (update-hash-for-parameter-hash (f reversed-curry-parameter-hashes) environment-hash))
          environment-hash
      )
    )

    ;; Given the tree hash `environment-hash` of an environment tree E
    ;; and the tree hash `function-hash` of a function tree F
    ;; return the tree hash of the tree corresponding to
    ;; `(a (q . F) E)`
    ;; This is the hash of a new function that adopts the new environment E.
    ;; This is used to build of the tree hash of a curried function.
    ;;
    ;; Note that `(a (q . F) E)` = `(a . ((q . F)  . (E . 0)))`

    (defun-inline tree-hash-of-apply (function-hash environment-hash)
      (sha256 TWO (sha256 ONE A_KW)
                  (sha256 TWO (sha256 TWO (sha256 ONE Q_KW) function-hash)
                              (sha256 TWO environment-hash (sha256 ONE 0))))
    )

    ;; function-hash:
    ;;   the hash of a puzzle function, ie. a `mod`
    ;;
    ;; reversed-curry-parameter-hashes:
    ;;   a list of pre-hashed trees representing parameters to be curried into the puzzle.
    ;;   Note that this must be applied in REVERSED order. This may seem strange, but it greatly simplifies
    ;;   the underlying code, since we calculate the tree hash from the bottom nodes up, and the last
    ;;   parameters curried must have their hashes calculated first.
    ;;
    ;; we return the hash of the curried expression
    ;;   (a (q . function-hash) (c (cp1 (c cp2 (c ... 1)...))))

    (defun puzzle-hash-of-curried-function (function-hash . reversed-curry-parameter-hashes)
      (tree-hash-of-apply function-hash
                          (build-curry-list reversed-curry-parameter-hashes (sha256 ONE ONE)))
      )

    ; takes a lisp tree and returns the hash of it
    (defun sha256tree (TREE)
      (if (l TREE)
          (sha256 2 (sha256tree (f TREE)) (sha256tree (r TREE)))
        (sha256 1 TREE)
        )
      )

    (defun please_append_my_lists_i_promise_i_wont_use_a_reserved_word_to_describe_that_process (l1)
      (if l1
          (if (f l1)
              (c (f (f l1)) (please_append_my_lists_i_promise_i_wont_use_a_reserved_word_to_describe_that_process (c (r (f l1)) (r l1))))
              (please_append_my_lists_i_promise_i_wont_use_a_reserved_word_to_describe_that_process (r l1))
              )
          ())
      )

    (defun fromJust (mObj) (if mObj (f mObj) (x "fromJust on nothing")))
    (defun just (obj) (list obj))

    (defun moddiv1 (res) (c (r res) (f res)))
    (defun moddiv (n d) (moddiv1 (divmod n d)))

    (defun label (_ actually) (i actually actually actually))
    (defun maskFor (pt) (lsh 1 (+ (* 8 (f pt)) (r pt))))
    (defun makeKing (color) (c 1 color))
    (defun makePawn (color) (c 0 color))
    (defun m$fromX (m) (f (f m)))
    (defun m$fromY (m) (r (f m)))
    (defun m$toX (m) (f (r m)))
    (defun m$toY (m) (r (r m)))

    (defun board$next (b) (f b))
    (defun board$king (b) (f (r b)))
    (defun board$red (b) (f (r (r b))))
    (defun board$black (b) (f (r (r (r b)))))

    (defun checkerAt1 (mask b)
      (if (logand mask (board$red b))
          (list (if (logand mask (board$king b)) (makeKing 0) (makePawn 0)))
          (if (logand mask (board$black b))
              (list (if (logand mask (board$king b)) (makeKing 1) (makePawn 1)))
              (quote ())
              )
          )
      )
    (defun checkerAt (pt b) (checkerAt1 (maskFor pt) b))

    (defun removeChecker1 (mask b)
      (list
      (board$next b)
      (logxor (board$king b) (if (logand mask (board$king b)) mask 0))
      (logxor (board$red b) (if (logand mask (board$red b)) mask 0))
      (logxor (board$black b) (if (logand mask (board$black b)) mask 0)))
      )
    (defun removeChecker (pt b) (removeChecker1 (maskFor pt) b))

    (defun isKing (checker) (= (f checker) 1))
    (defun inBounds (X Y) (* (* (+ (> X 0) (= X 0)) (> 8 X)) (* (+ (> Y 0) (= Y 0)) (> Y y))))
    (defun manhattanDistance (m) (abs (- (m$fromX m) (m$toX m))))
    (defun abs (s) (if (> s 0) s (- 0 s)))

    (defun direction1 (fromX fromY toX toY) (c (- toX fromX) (- toY fromY)))
    (defun direction (m) (c (- (m$toX m) (m$fromX m)) (- (m$toY m) (m$fromY m))))

    (defun validDiagonal3 (dir) (= (abs (f dir)) (abs (r dir))))
    (defun validDiagonal2 (m) (validDiagonal3 (direction m)))
    (defun validDiagonal1 (m) (if (+ (= (m$fromX m) (m$toX m)) (= (m$fromY m) (m$toY m))) () (validDiagonal2 m)))
    (defun validDiagonal (m) (validDiagonal1 m))

    (defun checkerColor (ch) (r ch))
    (defun otherColor (color) (if (= color 0) 1 0))

    (defun addChecker2 (king red black b)
      (list (board$next b) (logior king (board$king b)) (logior red (board$red b)) (logior black (board$black b))))
    (defun addChecker1 (mask ch b)
      (addChecker2 (if (isKing ch) mask 0) (if (checkerColor ch) 0 mask) (if (checkerColor ch) mask 0) b))
    (defun addChecker (pt ch b) (addChecker1 (maskFor pt) ch b))

    (defun colorOfMaybeChecker (mCh) (if mCh (just (checkerColor (fromJust mCh))) ()))
    (defun jumpState$sEqSteps (js) (f js))
    (defun jumpState$sMod2Eq0 (js) (f (r js)))
    (defun jumpState$theChecker (js) (f (r (r js))))
    (defun jumpState$otherColor (js) (f (r (r (r js)))))
    (defun jumpAtCoords0 (fromX fromY dx dy steps s)
      (c (+ fromX (* s (/ dx steps))) (+ fromY (* s (/ dy steps)))))

    (defun jumpAtCoords (fromX fromY dir steps s)
      (jumpAtCoords0 fromX fromY (f dir) (r dir) steps s))

    (defun newJumpState2 (oc sEqSteps sMod2Eq0 theChecker)
      (list
      sEqSteps
      sMod2Eq0
      theChecker
      (= (colorOfMaybeChecker theChecker) (just oc))))

    (defun newJumpState1 (oc steps jcoord b s)
      (newJumpState2 oc (= steps s) (not (r (divmod s 2))) (checkerAt jcoord b)))

    (defun newJumpState (oc steps m b s)
      (newJumpState1
      oc
      steps
      (jumpAtCoords (m$fromX m) (m$fromY m) (direction m) steps s) b s))

    (defun jumpsNextStep (steps color m b a s js)
      (if (label "true true None _" (* (* (jumpState$sEqSteps js) (jumpState$sMod2Eq0 js)) (not (jumpState$theChecker js))))
          (just a)
          (if (label "_ true Some _" (* (jumpState$sMod2Eq0 js) (not (not (jumpState$theChecker js)))))
            ()
            (if (label "_ false _ true" (* (not (jumpState$sMod2Eq0 js)) (jumpState$otherColor js)))
                (nextJump1 steps color m b (c (jumpAtCoords (m$fromX m) (m$fromY m) (direction m) steps s) a) (+ s 1))
                (if (label "_ true None _" (* (not (jumpState$sMod2Eq0 js)) (jumpState$otherColor js)))
                    (nextJump1 steps color m b a (+ s 1))
                    ()
                    )
                )
            )
          )
      )

    (defun nextJump1 (steps color m b a s)
      (jumpsNextStep
      steps color m b a s (newJumpState (otherColor color) steps m b s)))

    (defun jumps (color m b) (nextJump1 (manhattanDistance m) color m b () 1))

    (defun forward (color dy) (i color (> dy 0) (> 0 dy)))
    (defun kingRow (color) (i color 7 0))

    (defun nextMove (b)
      (list
      (otherColor (board$next b))
      (board$king b)
      (board$red b)
      (board$black b)))

    (defun availableJumps2 (a s color dx dy x y b atX atY jlist)
      (availableJumps (if jlist (c (c atX atY) a) a) (+ s 2) color dx dy x y b))

    (defun availableJumps1 (a s color dx dy x y b atX atY)
      (if (inBounds atX atY)
        (availableJumps2
        a s color dx dy x y b atX atY
        (jumps color (c (c x y) (c atX atY)) b))
        a
        )
      )

    (defun availableJumps (a s color dx dy x y b)
      (availableJumps1 a s color dx dy x y b (+ x (* s dx)) (+ y (* s dy))))

    (defun listCheckersWithColor2 (head rest) (if head (c head rest) rest))
    (defun listCheckersWithColor1 (n color b chq)
      (listCheckersWithColor2
      (if chq
          (if (= (colorOfMaybeChecker chq) (just color))
              (c (moddiv n 8) (fromJust chq))
              ()
              )
          ()
          )
      (listCheckersWithColor (+ n 1) color b))
      )

    (defun listCheckersWithColor (n color b)
      (if (> n 63)
          ()
          (listCheckersWithColor1 n color b (checkerAt (moddiv n 8) b))
          )
      )

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
    (defun allowedJumps (color pt b l)
      (please_append_my_lists_i_promise_i_wont_use_a_reserved_word_to_describe_that_process (mapToAvailableJumps color pt b l)))

    (defun availableMovesForChecker1 (color pt b movesInBounds)
      (please_append_my_lists_i_promise_i_wont_use_a_reserved_word_to_describe_that_process
      (list
        (allowedJumps color pt b movesInBounds)
        (offsetPoints pt (oneSpaceMovesNotBlocked pt b movesInBounds))
        )
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

    (defun availableMovesForChecker (ch pt b)
      (availableMovesForChecker1
      (checkerColor ch)
      pt
      b
      (oneSpaceMovesInBounds pt (oneSpaceMovesRaw ch pt b))
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

    ; Return the conditions this layer requires.
    (defun makeMove (BASE_INNER_PUZZLE_HASH P1_PK P2_PK P1_PH P2_PH AMT mM b)
      (list
       (list AGG_SIG_ME
             (if (board$next b) P2_PK P1_PK)
             (sha256tree mM)
             )

       (list CREATE_COIN
            (puzzle-hash-of-curried-function
             BASE_INNER_PUZZLE_HASH
             (sha256tree b)
             (sha256 AMT)
             (sha256 P2_PH)
             (sha256 P1_PH)
             (sha256 P2_PK)
             (sha256 P1_PK)
             (sha256 BASE_INNER_PUZZLE_HASH)
             )
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

    ; If a move is chosen, makeMove will return a new coin unless
    ; fromJust will throw if move didn't return a board, indicating that
    ; the move wasn't valid.
    (label "main"
           (if m
               (if (= d1 "simulate")
                   (move (toMove (fromJust m)) BOARD)

                   (makeMove
                    BASE_INNER_PUZZLE_HASH
                    P1_PK
                    P2_PK
                    P1_PH
                    P2_PH
                    AMT
                    m ;; move as the parent signs this as an argument.
                    (move (toMove (fromJust m)) BOARD)
                    )
                   )

             (if (availableMoves BOARD)
                 (x "not a win yet")
               (label "takeWin" (takeWin P1_PK P2_PK P1_PH P2_PH AMT BOARD))
               )
             )
           )
    )
