# 3D Baseball Research Asset

This folder preserves a reproducible reference to a Three.js baseball reconstruction that may be useful for future pitch-spin / seam-orientation work in this project.

## Upstream source

- Repository: `Vac1911/spinrate-visual`
- Upstream branch: `master`
- Upstream project: https://github.com/Vac1911/spinrate-visual
- Primary scene file: `app.json`
- `app.json` blob SHA observed during research: `e69c19e7538c0c79a8b5e3e47b7a82e2dae5b87e`
- Observed `app.json` size: `337329` bytes

The upstream README says the scene was largely generated with the Three.js editor and was made to reproduce the style of Baseball Savant's rotating baseball visualization. The scene includes a sphere, baseball texture, material, lights/camera, and a rotation script. The texture is embedded in `app.json` as a base64 JPEG.

Observed model structure:

- `SphereBufferGeometry`
- radius: `1`
- width segments: `32`
- height segments: `16`
- `MeshStandardMaterial`
- embedded baseball/seam texture
- rotation script that can rotate by Euler components or an arbitrary spin-axis vector

## Why the third-party files are not vendored here

No explicit license was found at the upstream repository root during this research pass. To keep provenance and licensing boundaries clear, this repository stores metadata and a deterministic fetch helper instead of copying the upstream texture / scene payload directly.

Run `python research_assets/3d_baseball/fetch_upstream.py` to download the exact upstream files into `research_assets/3d_baseball/upstream/` when local experimentation is needed. The helper verifies the expected `app.json` byte size and Git blob SHA.

## Potential project uses

- render an actual baseball surface while testing three-dimensional spin-axis logic;
- visualize seam orientation if pitch-level orientation data becomes retrievable;
- convert the scene to another Three.js / glTF workflow after checking licensing requirements;
- validate coordinate-system transforms independently from the production analysis pipeline.

This folder is a research asset only. It does not change the current Statcast ingestion or analysis schema.