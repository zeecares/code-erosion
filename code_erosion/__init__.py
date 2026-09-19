"""code-erosion: measure code verbosity and structural erosion.

Implements the two SlopCodeBench metrics described in
https://earendil.com/posts/measuring-code-sloppiness/ :

    Verbosity = |AST-grep flagged lines UNION clone lines| / SLOC
    mass(f)   = CC(f) * sqrt(SLOC(f))
    Erosion   = sum(mass(f) for CC(f) > 10) / sum(mass(f) for all f)

The Python engine follows scb-check 0.1.3 (the reference implementation
used by SlopCodeBench, https://github.com/SprocketLab/slop-code-bench,
MIT/Apache-2.0) including its bundled ast-grep rule set. TypeScript
support is an adaptation using the same method; see README for the
documented interpretation choices.
"""

__version__ = "0.6.1"
