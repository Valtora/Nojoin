"""Offline analysis for the Screen study of issue #653.

Does the meeting app's active-speaker ring, seen in a shared screen, help
Nojoin's speaker labels? This package answers that from frames the sampler
stored, without a model of any kind and without anyone looking at a frame.

- ``synth`` draws Teams, Zoom and Meet screens at runtime, with the lit tile
  known, so the detector can be built and gated on images nobody owns.
- ``detect`` finds the rings, groups them into tile slots and layouts, and
  says which tile is lit in each frame.
- ``align`` puts frames on the transcript clock and fits each platform's lag.
- ``metrics`` scores the pass rules that ``PREREG.md`` fixes in advance.
- ``run`` is the entry point for the no-network container. It writes
  ``summary.json`` and nothing else.
"""
