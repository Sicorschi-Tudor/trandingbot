"""ML layer (offline learning + shadow scoring). Learning runs separately from trading: models are trained by
`python -m app.ml train` on a DB snapshot, start as 'candidate', then 'shadow' (scored and logged, never trading);
only an explicit human approval (registry.approve) lets an entry model veto/scale or an exit model close early.

Modules: features (fs-v1 / xs-v1), dataset (from backtest/labeler), train (walk-forward, purge/embargo,
calibration), registry (ml_models + sha256-verified artifacts), infer (scoring helpers)."""
