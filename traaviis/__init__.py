"""TRAAVIIS -- evidence-grade environments for evaluating agents.

The `trvs` command authors, runs, inspects and verifies WallRiderLang worlds on
the deterministic TRVM runtime, and evaluates a user-supplied agent against a
frozen subject with `trvs eval-one` -- admitting the eval bundle, binding the
subject byte-exactly, and folding one content-addressed `episode-…` receipt.
This package is a thin terminal front-end over the existing Forge engine (the
`wrl_*.py` identity/lowering spine plus the ic_ref and ic32 reducers); it holds
no world semantics of its own and calls no model.
"""

__version__ = "0.1.0"
