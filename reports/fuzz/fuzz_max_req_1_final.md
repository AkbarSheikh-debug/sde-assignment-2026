# Grade report — fuzz_max_req_1

## Outcomes vs baseline (no-op controller)

| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | engagement gap captured | hallucinated calls (base) |
|---|---|---|---|---|---|---|---|---|
| ks_cancel | 0.374 | +10.4% | 0.262 | +10.0% | 0.495 | -0.8% | 0% | 55 (60) |
| nw_kyc | 0.345 | -9.0% | 0.234 | -4.3% | 0.582 | -3.4% | 0% | 27 (13) |
| nw_renewal | 0.465 | +9.8% | 0.288 | +11.3% | 0.553 | -1.5% | -22% | 69 (208) |
| ALL | 0.398 | +6.0% | 0.266 | +7.7% | 0.532 | -1.8% | -5% | 151 (281) |

## Changes applied by the controller

7 applied — neutral: 3, beneficial: 3, harmful: 1

- ks_cancel v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.007 → **neutral**
- nw_renewal v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.014 → **neutral**
- ks_cancel v3 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.029 → **beneficial**
- ks_cancel v4 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.014 → **neutral**
- nw_renewal v3 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.045 → **beneficial**
- nw_renewal v4 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.022 → **beneficial**
- nw_renewal v6 (controller_approved) dims=['prompt_version'] engageΔ=-0.035 didΔ=+0.000 → **harmful**

## Governance

```
{
  "approval_requests": 1,
  "decisions": {
    "approved": 1,
    "expired": 0
  },
  "rejection_reasons": {},
  "applied_via_approval": 1,
  "auto_applied": 6,
  "auto_apply_rejected": 0,
  "apply_conflicts": 0,
  "lapsed": 0,
  "peak_requests_per_24h": {
    "nw_renewal": 1
  }
}
```

## Incidents

```
[
  {
    "type": "supervisor_edit",
    "workflow": "nw_renewal",
    "at": "D3 16:30",
    "mttr_minutes": 1065,
    "fixed_by": "controller_approved"
  }
]
```

## Decaying variants — share of in-population calls still on the variant, by day after onset

```
{}
```

Credits used: 9 · validation runs: 3 · LLM fix calls: 0