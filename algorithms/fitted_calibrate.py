"""SCIMAP Fitted 2: Calibrate Weights — the search, and the honesty check on it.

Takes the per-site, per-class statistics table produced by
``scimap:fittedstats`` (or a ``all_catchments_class_means_summary.csv`` from
the SCIMAP-Fitted research scripts) plus observed water-quality values, and
searches land-cover weight space for the vector that ranks the sites in the
same order the observations do.

Cross-validation lives here as an option rather than as a separate algorithm
because its whole purpose is comparison against the full calibration **on the
identical sample**. A separate tool would have to redraw the same Latin
hypercube from the same seed and hope it matched; one fewer land-cover class
present and the comparison is quietly meaningless. One dialog, one sample, one
set of weights feeding both paths makes the comparison exact by construction.
"""

import os

import numpy as np
from qgis.core import (
    QgsProcessing,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterField,
    QgsProcessingParameterFile,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterFolderDestination,
    QgsProcessingParameterMatrix,
    QgsProcessingParameterNumber,
    QgsProcessingParameterString,
    QgsProcessingParameterVectorLayer,
)

from ..core import calibration, fitted_io, landcover, plotting
from ..data import params_xml
from ..data.defaults import (
    FITTED_DEFAULT_BATCH_SIZE,
    FITTED_DEFAULT_CV_K,
    FITTED_DEFAULT_CV_MAX_FOLDS,
    FITTED_DEFAULT_SAMPLES,
    FITTED_DEFAULT_TOP_N,
    FITTED_PINNED_CLASS,
    FITTED_PINNED_WEIGHT,
    SCIMAP_CLASSES,
    default_bounds_matrix,
)
from ..localization import tr
from .base import ScimapCanceled
from .fitted_base import ScimapFittedAlgorithmBase

#: Above this, warn before committing to the run.
_SLOW_RUN_SECONDS = 600


class ScimapFittedCalibrateAlgorithm(ScimapFittedAlgorithmBase):
    """Monte Carlo calibration of land-cover risk weights against observations."""

    INPUT_STATS = 'INPUT_STATS'
    INPUT_SITES = 'INPUT_SITES'
    SITE_ID_FIELD = 'SITE_ID_FIELD'
    OBS_FIELDS = 'OBS_FIELDS'
    VALUE_COLUMN = 'VALUE_COLUMN'

    N_SAMPLES = 'N_SAMPLES'
    TOP_N = 'TOP_N'
    RANK_BY = 'RANK_BY'
    WEIGHT_BOUNDS = 'WEIGHT_BOUNDS'
    NORMALISE_WEIGHTS = 'NORMALISE_WEIGHTS'
    PIN_CLASS = 'PIN_CLASS'
    PIN_WEIGHT = 'PIN_WEIGHT'
    PIN_IN_CALIBRATION = 'PIN_IN_CALIBRATION'
    SEED = 'SEED'

    RUN_CROSS_VALIDATION = 'RUN_CROSS_VALIDATION'
    CV_K = 'CV_K'
    CV_MAX_FOLDS = 'CV_MAX_FOLDS'
    CV_BATCH_SIZE = 'CV_BATCH_SIZE'
    CV_THREADS = 'CV_THREADS'

    OUT_METRICS = 'OUT_METRICS'
    OUT_TOP_SETS = 'OUT_TOP_SETS'
    OUT_PARAM_XML = 'OUT_PARAM_XML'
    OUT_RUN_SUMMARY = 'OUT_RUN_SUMMARY'
    OUT_CV_RESULTS = 'OUT_CV_RESULTS'
    OUT_CV_PREDICTIONS = 'OUT_CV_PREDICTIONS'
    OUT_PLOT_FOLDER = 'OUT_PLOT_FOLDER'

    _RANK_BY_VALUES = ('mean_spearman', 'mean_pearson', 'mean_r2')

    def createInstance(self):
        return ScimapFittedCalibrateAlgorithm()

    def name(self):
        return 'fittedcalibrate'

    def displayName(self):
        return tr('SCIMAP Fitted 2: Calibrate Weights')

    def shortHelpString(self):
        return tr(
            'Infers SCIMAP land-cover risk weights from observed water quality '
            'instead of assuming them.\n\n'
            'For every sampled weight vector the predicted risk at each '
            'monitoring site is compared against the observations by Spearman '
            'rank correlation, and the best-fitting vectors are kept. Pearson '
            'and R2 are reported as diagnostics only: the prediction is an '
            'arbitrary-scale index and the observation is a concentration, so '
            'R2 between them measures the scale mismatch as much as the fit.\n\n'
            'The objective is unchanged by multiplying every weight by the same '
            'number, so only the RATIOS between classes are identifiable. '
            'Weights are rescaled so the largest is 1 before being reported.\n\n'
            'Input is the statistics table from "SCIMAP Fitted 1: Catchment '
            'Statistics". A summary CSV from the SCIMAP-Fitted research scripts '
            'is also accepted.\n\n'
            'Observations of -9999 are treated as missing. A determinand needs '
            'at least three valid values.\n\n'
            'Cross-validation repeatedly refits on all but k sites and predicts '
            'those held out, which is the check on whether a high correlation '
            'is real or an artefact of searching a large space with few '
            'observations. It is off by default because its cost grows sharply '
            'with k.'
        )

    # ── Parameters ──────────────────────────────────────────────────────

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFile(
            self.INPUT_STATS,
            tr('Catchment statistics table (CSV)'),
            behavior=QgsProcessingParameterFile.File,
            extension='csv',
            fileFilter='CSV files (*.csv)',
        ))

        self.addParameter(QgsProcessingParameterVectorLayer(
            self.INPUT_SITES,
            tr('Observation sites (optional; overrides values in the CSV)'),
            types=[QgsProcessing.TypeVectorPoint],
            optional=True,
        ))

        self.addParameter(QgsProcessingParameterField(
            self.SITE_ID_FIELD,
            tr('Site ID field'),
            parentLayerParameterName=self.INPUT_SITES,
            optional=True,
        ))

        self.addParameter(QgsProcessingParameterField(
            self.OBS_FIELDS,
            tr('Observed value field(s) — one per determinand'),
            parentLayerParameterName=self.INPUT_SITES,
            type=QgsProcessingParameterField.Numeric,
            allowMultiple=True,
            optional=True,
        ))

        self.add_advanced_parameter(QgsProcessingParameterString(
            self.VALUE_COLUMN,
            tr('Statistics column to calibrate against'),
            defaultValue='mean_connectivity_x_erosion',
        ))

        self.addParameter(QgsProcessingParameterNumber(
            self.N_SAMPLES,
            tr('Number of weight sets to sample'),
            type=QgsProcessingParameterNumber.Integer,
            defaultValue=FITTED_DEFAULT_SAMPLES,
            minValue=100,
            maxValue=5000000,
        ))

        self.addParameter(QgsProcessingParameterNumber(
            self.TOP_N,
            tr('Number of best-fitting sets to keep'),
            type=QgsProcessingParameterNumber.Integer,
            defaultValue=FITTED_DEFAULT_TOP_N,
            minValue=1,
        ))

        self.addParameter(QgsProcessingParameterEnum(
            self.RANK_BY,
            tr('Rank weight sets by'),
            options=[
                tr('Mean Spearman rank correlation (recommended)'),
                tr('Mean Pearson correlation'),
                tr('Mean R2 (diagnostic only)'),
            ],
            defaultValue=0,
        ))

        self.addParameter(QgsProcessingParameterMatrix(
            self.WEIGHT_BOUNDS,
            tr('SCIMAP class -> weight search range'),
            headers=[tr('SCIMAP class'), tr('Min weight'), tr('Max weight')],
            numberRows=len(SCIMAP_CLASSES),
            defaultValue=default_bounds_matrix(),
            optional=True,
        ))

        self.addParameter(QgsProcessingParameterBoolean(
            self.NORMALISE_WEIGHTS,
            tr('Rescale reported weights so the largest is 1'),
            defaultValue=True,
        ))

        self.add_advanced_parameter(QgsProcessingParameterNumber(
            self.PIN_CLASS,
            tr('SCIMAP class held at a fixed weight (0 = none)'),
            type=QgsProcessingParameterNumber.Integer,
            defaultValue=FITTED_PINNED_CLASS,
            minValue=0,
        ))
        self.add_advanced_parameter(QgsProcessingParameterNumber(
            self.PIN_WEIGHT,
            tr('Weight for the held class'),
            type=QgsProcessingParameterNumber.Double,
            defaultValue=FITTED_PINNED_WEIGHT,
        ))
        self.add_advanced_parameter(QgsProcessingParameterBoolean(
            self.PIN_IN_CALIBRATION,
            tr('Hold that class during calibration as well as cross-validation'),
            defaultValue=False,
        ))
        self.add_advanced_parameter(QgsProcessingParameterNumber(
            self.SEED,
            tr('Random seed'),
            type=QgsProcessingParameterNumber.Integer,
            defaultValue=42,
        ))

        self.addParameter(QgsProcessingParameterBoolean(
            self.RUN_CROSS_VALIDATION,
            tr('Cross-validate the calibration (slow)'),
            defaultValue=False,
        ))
        self.addParameter(QgsProcessingParameterNumber(
            self.CV_K,
            tr('Sites held out per fold (k)'),
            type=QgsProcessingParameterNumber.Integer,
            defaultValue=FITTED_DEFAULT_CV_K,
            minValue=1,
            maxValue=5,
        ))
        self.addParameter(QgsProcessingParameterNumber(
            self.CV_MAX_FOLDS,
            tr('Maximum folds before sampling them'),
            type=QgsProcessingParameterNumber.Integer,
            defaultValue=FITTED_DEFAULT_CV_MAX_FOLDS,
            minValue=1,
        ))
        self.add_advanced_parameter(QgsProcessingParameterNumber(
            self.CV_BATCH_SIZE,
            tr('Weight sets ranked per batch'),
            type=QgsProcessingParameterNumber.Integer,
            defaultValue=FITTED_DEFAULT_BATCH_SIZE,
            minValue=1000,
        ))
        self.add_advanced_parameter(QgsProcessingParameterNumber(
            self.CV_THREADS,
            tr('Cross-validation worker threads'),
            type=QgsProcessingParameterNumber.Integer,
            defaultValue=min(4, os.cpu_count() or 1),
            minValue=1,
            maxValue=16,
        ))

        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUT_METRICS,
            tr('Calibration metrics (all weight sets)'),
            fileFilter='CSV files (*.csv)',
        ))
        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUT_TOP_SETS,
            tr('Best-fitting weight sets'),
            fileFilter='CSV files (*.csv)',
        ))
        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUT_PARAM_XML,
            tr('Fitted parameter set (XML)'),
            fileFilter='XML files (*.xml)',
            optional=True,
            createByDefault=True,
        ))
        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUT_RUN_SUMMARY,
            tr('Run summary'),
            fileFilter='CSV files (*.csv)',
            optional=True,
            createByDefault=False,
        ))
        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUT_CV_RESULTS,
            tr('Cross-validation skill'),
            fileFilter='CSV files (*.csv)',
            optional=True,
            createByDefault=False,
        ))
        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUT_CV_PREDICTIONS,
            tr('Cross-validation held-out predictions'),
            fileFilter='CSV files (*.csv)',
            optional=True,
            createByDefault=False,
        ))
        self.addParameter(QgsProcessingParameterFolderDestination(
            self.OUT_PLOT_FOLDER,
            tr('Diagnostic plots folder'),
            optional=True,
            createByDefault=False,
        ))

    # ── Execution ───────────────────────────────────────────────────────

    def processAlgorithm(self, parameters, context, feedback):
        try:
            return self._run(parameters, context, feedback)
        except ScimapCanceled:
            feedback.pushInfo("SCIMAP Fitted calibration cancelled.")
            return {}

    def _run(self, parameters, context, feedback):
        stats_path = self.parameterAsFile(parameters, self.INPUT_STATS, context)
        value_column = (self.parameterAsString(parameters, self.VALUE_COLUMN, context)
                        or 'mean_connectivity_x_erosion').strip()

        feedback.setProgress(2)
        feedback.pushInfo(f"1. Reading catchment statistics from {os.path.basename(stats_path)}...")
        rows, csv_obs_columns = fitted_io.read_stats_csv(stats_path, value_column)
        design = calibration.build_design_matrix(rows)
        feedback.pushInfo(
            f"   {design.n_sites} observation sites x {design.n_classes} land-cover classes."
        )

        observations = self._resolve_observations(
            parameters, context, feedback, design, rows, csv_obs_columns)

        ratio, risk = calibration.overfitting_risk(design.n_sites, design.n_classes)
        message = (
            f"   {ratio:.1f} observation sites per land-cover class "
            f"(overfitting risk: {risk})."
        )
        if risk == 'HIGH':
            feedback.pushWarning(
                message + " A high correlation at this ratio can easily be an "
                "artefact of the search; enable cross-validation to test it."
            )
        else:
            feedback.pushInfo(message)

        # ── Sample ──────────────────────────────────────────────────────
        n_samples = self.parameterAsInt(parameters, self.N_SAMPLES, context)
        top_n = self.parameterAsInt(parameters, self.TOP_N, context)
        seed = self.parameterAsInt(parameters, self.SEED, context)
        rank_by = self._RANK_BY_VALUES[
            self.parameterAsEnum(parameters, self.RANK_BY, context)]
        batch_size = self.parameterAsInt(parameters, self.CV_BATCH_SIZE, context)

        pin_class = self.parameterAsInt(parameters, self.PIN_CLASS, context) or None
        pin_weight = self.parameterAsDouble(parameters, self.PIN_WEIGHT, context)
        pin_in_calibration = self.parameterAsBool(
            parameters, self.PIN_IN_CALIBRATION, context)

        bounds = landcover.matrix_to_bounds(
            self.parameterAsMatrix(parameters, self.WEIGHT_BOUNDS, context))

        self.check_canceled(feedback)
        feedback.setProgress(8)
        feedback.pushInfo(
            f"2. Sampling {n_samples:,} weight sets by Latin hypercube (seed {seed})...")
        weights, adjustable = calibration.sample_weights(
            n_samples, design.classes, bounds=bounds, seed=seed,
            pinned_class=pin_class if pin_in_calibration else None,
            pinned_weight=pin_weight,
        )
        if pin_class and pin_in_calibration:
            feedback.pushInfo(
                f"   SCIMAP class {pin_class} held at {pin_weight:g}; "
                f"{len(adjustable)} classes calibrated.")

        # ── Calibrate ───────────────────────────────────────────────────
        self.check_canceled(feedback)
        feedback.setProgress(12)
        feedback.pushInfo("3. Scoring weight sets against the observations...")
        result = calibration.evaluate_parameter_sets(
            design.base, weights, observations,
            batch_size=batch_size, rank_by=rank_by,
            progress=self._progress(feedback, 12, 35),
        )
        best = result.order[0]
        feedback.pushInfo(
            f"   Best set: {rank_by} = {result.rank_score[best]:.4f} "
            f"over {len(result.obs_columns)} determinand(s)."
        )

        # ── Cross-validate ──────────────────────────────────────────────
        cv_result, cv_scores = None, None
        if self.parameterAsBool(parameters, self.RUN_CROSS_VALIDATION, context):
            cv_result, cv_scores = self._cross_validate(
                parameters, context, feedback, design, weights, observations,
                bounds, seed, pin_class, pin_weight, batch_size,
            )

        # ── Outputs ─────────────────────────────────────────────────────
        self.check_canceled(feedback)
        feedback.setProgress(90)
        feedback.pushInfo("5. Writing results...")

        reported_weights = (
            calibration.normalise_weight_rows(weights)
            if self.parameterAsBool(parameters, self.NORMALISE_WEIGHTS, context)
            else weights
        )

        results = {}
        metrics_path = self.parameterAsFileOutput(parameters, self.OUT_METRICS, context)
        fitted_io.write_metrics_csv(
            metrics_path, result, design.classes, reported_weights)
        results[self.OUT_METRICS] = metrics_path

        top_path = self.parameterAsFileOutput(parameters, self.OUT_TOP_SETS, context)
        fitted_io.write_top_sets_csv(
            top_path, result, design.classes, top_n, reported_weights)
        results[self.OUT_TOP_SETS] = top_path

        xml_path = self.parameterAsFileOutput(parameters, self.OUT_PARAM_XML, context)
        if xml_path:
            params_xml.write_weights(
                xml_path,
                fitted_io.best_weights_dict(reported_weights[best], design.classes),
                name=f"SCIMAP Fitted ({rank_by} {result.rank_score[best]:.3f})",
            )
            results[self.OUT_PARAM_XML] = xml_path
            feedback.pushInfo(
                "   Fitted parameter set written; it can be loaded straight into "
                "'Apply Land Cover Risk Weights' or 'Run SCIMAP Sediment'."
            )

        summary_path = self.parameterAsFileOutput(
            parameters, self.OUT_RUN_SUMMARY, context)
        if summary_path:
            fitted_io.write_run_summary_csv(summary_path, self._summary(
                stats_path, value_column, design, result, n_samples, top_n,
                rank_by, seed, ratio, risk, cv_result, pin_class,
                pin_weight, pin_in_calibration,
            ))
            results[self.OUT_RUN_SUMMARY] = summary_path

        if cv_result is not None:
            cv_results_path = self.parameterAsFileOutput(
                parameters, self.OUT_CV_RESULTS, context)
            if cv_results_path:
                fitted_io.write_cv_results_csv(cv_results_path, cv_result, cv_scores)
                results[self.OUT_CV_RESULTS] = cv_results_path
            cv_predictions_path = self.parameterAsFileOutput(
                parameters, self.OUT_CV_PREDICTIONS, context)
            if cv_predictions_path:
                fitted_io.write_cv_predictions_csv(
                    cv_predictions_path, cv_result, design.site_ids)
                results[self.OUT_CV_PREDICTIONS] = cv_predictions_path

        plot_folder = self.parameterAsString(parameters, self.OUT_PLOT_FOLDER, context)
        if plot_folder:
            written = self._write_plots(
                plot_folder, result, reported_weights, design, top_n,
                cv_result, feedback)
            if written:
                results[self.OUT_PLOT_FOLDER] = plot_folder

        feedback.setProgress(100)
        return results

    # ── Helpers ─────────────────────────────────────────────────────────

    def _progress(self, feedback, start, end):
        """A progress callable spanning [start, end] on the overall bar."""
        def report(fraction, message=None):
            self.check_canceled(feedback)
            feedback.setProgress(start + (end - start) * max(0.0, min(1.0, fraction)))
            if message:
                feedback.pushInfo(f"   {message}")
        return report

    def _resolve_observations(self, parameters, context, feedback, design,
                              rows, csv_obs_columns):
        """Observation values per site, from the point layer or the CSV."""
        points_layer = self.parameterAsVectorLayer(
            parameters, self.INPUT_SITES, context)
        obs_fields = self.parameterAsFields(parameters, self.OBS_FIELDS, context)

        if points_layer is not None and obs_fields:
            id_field = (self.parameterAsFields(
                parameters, self.SITE_ID_FIELD, context) or [None])[0]
            sites = self.read_observation_sites(
                points_layer, points_layer.crs(), context,
                id_field=id_field, value_fields=obs_fields, feedback=feedback)

            by_id = {}
            for site in sites:
                by_id[fitted_io.extract_site_id(site['site_id'])] = site['values']

            observations = {}
            for field in obs_fields:
                observations[field] = np.array(
                    [by_id.get(site, {}).get(field, np.nan)
                     for site in design.site_ids],
                    dtype=np.float64,
                )
            matched = sum(1 for site in design.site_ids if site in by_id)
            feedback.pushInfo(
                f"   Observations from the point layer: {len(obs_fields)} "
                f"determinand(s), {matched}/{design.n_sites} sites matched by ID."
            )
            if matched == 0:
                raise RuntimeError(
                    "No observation site in the point layer matches a site in the "
                    "statistics table. Check that the Site ID field holds the same "
                    "identifiers the statistics table was written with."
                )
            return observations

        if not csv_obs_columns:
            raise RuntimeError(
                "The statistics table carries no observation columns, and no "
                "observation point layer and value field(s) were supplied. One or "
                "the other is required — there is nothing to calibrate against."
            )

        feedback.pushInfo(
            f"   Observations from the statistics table: "
            f"{', '.join(csv_obs_columns)}.")
        return fitted_io.observations_from_rows(
            rows, csv_obs_columns, design.site_ids)

    def _cross_validate(self, parameters, context, feedback, design, weights,
                        observations, bounds, seed, pin_class, pin_weight,
                        batch_size):
        import math
        import time

        k_out = self.parameterAsInt(parameters, self.CV_K, context)
        max_folds = self.parameterAsInt(parameters, self.CV_MAX_FOLDS, context)
        threads = self.parameterAsInt(parameters, self.CV_THREADS, context)

        if k_out >= design.n_sites:
            raise RuntimeError(
                f"Cannot hold out {k_out} of {design.n_sites} sites; k must be "
                "smaller than the number of sites."
            )

        folds, exhaustive, sampled = calibration.leave_k_out_folds(
            design.n_sites, k_out, max_folds=max_folds, seed=seed)

        # The research scripts pin the water/other class during
        # cross-validation only. Respect that, but say so rather than letting
        # calibration and validation quietly search different spaces.
        cv_weights = weights
        if pin_class and pin_class in design.classes:
            column = design.classes.index(pin_class)
            if not np.allclose(weights[:, column], pin_weight):
                cv_weights = weights.copy()
                cv_weights[:, column] = pin_weight
                feedback.pushInfo(
                    f"   SCIMAP class {pin_class} held at {pin_weight:g} for "
                    "cross-validation only, matching the research scripts."
                )

        self.check_canceled(feedback)
        feedback.setProgress(36)
        feedback.pushInfo(
            f"4. Leave-{k_out}-out cross-validation: {len(folds):,} folds "
            f"x {len(observations)} determinand(s)."
        )
        if sampled:
            feedback.pushWarning(
                f"   Exhaustive cross-validation would need {exhaustive:,} folds; "
                f"{len(folds):,} were drawn at random instead (seed {seed}). This "
                "is bounded Monte-Carlo cross-validation — sound, and reproducible, "
                "but not the full enumeration."
            )

        # Time one fold before committing, so a run that will take hours says so
        # up front rather than appearing to hang behind a modal dialog.
        started = time.time()
        calibration.cross_validate(
            design.base, cv_weights, observations, folds[:1],
            batch_size=batch_size, threads=1)
        projected = (time.time() - started) * len(folds) / max(1, threads)
        feedback.pushInfo(f"   Projected cross-validation time: ~{projected / 60:.1f} min.")
        if projected > _SLOW_RUN_SECONDS:
            feedback.pushWarning(
                "   That is a long run. Reduce k, the fold cap, or the number of "
                "weight sets to shorten it."
            )

        cv_result = calibration.cross_validate(
            design.base, cv_weights, observations, folds,
            batch_size=batch_size, threads=threads,
            progress=self._progress(feedback, 38, 88),
        )
        cv_result.n_folds_exhaustive = exhaustive
        cv_result.sampled = sampled

        scores = calibration.out_of_sample_scores(cv_result)
        for name, entry in scores.items():
            feedback.pushInfo(
                f"   {name}: out-of-sample Spearman "
                f"{entry['spearman']:.4f} (p = {entry['spearman_p']:.3g}, "
                f"n = {entry['n_valid']})."
            )
        feedback.pushInfo(
            "   Compare that against the calibration score above. Much lower "
            "means the calibrated correlation is largely an artefact of the search."
        )
        return cv_result, scores

    def _summary(self, stats_path, value_column, design, result, n_samples,
                 top_n, rank_by, seed, ratio, risk, cv_result, pin_class,
                 pin_weight, pin_in_calibration):
        summary = {
            'statistics_table': os.path.basename(stats_path),
            'value_column': value_column,
            'n_sites': design.n_sites,
            'n_land_cover_classes': design.n_classes,
            'land_cover_classes': ' '.join(str(c) for c in design.classes),
            'observation_columns': ' '.join(result.obs_columns),
            'n_samples': n_samples,
            'top_n': top_n,
            'rank_by': rank_by,
            'seed': seed,
            'pinned_class': pin_class or '',
            'pinned_weight': pin_weight if pin_class else '',
            'pinned_during_calibration': int(bool(pin_class and pin_in_calibration)),
            'sites_per_class': f"{ratio:.2f}",
            'overfitting_risk': risk,
            'best_rank_score': f"{result.rank_score[result.order[0]]:.6f}",
        }
        for name in result.obs_columns:
            summary[f'n_observations_{name}'] = result.n_obs_by_column[name]
        if cv_result is not None:
            summary.update({
                'cv_k_out': cv_result.k_out,
                'cv_folds_run': cv_result.fold_count,
                'cv_folds_exhaustive': cv_result.n_folds_exhaustive,
                'cv_folds_sampled': int(bool(cv_result.sampled)),
            })
        return summary

    @staticmethod
    def _significance_lines(result):
        """Correlations a fit has to beat to be significant, for the dotty plot.

        The y axis is a correlation, so a horizontal line at the critical value
        says directly which weight sets are distinguishable from no
        relationship at all — worth knowing before reading a ridge as a signal.

        Where several determinands were calibrated against, the smallest count
        is used, which gives the highest (most demanding) threshold; the y axis
        is then a mean over determinands, so the line is a guide rather than an
        exact test, and the label says how many observations it assumes.
        """
        counts = [result.n_obs_by_column[name] for name in result.obs_columns]
        if not counts:
            return ()
        n_obs = min(counts)
        return [
            (calibration.critical_correlation(n_obs, 0.10), f'90% (n={n_obs})'),
            (calibration.critical_correlation(n_obs, 0.04), f'96% (n={n_obs})'),
        ]

    def _write_plots(self, folder, result, reported_weights, design, top_n,
                     cv_result, feedback):
        """Diagnostic PNGs. matplotlib is optional in a QGIS install."""
        try:
            import matplotlib  # noqa: F401
        except ImportError:
            feedback.pushWarning(
                "matplotlib is not available in this QGIS Python environment, so "
                "the diagnostic plots were skipped. Every number behind them is "
                "in the CSV outputs."
            )
            return []

        os.makedirs(folder, exist_ok=True)
        labels = self.scimap_class_labels(design.classes)
        written = []

        top_indices = result.top_indices(top_n)
        written.append(plotting.save_weight_boxplot(
            reported_weights[top_indices], labels,
            os.path.join(folder, 'fitted_weights_boxplot.png'),
            subtitle=(f"Best {len(top_indices)} of {result.n_sets:,} sampled sets"),
        ))
        written.append(plotting.save_dotty_plot(
            reported_weights, result.rank_score, labels,
            os.path.join(folder, 'dotty_plot.png'),
            ylabel=result.rank_by.replace('mean_', 'Mean ').replace('_', ' '),
            significance_lines=self._significance_lines(result),
        ))

        if cv_result is not None:
            n_train = design.n_sites - cv_result.k_out
            written.append(plotting.save_spearman_boxplot(
                {
                    name: values[calibration.top_n_indices(values, top_n)]
                    for name, values in cv_result.mean_spearman_by_column.items()
                },
                os.path.join(folder, 'cross_validation_boxplot.png'),
                significance_lines=[
                    (calibration.critical_correlation(n_train, 0.05), 'p = 0.05'),
                    (calibration.critical_correlation(n_train, 0.01), 'p = 0.01'),
                ],
            ))
            for name in cv_result.obs_columns:
                predicted = cv_result.predictions[name]
                observed = cv_result.observed[name]
                valid = np.isfinite(predicted) & np.isfinite(observed)
                if valid.sum() < 2:
                    continue
                errors = np.full(design.n_sites, np.nan)
                errors[valid] = np.abs(
                    calibration._standardise(predicted[valid])
                    - calibration._standardise(observed[valid]))
                written.append(plotting.save_error_bar_plot(
                    [str(site) for site in design.site_ids], np.nan_to_num(errors),
                    os.path.join(
                        folder, f'held_out_error_{_slug(name)}.png'),
                    title=f'Held-out prediction error by site — {name}',
                ))

        feedback.pushInfo(f"   {len(written)} diagnostic plot(s) written to {folder}.")
        return written


def _slug(text):
    return ''.join(c if c.isalnum() else '_' for c in str(text)).strip('_').lower()
