# Report Image Mapping

This project maps optimizer final-template XMLs to report images through
`src/ta_foundation/web/optimizer_image_lookup.py`.

## When Reports Need The Mapping

Any report section that displays strategy portraits, card images, or generated
detail charts must run the enriched package path:

- `build_session_candidate_report(..., enrich_packages=True)`
- or the per-candidate report builder, which enriches by default.

Reports that need portraits/cards but not generated detail charts should also
pass `enrich_detail_charts=False`. This keeps image mapping enabled without
generating the heavier `Analysis.csv` and settings-table chart images.

The enriched path attaches these fields to `pkg.metadata["derived"]`:

- `display_name` and `display_name_spaced`
- `template_path`
- `run_image_uri` and `run_image_path`
- `background_image_uri` and `background_image_path`
- `analysis_image_uri`
- `summery_image_uri`

The weekly report pack uses enrichment for:

- `Executive-Strategy-Profiles.html`: portraits plus generated detail charts.
- `Weekly-Prop-Dashboard.html`: portraits/card images only.
- `Weekly-Leaderboard-Cards.html`: portraits/card images only. This mirrors
  `docs/reports_documentation/configs/07_weekly_leaderboard.yaml` and the
  historical output `outputs3_19/weekly_leaderboard_cards_3_14.html`.

Pure data reports can stay lightweight and use semantic names only.

## Portrait Lookup Order

The lookup decodes each XML with the external `template_naming` package, then
searches configured image directories in priority order.

Default directories:

- `%USERPROFILE%\Pictures\NewGodImages`
- `%USERPROFILE%\Pictures\God images`

For each directory, the lookup tries:

1. Exact stem: `<Phase><MA><Descriptor><Direction>-<Market>`
2. Exact stem: `<Phase><MA><Descriptor><Direction>`
3. Exact stem: `<Phase><MA><Descriptor>`
4. Exact stem: `<MA><Descriptor>`
5. Exact stem: `<MA>`
6. Semantic substring: `<MA><Descriptor>`
7. Per-god pool: `<MA>_images\*`
8. MA substring: `<MA>`
9. Exact stem: `Default`

Supported image extensions are `.png`, `.jpg`, and `.jpeg`.
`*_Background.*` files are skipped as portrait candidates, but a matching
`*_Background.*` companion can be used as the executive-card background.

## Detail Charts

Executive cards also include generated charts when enrichment is enabled:

- `analysis_image_uri` is generated from each candidate `Analysis.csv`.
- `summery_image_uri` is generated from each candidate settings table.

The `run_executive_profile_cards` section must keep
`show_detail_charts: true` to display these images.
