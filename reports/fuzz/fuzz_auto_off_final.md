# Grade report — fuzz_auto_off

## Outcomes vs baseline (no-op controller)

| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | engagement gap captured | hallucinated calls (base) |
|---|---|---|---|---|---|---|---|---|
| ks_cancel | 0.365 | +6.4% | 0.257 | +6.1% | 0.498 | +0.2% | 1% | 57 (55) |
| nw_kyc | 0.464 | +26.0% | 0.305 | +25.4% | 0.587 | -1.0% | -13% | 28 (16) |
| nw_renewal | 0.417 | +27.1% | 0.255 | +22.8% | 0.558 | +0.9% | 7% | 48 (22) |
| ALL | 0.403 | +17.4% | 0.266 | +15.3% | 0.538 | +0.7% | 3% | 133 (93) |

## Changes applied by the controller

21 applied — neutral: 13, beneficial: 8

- ks_cancel v2 (controller_approved) dims=['voice', 'caller_id'] engageΔ=-0.005 didΔ=+0.006 → **neutral**
- nw_renewal v2 (controller_approved) dims=['voice', 'caller_id'] engageΔ=+0.010 didΔ=-0.009 → **neutral**
- nw_kyc v2 (controller_approved) dims=['voice', 'caller_id'] engageΔ=-0.001 didΔ=+0.175 → **beneficial**
- nw_renewal v3 (controller_approved) dims=['welcome_message'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- nw_renewal v4 (controller_approved) dims=['voice'] engageΔ=-0.004 didΔ=+0.000 → **neutral**
- nw_kyc v3 (controller_approved) dims=['welcome_message'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- ks_cancel v3 (controller_approved) dims=['welcome_message'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- nw_renewal v5 (controller_approved) dims=['welcome_message'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- nw_renewal v6 (controller_approved) dims=['voice'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- ks_cancel v4 (controller_approved) dims=['voice', 'caller_id'] engageΔ=+0.000 didΔ=+0.042 → **beneficial**
- nw_kyc v4 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.086 → **beneficial**
- nw_renewal v7 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.134 → **beneficial**
- ks_cancel v5 (controller_approved) dims=['welcome_message'] engageΔ=+0.004 didΔ=+0.000 → **neutral**
- nw_renewal v8 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.208 → **beneficial**
- ks_cancel v6 (controller_approved) dims=['voice'] engageΔ=+0.003 didΔ=+0.000 → **neutral**
- nw_renewal v9 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.017 → **neutral**
- nw_renewal v10 (controller_approved) dims=['voice'] engageΔ=+0.014 didΔ=+0.000 → **beneficial**
- ks_cancel v7 (controller_approved) dims=['welcome_message'] engageΔ=+0.006 didΔ=+0.000 → **neutral**
- nw_renewal v11 (controller_approved) dims=['voice'] engageΔ=+0.016 didΔ=+0.000 → **beneficial**
- ks_cancel v8 (controller_approved) dims=['voice'] engageΔ=+0.005 didΔ=+0.000 → **neutral**
- nw_renewal v12 (controller_approved) dims=['voice'] engageΔ=+0.105 didΔ=+0.000 → **beneficial**

## Governance

```
{
  "approval_requests": 22,
  "decisions": {
    "approved": 21,
    "rejected": 1,
    "expired": 0
  },
  "rejection_reasons": {
    "approver declined (judgement call)": 1
  },
  "applied_via_approval": 21,
  "auto_applied": 0,
  "auto_apply_rejected": 0,
  "apply_conflicts": 0,
  "lapsed": 0,
  "peak_requests_per_24h": {
    "nw_kyc": 1,
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
    "workflow": "nw_renewal",
    "at": "D1 10:00",
    "flagged_did_share_by_day": {
      "1": 0.454,
      "2": 0.465,
      "3": 0.135,
      "4": 0.0,
      "5": 0.0,
      "6": 0.0,
      "7": 0.0
    }
  }
]
```

## Decaying variants — share of in-population calls still on the variant, by day after onset

```
{
  "nw_kyc:voice=V2": {
    "3": 0.046,
    "4": 0.052,
    "5": 0.044,
    "6": 0.048,
    "7": 0.051
  }
}
```

Credits used: 0 · validation runs: 0 · LLM fix calls: 0