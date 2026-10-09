# examples

Plans the model can build. Each file is a `name` and a `plan`: a list of items, each with a `kind` and its `slots`.

| File | Part | Plan items |
| --- | --- | --- |
| `rounded_block.json` | a block with rounded corners | 2 |
| `control_panel.json` | a machine control panel: screen recess, buttons, lamps, mounting holes | 15 |

```
uv run python -m forge.s1.third.probe --checkpoint forge-s1.pt --arguments model examples/control_panel.json
```
