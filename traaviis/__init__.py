"""TRAAVIIS -- a local-first toolchain for evidence-grade agent evaluation.

The `trvs` command evaluates a user-supplied agent against a frozen subject with
`trvs eval-one` -- admitting the eval bundle, binding the subject byte-exactly, and
folding one content-addressed `episode-…` receipt -- with TRVM worlds as its
exact-replay substrate (authored, run, inspected and verified over the existing
Forge engine's `wrl_*.py` identity/lowering spine plus the ic_ref and ic32
reducers). The world commands hold no world semantics of their own.

TRAAVIIS does not embed, select, or route a model. Evaluation runs a user-supplied
agent command, which may use one; Residency evaluation does not require Forge unless
the task requires the identity verifier.
"""

__version__ = "0.1.0"
