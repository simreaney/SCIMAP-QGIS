# SCIMAP Fitted 2: Calibrate Weights

The second of the three [SCIMAP Fitted](11-fitted-1-catchment-statistics.md)
tools. Takes the per-site, per-class statistics table produced by
[SCIMAP Fitted 1](11-fitted-1-catchment-statistics.md) (or an equivalent
summary CSV from the SCIMAP-Fitted research scripts) plus observed
water-quality values, and searches land-cover weight space for the vector that
ranks the sites in the same order the observations do.

For every sampled weight vector, the predicted risk at each monitoring site is
compared against the observations by **Spearman rank correlation**, and the
best-fitting vectors are kept. Pearson correlation and R² are reported as
diagnostics only: the prediction is an arbitrary-scale index and the
observation is a concentration, so R² between them measures the scale mismatch
as much as the fit.

**Where to find it:** Processing Toolbox → SCIMAP → **SCIMAP Fitted 2:
Calibrate Weights**; toolbar icon; `&SCIMAP` menu.

**Screenshot:**
![SCIMAP Fitted 2 Processing dialog](screenshots/12-fitted-2-calibrate-weights/dialog.png)
*The Processing dialog with the statistics table input and weight search range table.*

## Parameters

| Parameter | Description | Default |
|---|---|---|
| Catchment statistics table (CSV) | Output of [SCIMAP Fitted 1](11-fitted-1-catchment-statistics.md), or an equivalent research-script summary. | — |
| Observation sites (optional) | A point layer that overrides the observation values already in the CSV. | Optional |
| Site ID field | Matches sites between the point layer and the CSV. | Optional |
| Observed value field(s) | One numeric field per determinand. | Optional |
| Statistics column to calibrate against (Advanced) | Which per-class statistic to use as the predictor. | `mean_connectivity_x_erosion` |
| Number of weight sets to sample | Size of the Latin hypercube search. | 5,000 |
| Number of best-fitting sets to keep | How many top weight vectors are written to the "best-fitting" output. | 100 |
| Rank weight sets by | Mean Spearman rank correlation (recommended), mean Pearson correlation, or mean R² (diagnostic only). | Spearman |
| SCIMAP class → weight search range | Min/max search bounds per SCIMAP class. | Default bounds |
| Rescale reported weights so the largest is 1 | Only the *ratios* between class weights are identifiable — multiplying every weight by the same number leaves the objective unchanged — so weights are rescaled for readability. | On |
| SCIMAP class held at a fixed weight (Advanced) | Optionally pin one class (e.g. water) rather than calibrate it. `0` = none. | None |
| Weight for the held class (Advanced) | The fixed value for the pinned class. | — |
| Hold that class during calibration as well as cross-validation (Advanced) | Whether the pin applies to the main search too, or only to cross-validation. | Off |
| Random seed (Advanced) | For reproducible sampling. | 42 |
| Cross-validate the calibration (slow) | See below. | Off |
| Sites held out per fold (k) | Sites excluded from fitting and predicted, per fold. | 1 |
| Maximum folds before sampling them | Caps runtime when the exhaustive fold count is very large. | — |
| Weight sets ranked per batch (Advanced) | Batch size for the internal ranking loop. | — |
| Cross-validation worker threads (Advanced) | Threads used for cross-validation. | Up to 4 |

## Outputs

| Output | Description |
|---|---|
| Calibration metrics (all weight sets) | Every sampled weight set with its scores. |
| Best-fitting weight sets | The top-N sets, with their weights and scores. |
| Fitted parameter set (XML) (optional) | The single best-fitting weight vector, in the same XML format used by [Apply Land Cover Risk Weights](03-apply-land-cover-risk-weights.md) and [SCIMAP Sediment](04-scimap-sediment.md) — load it straight into either. |
| Run summary (optional) | Key settings and headline results for this run. |
| Cross-validation skill (optional) | Out-of-sample correlation, if cross-validation was run. |
| Cross-validation held-out predictions (optional) | Per-site held-out predictions, if cross-validation was run. |
| Diagnostic plots folder (optional) | Weight boxplot, dotty plot, cross-validation boxplot and held-out error plots (needs matplotlib). |

## Cross-validation — the honesty check

With seven land-cover classes and often only a few dozen monitoring sites,
there are only a handful of observations per free parameter, so a high
correlation can easily be an artefact of searching a large space rather than a
real relationship. **Cross-validation** repeatedly refits on all but *k* sites
and predicts those held-out sites; comparing that out-of-sample score against
the full-sample calibration score is the check on whether the fit is real. It
is off by default because its cost grows sharply with *k*, and the tool warns
before committing to a run it estimates will take more than ten minutes.

**Screenshot:**
![Example dotty plot from the diagnostic plots folder](screenshots/12-fitted-2-calibrate-weights/dotty-plot.png)
*The dotty plot: rank score against each class's sampled weight, showing which classes the data actually constrain.*

**Screenshot:**
![Example weight boxplot from the diagnostic plots folder](screenshots/12-fitted-2-calibrate-weights/weight-boxplot.png)
*The weight boxplot for the best-fitting sets.*

## Notes

- Observations of `-9999` are treated as missing. A determinand needs at least
  three valid values to be calibrated against.
- If the observation-sites-per-class ratio is low, the log reports an
  "overfitting risk" rating and recommends enabling cross-validation.

Next: [SCIMAP Fitted 3: Ensemble Risk Maps](13-fitted-3-ensemble-risk-maps.md).
