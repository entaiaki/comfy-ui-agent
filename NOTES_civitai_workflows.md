# Civitai / downloaded workflow notes

Many downloaded ComfyUI workflows are **UI workflows**, not API workflows.

## How to tell

- **UI workflow** usually has top-level keys like:
  - `id`, `revision`, `last_node_id`, `last_link_id`
  - `nodes`: list
  - `links`: list

Example: `Anima_Cuple_MultipleCharaLoRA.json` in your `my_workflows/`.

- **API workflow** is usually a dict mapping node_id -> node object:
  - `{ "1": {"class_type": ..., "inputs": {...}}, "2": ... }`
  - or wrapped `{ "prompt": { ... } }`

Example: `flux_kontext_txt2img_api_workflow.json`.

## This bridge

- Bridge can run **API workflows** directly (POST /prompt).
- For UI workflows, you typically need to convert/export an API workflow.

## Added inspection endpoint

GET `/workflow/inspect?workflow=...` returns a compact summary for LLM planning.

This supports both UI and API formats.

## Next step (planned)

Add a conversion path for UI workflows.
Options:
1) Use ComfyUI frontend/export to API workflow (preferred if available).
2) Implement a local converter (harder, depends on node defs/extensions).
