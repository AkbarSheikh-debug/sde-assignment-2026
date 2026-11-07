# Grade report — fuzz_rand4

## Outcomes vs baseline (no-op controller)

| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | engagement gap captured | hallucinated calls (base) |
|---|---|---|---|---|---|---|---|---|
| ks_cancel | 0.374 | +12.1% | 0.264 | +10.5% | 0.493 | +0.1% | -14% | 57 (51) |
| nw_kyc | 0.333 | +66.1% | 0.216 | +58.3% | 0.602 | -0.5% | 1% | 25 (11) |
| nw_renewal | 0.451 | +9.6% | 0.280 | +7.5% | 0.549 | +1.2% | 7% | 58 (52) |
| ALL | 0.391 | +18.0% | 0.261 | +14.9% | 0.531 | +1.1% | 3% | 140 (114) |

## Changes applied by the controller

23 applied — neutral: 17, beneficial: 6

- ks_cancel v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.007 → **neutral**
- nw_renewal v2 (controller_auto) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.014 → **neutral**
- ks_cancel v3 (controller_approved) dims=['voice'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- nw_renewal v3 (controller_approved) dims=['voice'] engageΔ=+0.007 didΔ=+0.000 → **neutral**
- nw_renewal v4 (controller_approved) dims=['welcome_message'] engageΔ=+0.003 didΔ=+0.000 → **neutral**
- nw_renewal v5 (controller_approved) dims=['voice'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- nw_kyc v2 (controller_approved) dims=['voice', 'caller_id'] engageΔ=+0.000 didΔ=+0.704 → **beneficial**
- ks_cancel v4 (controller_approved) dims=['welcome_message', 'caller_id'] engageΔ=-0.001 didΔ=+0.042 → **beneficial**
- nw_renewal v6 (controller_approved) dims=['welcome_message'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- ks_cancel v5 (controller_approved) dims=['voice'] engageΔ=+0.001 didΔ=+0.000 → **neutral**
- nw_renewal v7 (controller_approved) dims=['voice', 'caller_id'] engageΔ=+0.000 didΔ=+0.060 → **beneficial**
- nw_kyc v3 (controller_approved) dims=['welcome_message'] engageΔ=+0.003 didΔ=+0.000 → **neutral**
- ks_cancel v6 (controller_approved) dims=['welcome_message'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- nw_renewal v8 (controller_approved) dims=['welcome_message'] engageΔ=+0.004 didΔ=+0.000 → **neutral**
- nw_kyc v4 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.261 → **beneficial**
- nw_renewal v9 (controller_approved) dims=['welcome_message'] engageΔ=+0.005 didΔ=+0.000 → **neutral**
- nw_kyc v5 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.239 → **beneficial**
- ks_cancel v8 (controller_approved) dims=['voice'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- nw_renewal v10 (controller_approved) dims=['voice'] engageΔ=+0.010 didΔ=+0.000 → **beneficial**
- ks_cancel v9 (controller_approved) dims=['voice'] engageΔ=+0.001 didΔ=+0.000 → **neutral**
- nw_renewal v11 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.007 didΔ=+0.000 → **neutral**
- ks_cancel v10 (controller_approved) dims=['voice'] engageΔ=+0.001 didΔ=+0.000 → **neutral**
- nw_renewal v12 (controller_approved) dims=['welcome_message'] engageΔ=+0.000 didΔ=+0.000 → **neutral**

## Governance

```
{
  "approval_requests": 23,
  "decisions": {
    "approved": 21,
    "rejected": 2,
    "expired": 0
  },
  "rejection_reasons": {
    "approver declined (judgement call)": 2
  },
  "applied_via_approval": 21,
  "auto_applied": 2,
  "auto_apply_rejected": 0,
  "apply_conflicts": 0,
  "lapsed": 0,
  "peak_requests_per_24h": {
    "nw_kyc": 2,
    "nw_renewal": 2,
    "ks_cancel": 1
  }
}
```

## Incidents

```
[
  {
    "type": "did_flagged",
    "workflow": "nw_kyc",
    "at": "D1 10:00",
    "flagged_did_share_by_day": {
      "1": 1.0,
      "2": 0.864,
      "3": 0.739,
      "4": 0.611,
      "5": 0.505,
      "6": 0.294,
      "7": 0.09
    }
  },
  {
    "type": "supervisor_edit",
    "workflow": "ks_cancel",
    "at": "D3 15:00"
  }
]
```

## Decaying variants — share of in-population calls still on the variant, by day after onset

```
{
  "ks_cancel:welcome_message=W2": {
    "3": 0.048,
    "4": 0.05,
    "5": 0.05,
    "6": 0.051,
    "7": 0.051
  },
  "ks_cancel:voice=V3": {
    "4": 1.0,
    "5": 0.967,
    "6": 0.902,
    "7": 0.879
  },
  "nw_kyc:welcome_message=W2": {
    "5": 0.054,
    "6": 0.039,
    "7": 0.046
  }
}
```

Credits used: 0 · validation runs: 0 · LLM fix calls: 0