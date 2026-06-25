# AI-Analytics

Shared detector engine for the operational-intelligence portfolio — Blockthrough
programmatic ad business. The portfolio *senses* and *explains* the drivers behind
`Revenue = AAPV x RPM / 1000`, model by model, each tied to one operational decision.

This repo is the **engine only**: the reusable machinery every model imports.
Per-model logic lives in the Hex notebooks.

## Install (from a notebook or environment)

```
pip install git+https://github.com/AKlearntogrow/AI-Analytics.git
```

```python
from detector import deseasonalize, robust_scale, downward_cusum, score_series
```

## Layout

See `CLAUDE.md` for the engineering rules and the hard constraints baked into the engine.
