"""Claude model identifiers.

Centralized so the rest of the codebase never inlines a model string. When a
new model is added or an existing one rotated, this is the only file that
changes; call sites already declare *which class* of work they're doing
(default vs. judgment-heavy) by picking ``SONNET`` vs. ``OPUS``.
"""

from __future__ import annotations

from enum import StrEnum


class ClaudeModel(StrEnum):
    """Anthropic model IDs the wrapper accepts.

    Pick based on the *kind* of work, not raw capability:

    - ``HAIKU`` — high-volume, cost-sensitive structured judging where
      the rubric is well-defined and the output is short. Used by the
      authenticity LLM judge (Step 3.6): one call per CV upload, rates
      Achievement Specificity + Internal Consistency on a 0-100 scale.
    - ``SONNET`` — routine extraction, classification, deterministic JSON,
      rephrasing. The general-purpose default.
    - ``OPUS`` — heavyweight reasoning: hard-filter scoring, multi-criteria
      rubric evaluation across long contexts, ambiguous or adversarial
      inputs. Call sites that pick ``OPUS`` should leave a one-line
      comment saying why.
    """

    HAIKU = "claude-haiku-4-5-20251001"
    SONNET = "claude-sonnet-4-6"
    OPUS = "claude-opus-4-7"
