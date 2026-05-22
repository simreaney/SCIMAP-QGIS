# SCIMAP QGIS Plugin Distribution Guide

This guide explains how to publish the plugin in `qgis_plugin/` to the official QGIS Plugin Repository.

## 1. Prerequisites

- QGIS account on the plugin site: https://plugins.qgis.org
- Maintainer access to the plugin entry (owner or approved collaborator)
- A clean plugin folder containing at least:
  - `__init__.py`
  - `metadata.txt`
  - provider/algorithm Python files
  - `icon.svg`

## 2. Metadata Checklist

Before packaging, open `qgis_plugin/metadata.txt` and confirm:

- `name` is final and stable
- `version` is incremented for each release
- `qgisMinimumVersion` and `qgisMaximumVersion` reflect supported versions
- `description` is short and clear
- `about` is detailed and accurate
- `author` and `email` are correct
- `tracker` is set to your issue tracker URL
- `repository` is set to your code repository URL
- `category=Processing` (correct for this plugin)

Recommended additions if not already present:

- `homepage=<project website>`
- `tags=scimap,hydrology,erosion,diffuse pollution,risk`
- `icon=icon.svg`

Important for this plugin: confirm dependency notes are accurate. If you call `whitebox_tools` directly (native binary), do not claim a hard dependency on the separate Whitebox QGIS plugin unless you actually require it.

## 3. Validate Plugin Locally

1. Copy or symlink the `qgis_plugin` folder into your local QGIS profile plugin directory.
2. Start QGIS with a clean profile.
3. Enable the plugin in Plugin Manager.
4. Confirm:
   - provider appears in Processing Toolbox
   - toolbar/menu button works
   - algorithm runs end-to-end on a small test dataset
   - output rasters and vector layer load correctly

Optional validation helpers:

- Plugin validator: https://plugins.qgis.org/plugins/validator/
- Plugin CI tool: https://github.com/opengisch/qgis-plugin-ci

## 4. Package the Plugin ZIP

The ZIP must contain one top-level folder that is the plugin folder.

From project root:

```bash
cd /Users/simreaney/Documents/GitHub/scimap-app
zip -r scimap_sediment_1.0.zip qgis_plugin \
  -x "*.DS_Store" "*/__pycache__/*" "*.pyc" ".git/*"
```

Check the archive structure:

- top-level folder should be `qgis_plugin/`
- `qgis_plugin/metadata.txt` must exist in the ZIP

## 5. Create or Update the Repository Entry

1. Sign in to https://plugins.qgis.org
2. Open your plugin page (or create a new plugin entry)
3. Fill metadata fields in the web form
4. Upload the ZIP package
5. Mark release channel (stable/experimental) appropriately

## 6. Review and Approval

After upload:

- The repository performs automatic checks
- You may receive validation warnings/errors
- Resolve issues and upload a new ZIP if needed

Common causes of rejection:

- missing or invalid metadata fields
- broken plugin import (`classFactory` issues)
- invalid version progression
- unsupported QGIS version range

## 7. Release Management

For each new release:

1. Update `version` in `metadata.txt`
2. Update changelog notes (recommended: add `changelog=` field)
3. Re-package ZIP
4. Upload new version on plugins.qgis.org

## 8. Suggested Release Checklist for This Repo

- Confirm runtime behavior of `qgis_plugin/scimap_algorithm.py` with current WhiteboxTools
- Confirm `qgis_plugin/scimap_provider.py` toolbar action opens algorithm dialog
- Confirm icon displays in provider, algorithm, and toolbar
- Confirm stream vector export contains `Risk` field values and CRS
- Confirm no temporary/debug files are included in ZIP

## 9. Optional: Automate Publishing with qgis-plugin-ci

You can automate package creation and upload in CI/CD.

Example:

```bash
pip install qgis-plugin-ci
qgis-plugin-ci package --allow-uncommitted-changes
qgis-plugin-ci publish --server https://plugins.qgis.org \
  --username <plugins-username> \
  --password <plugins-password-or-token>
```

Use a CI secret/token instead of plain credentials.
