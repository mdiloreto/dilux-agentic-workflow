# OpenSpec integration

DAW can optionally pair its ticket workflow with OpenSpec. This is an additive,
portable integration: DAW still supplies phases, gates, and the ticket-specific
execution plan; OpenSpec supplies the product specification baseline and the
proposed requirement change.

## Enable it

The target repository must have the OpenSpec CLI available (either as the
executable `node_modules/.bin/openspec` in that repository or on `PATH`) and a
product baseline at `openspec/specs/`. The repository-local executable takes
precedence, so an npm project can install it with `npm ci` without requiring a
global OpenSpec installation. Add these exact lines to its `AGENTS.md`:

```markdown
## Specification framework
Spec format: OpenSpec
```

Any other setting, missing heading, or `Spec format: DAW` leaves legacy DAW
behavior unchanged. Installation does not require OpenSpec.

## Artifact ownership

| Artifact | Owner | Meaning |
|---|---|---|
| `openspec/specs/` | OpenSpec | Current product baseline |
| `openspec/changes/<slug>/` | OpenSpec | One ticket's proposed change: proposal, design, tasks, and capability deltas |
| `docs/ddw/specs/spec-{ticket}.md` or `fix-{ticket}.md` | DAW | The ticket's executable implementation plan |

The DAW plan links exactly one change in its header:

```markdown
| OpenSpec change | openspec/changes/<slug> |
```

Do not treat a change delta as the current baseline or edit `openspec/specs/`
to pre-approve it. DAW does not merge changes or replace OpenSpec governance.

## How it fits the workflow

DEFINE still establishes the DAW PRD for the ticket's intent and acceptance
criteria; it does not replace the OpenSpec product baseline. In PLAN, read the
baseline, then create or update the linked proposed change and the DAW execution
plan. Keep product requirements in OpenSpec; the DAW plan records implementation
blocks, file-level work, PRD coverage, tests, and completion criteria.

PLAN may advance only when both validators pass:

```sh
openspec validate <slug> --strict
ddw-validate-spec docs/ddw/specs/spec-{ticket}.md
```

The existing DAW `spec` receipt is extended to include every file in the linked
change. Editing the DAW plan or any file under that change invalidates the
receipt and the `spec` gate. Return to PLAN, update the paired artifacts as
needed, and validate again before CODE. This is the same existing gate, not a
new workflow phase or a substitute for DAW validation.

## Direct implementation from an existing baseline

If the user explicitly opts in for a ticket, `OPENSPEC-DIRECT` skips DAW
DEFINE, PLAN and VERIFY. In CLASSIFY read all `openspec/specs/*/spec.md`,
identify which requirements are already implemented, and choose a bounded
outstanding slice. Advance with:

```sh
.ddw/scripts/transition.py --to CODE --direct-openspec --ticket <ID> \
  --title "<slice>" --autonomy autonomous --action "<reason>"
```
`autonomous` is optional and independent of the direct-spec tier.

The entry checks the exact `AGENTS.md` OpenSpec opt-in and the presence of a
readable baseline, **not** feature or spec validity. This mode does not require
a new `openspec/changes/` delta, a DAW PRD/plan, OpenSpec validation, or
spec-based module verification. CODE still owes test and SAST receipts before
CLOSEOUT; CLOSEOUT still owes a commit and PR. If the baseline cannot resolve
a requirement, stop and ask. No other tier changes its existing gates.

During a direct ticket's CODE and CLOSEOUT, agent writes to `openspec/specs/`
and its parent paths are refused. The baseline is input, not an artifact to
rewrite to fit the implementation. A requirements change needs a separate
decision and workflow. The direct entry binds the whole baseline tree's
paths and bytes; post-write validation stops the ticket if it later drifts,
including after a shell write. This does not attest baseline semantics or
validity.
