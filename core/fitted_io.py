"""CSV interchange for the SCIMAP-Fitted tools.

Deliberately built on the standard library's ``csv`` module rather than
pandas. The research scripts use ``read_csv`` and ``pivot_table`` here, but
everything involved is a few hundred rows — one per (site, land-cover class) —
so pandas buys nothing, is not a declared dependency of either the QGIS plugin
or the web application, and is a heavyweight import on the GUI thread. It is
also the place where a class missing from one catchment silently becomes a
zero column via ``fill_value``; doing the pivot explicitly in
``calibration.build_design_matrix`` keeps that visible.
"""

import csv
import os
import re

import numpy as np

#: Columns every catchment-statistics table carries.
STATS_FIELDS = (
    'site_id',
    'land_cover_class',
    'pixel_count',
    'area_m2',
    'mean_connectivity_x_erosion',
    'rainfall_dilution',
)

#: Observation columns are written with this prefix so they can be picked out
#: again without being told which determinands were used.
OBS_PREFIX = 'obs_'

#: Per-class statistics are written as ``connectivity_x_erosion_<stat>``,
#: the same names the research scripts use.
STAT_PREFIX = 'connectivity_x_erosion_'

WEIGHT_PREFIX = 'weight_class_'

_TRAILING_NUMBER = re.compile(r'(\d+)$')


def _to_float(value):
    if value is None or value == '':
        return float('nan')
    try:
        return float(value)
    except (TypeError, ValueError):
        return float('nan')


def extract_site_id(value):
    """Parse a site identifier from a ``catchment_12``-style label.

    The research scripts carry the site identifier in the *filename* of each
    catchment shapefile and recover it by splitting on an underscore. The
    plugin carries a real ``site_id`` field instead, but reading a Step 2
    ``all_catchments_class_means_summary.csv`` still has to cope with the old
    form — which is what lets a calibration run be validated against results
    the scripts already produced.
    """
    text = str(value).strip()
    match = _TRAILING_NUMBER.search(text)
    if match and ('_' in text or text.isdigit()):
        return int(match.group(1))
    return text


# ── Catchment statistics ────────────────────────────────────────────────

def write_stats_csv(path, rows, obs_columns=()):
    """Write the long-format per-site, per-class statistics table.

    Any ``connectivity_x_erosion_<stat>`` columns the rows carry are written
    alongside the standard fields, so a table summarised with the median (say)
    can be fed to the calibration through its statistics-column parameter.
    """
    obs_columns = list(obs_columns)

    statistic_fields = sorted({
        key for row in rows for key in row if key.startswith(STAT_PREFIX)
    })

    header = (list(STATS_FIELDS) + statistic_fields
              + [f"{OBS_PREFIX}{name}" for name in obs_columns])

    with open(path, 'w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=header, extrasaction='ignore')
        writer.writeheader()
        for row in rows:
            record = {field: row.get(field, '') for field in STATS_FIELDS}
            for field in statistic_fields:
                record[field] = row.get(field, '')
            for name in obs_columns:
                key = f"{OBS_PREFIX}{name}"
                record[key] = row.get(key, row.get(name, ''))
            writer.writerow(record)
    return path


def read_stats_csv(path, value_column=None):
    """Read a statistics table written here, or a Step 2 summary from the scripts.

    Returns ``(rows, obs_columns)``. The scripts name the site column
    ``catchment`` and hold values like ``catchment_12``; both that and a plain
    ``site_id`` are accepted.

    *value_column* selects which column carries the per-class risk value, for
    tables written with a statistic other than the mean (the research scripts
    can emit median/min/max/std/sum as
    ``connectivity_x_erosion_<stat>``). It is read into
    ``mean_connectivity_x_erosion`` so everything downstream has one key.
    """
    with open(path, newline='', encoding='utf-8-sig') as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        if not fieldnames:
            raise ValueError(f"{os.path.basename(path)} has no header row.")

        site_field = (
            'site_id' if 'site_id' in fieldnames
            else 'catchment' if 'catchment' in fieldnames
            else None
        )
        if site_field is None:
            raise ValueError(
                f"{os.path.basename(path)} has neither a 'site_id' nor a "
                "'catchment' column, so its rows cannot be matched to "
                "observation sites."
            )
        if 'land_cover_class' not in fieldnames:
            raise ValueError(
                f"{os.path.basename(path)} has no 'land_cover_class' column.")

        value_field = value_column or 'mean_connectivity_x_erosion'
        if value_field not in fieldnames:
            raise ValueError(
                f"{os.path.basename(path)} has no '{value_field}' column. "
                f"Available columns: {', '.join(fieldnames)}"
            )

        obs_columns = [
            name[len(OBS_PREFIX):] for name in fieldnames
            if name.startswith(OBS_PREFIX)
        ]

        rows = []
        for record in reader:
            if not record.get(site_field):
                continue
            row = {
                'site_id': extract_site_id(record[site_field]),
                'land_cover_class': int(float(record['land_cover_class'])),
                'pixel_count': _to_float(record.get('pixel_count')),
                'area_m2': _to_float(record.get('area_m2')),
                'mean_connectivity_x_erosion': _to_float(record.get(value_field)),
                'rainfall_dilution': _to_float(record.get('rainfall_dilution')),
            }
            for name in obs_columns:
                row[f"{OBS_PREFIX}{name}"] = _to_float(
                    record.get(f"{OBS_PREFIX}{name}"))
            rows.append(row)

    if not rows:
        raise ValueError(f"{os.path.basename(path)} contains no data rows.")
    return rows, obs_columns


def observations_from_rows(rows, obs_columns, site_ids):
    """Collect per-site observation values, aligned to *site_ids*.

    The statistics table has one row per (site, class), so each observation
    value is repeated across a site's rows; the first finite value wins.
    """
    observations = {}
    order = {site: index for index, site in enumerate(site_ids)}
    for name in obs_columns:
        values = np.full(len(site_ids), np.nan, dtype=np.float64)
        for row in rows:
            index = order.get(row['site_id'])
            if index is None or np.isfinite(values[index]):
                continue
            value = row.get(f"{OBS_PREFIX}{name}", np.nan)
            if np.isfinite(value):
                values[index] = value
        observations[name] = values
    return observations


# ── Weight sets ─────────────────────────────────────────────────────────

def read_weight_sets(path, n_sets=0):
    """Read calibrated weight sets from any CSV carrying ``weight_class_*``.

    Accepts this package's ``all_parameter_sets_metrics.csv`` and the research
    scripts' ``top_N_parameter_sets_*.csv`` alike. Rows are taken in file
    order, which both writers sort best-first; *n_sets* of 0 means all of them.

    Returns ``(weights, classes)``.
    """
    with open(path, newline='', encoding='utf-8-sig') as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        weight_fields = [f for f in fieldnames if f.startswith(WEIGHT_PREFIX)]
        if not weight_fields:
            raise ValueError(
                f"{os.path.basename(path)} has no '{WEIGHT_PREFIX}<n>' columns, "
                "so it does not hold calibrated weight sets."
            )
        # Numeric, not lexical: lexical would order weight_class_10 before
        # weight_class_2 and silently transpose two land-cover classes.
        weight_fields.sort(key=lambda name: int(name[len(WEIGHT_PREFIX):]))
        classes = [int(name[len(WEIGHT_PREFIX):]) for name in weight_fields]

        rows = []
        for record in reader:
            rows.append([_to_float(record.get(name)) for name in weight_fields])
            if n_sets and len(rows) >= n_sets:
                break

    if not rows:
        raise ValueError(f"{os.path.basename(path)} contains no weight sets.")
    return np.asarray(rows, dtype=np.float64), classes


def best_weights_dict(weights_row, classes):
    """``{class_id: weight}`` for ``data/params_xml.py::write_weights``."""
    return {int(c): float(w) for c, w in zip(classes, np.asarray(weights_row))}


# ── Calibration outputs ─────────────────────────────────────────────────

def _metric_fields(obs_columns):
    fields = []
    for name in obs_columns:
        fields.extend([
            f"spearman_{name}", f"spearman_p_{name}",
            f"spearman_sig_5pct_{name}", f"spearman_sig_1pct_{name}",
            f"pearson_{name}", f"pearson_p_{name}",
            f"pearson_sig_5pct_{name}", f"pearson_sig_1pct_{name}",
            f"r2_{name}",
        ])
    return fields


def _metric_values(result, name, index):
    spearman_p = float(result.spearman_p[name][index])
    pearson_p = float(result.pearson_p[name][index])
    return {
        f"spearman_{name}": float(result.spearman[name][index]),
        f"spearman_p_{name}": spearman_p,
        f"spearman_sig_5pct_{name}": int(spearman_p < 0.05),
        f"spearman_sig_1pct_{name}": int(spearman_p < 0.01),
        f"pearson_{name}": float(result.pearson[name][index]),
        f"pearson_p_{name}": pearson_p,
        f"pearson_sig_5pct_{name}": int(pearson_p < 0.05),
        f"pearson_sig_1pct_{name}": int(pearson_p < 0.01),
        f"r2_{name}": float(result.r2[name][index]),
    }


def _weight_fields(classes):
    return [f"{WEIGHT_PREFIX}{c}" for c in classes]


def _write_parameter_rows(path, result, classes, weights, indices):
    header = (
        ['param_set_index', 'rank_score', 'mean_spearman', 'mean_pearson', 'mean_r2']
        + _metric_fields(result.obs_columns)
        + _weight_fields(classes)
    )
    with open(path, 'w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        for index in indices:
            row = {
                'param_set_index': int(index),
                'rank_score': float(result.rank_score[index]),
                'mean_spearman': float(result.mean_spearman[index]),
                'mean_pearson': float(result.mean_pearson[index]),
                'mean_r2': float(result.mean_r2[index]),
            }
            for name in result.obs_columns:
                row.update(_metric_values(result, name, index))
            for field, value in zip(_weight_fields(classes), weights[index]):
                row[field] = float(value)
            writer.writerow(row)
    return path


def write_metrics_csv(path, result, classes, weights=None):
    """Every sampled weight set, best-first by the chosen ranking metric."""
    weights = result.weights if weights is None else weights
    return _write_parameter_rows(path, result, classes, weights, result.order)


def write_top_sets_csv(path, result, classes, top_n, weights=None):
    """The best *top_n* sets for each determinand, in one file.

    The scripts write one file per determinand; a Processing algorithm cannot
    declare a variable number of outputs, so they are merged and distinguished
    by an ``observation_target`` column.
    """
    weights = result.weights if weights is None else weights
    header = (
        ['observation_target', 'rank', 'param_set_index', 'spearman', 'spearman_p']
        + _weight_fields(classes)
    )
    with open(path, 'w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        for name in result.obs_columns:
            for rank, index in enumerate(result.top_indices(top_n, column=name), 1):
                row = {
                    'observation_target': name,
                    'rank': rank,
                    'param_set_index': int(index),
                    'spearman': float(result.spearman[name][index]),
                    'spearman_p': float(result.spearman_p[name][index]),
                }
                for field, value in zip(_weight_fields(classes), weights[index]):
                    row[field] = float(value)
                writer.writerow(row)
    return path


def write_run_summary_csv(path, summary):
    """One row of provenance: what was searched, over what, and how riskily."""
    with open(path, 'w', newline='', encoding='utf-8') as handle:
        writer = csv.writer(handle)
        writer.writerow(['setting', 'value'])
        for key, value in summary.items():
            writer.writerow([key, value])
    return path


# ── Cross-validation outputs ────────────────────────────────────────────

def write_cv_results_csv(path, cv_result, scores):
    """Out-of-sample skill, one row per determinand.

    *scores* maps a determinand to its ``{'spearman', 'spearman_p', 'pearson',
    'pearson_p', 'r2', 'mae', 'n_valid'}`` computed from the pooled held-out
    predictions.
    """
    header = [
        'observation_target', 'k_out', 'n_folds', 'n_folds_exhaustive', 'folds_sampled',
        'lkocv_spearman', 'lkocv_spearman_p', 'lkocv_pearson', 'lkocv_pearson_p',
        'lkocv_r2_standardised', 'lkocv_mae_standardised', 'n_valid',
    ]
    with open(path, 'w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        for name in cv_result.obs_columns:
            entry = scores.get(name, {})
            writer.writerow({
                'observation_target': name,
                'k_out': cv_result.k_out,
                'n_folds': cv_result.fold_count,
                'n_folds_exhaustive': cv_result.n_folds_exhaustive,
                'folds_sampled': int(bool(cv_result.sampled)),
                'lkocv_spearman': entry.get('spearman'),
                'lkocv_spearman_p': entry.get('spearman_p'),
                'lkocv_pearson': entry.get('pearson'),
                'lkocv_pearson_p': entry.get('pearson_p'),
                'lkocv_r2_standardised': entry.get('r2'),
                'lkocv_mae_standardised': entry.get('mae'),
                'n_valid': entry.get('n_valid'),
            })
    return path


def write_cv_predictions_csv(path, cv_result, site_ids):
    """Per-site held-out predictions beside the observation they are tested on."""
    header = ['site_id']
    for name in cv_result.obs_columns:
        header.extend([f"observed_{name}", f"predicted_{name}", f"n_folds_{name}"])

    with open(path, 'w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        for index, site in enumerate(site_ids):
            row = {'site_id': site}
            for name in cv_result.obs_columns:
                row[f"observed_{name}"] = float(cv_result.observed[name][index])
                row[f"predicted_{name}"] = float(cv_result.predictions[name][index])
                row[f"n_folds_{name}"] = int(cv_result.prediction_counts[name][index])
            writer.writerow(row)
    return path
